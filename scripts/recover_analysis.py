"""Repair explicitly selected failed/keyword-only analyses; dry-run by default.

Example: python -m scripts.recover_analysis --article-ids 123 456
Paid apply reserves $0.10 before each attempt, with a run budget <= $5.
No embeddings, background worker restarts, or global history deletion.
"""
import argparse
import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import select, text

from scripts import analyze
from src.budgeted_chat import BudgetedChat
from src.config import OPENROUTER_API_KEY
from src.db import Analysis, get_session
from src.knowledge import upsert_analysis_mentions

MAX_ARTICLES = 25


def retryable(analysis) -> bool:
    return analysis.sentiment is None and (
        analysis.model_used == "keyword_filter"
        or (not analysis.model_used and analysis.is_relevant is False)
    )


def check_budget(max_usd: Decimal) -> dict:
    if not max_usd.is_finite() or not Decimal("0") < max_usd <= Decimal("5"):
        raise ValueError("Budget must be positive and at most $5")
    response = httpx.get("https://openrouter.ai/api/v1/key", timeout=10,
                        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"})
    response.raise_for_status()
    data = response.json()["data"]
    try:
        if data.get("limit_remaining") is None and data.get("limit") is None:
            # Uncapped account: the local reservation and request price ceilings
            # still bound this command, independently of unrelated workers.
            return {"remaining_usd": None, "usage_usd": data.get("usage")}
        remaining = Decimal(str(data.get("limit_remaining")))
    except InvalidOperation as exc:
        raise ValueError("Invalid remaining key allowance") from exc
    if not remaining.is_finite() or remaining <= 0:
        raise ValueError("The project key has no remaining allowance")
    return {"remaining_usd": str(remaining), "usage_usd": data.get("usage")}


def repair_row(row, *, analyze_article=None) -> str:
    # Release the read transaction before a potentially slow provider call.
    result = (analyze_article or analyze._analyze_one)(row)
    if result is None:
        return "provider_failed"
    with get_session() as session:
        current = session.execute(select(Analysis).where(
            Analysis.id == row.analysis_id, Analysis.article_id == row.id,
        ).with_for_update()).scalar_one_or_none()
        if current is None or not retryable(current):
            return "changed_concurrently"
        # A new keyword rejection must not inherit metadata from the old stub.
        for key in ("sentiment", "sentiment_confidence", "event_type", "event_key",
                    "action_level", "model_used", "prompt_version", "raw_response", "entities", "topics"):
            setattr(current, key, None)
        for key, value in result.items():
            setattr(current, key, value)
        current.analyzed_at = datetime.now(timezone.utc)
        session.flush()
        upsert_analysis_mentions(session, current.id, current.article_id, current.entities)
    return "repaired"


def recover(article_ids: list[int], *, apply: bool = False,
            budget: Decimal = Decimal("5")) -> dict:
    ids = sorted(set(article_ids))
    if not ids or len(ids) > MAX_ARTICLES or any(i <= 0 for i in ids):
        raise ValueError(f"Select 1 to {MAX_ARTICLES} positive article IDs")
    with get_session() as session:
        rows = session.execute(text("""
            SELECT ar.id, ar.title, ar.body, an.id AS analysis_id,
                   source.name AS source_name, source.country_code
            FROM articles ar
            JOIN article_country_facts source ON source.article_id = ar.id
            JOIN analysis an ON an.article_id = ar.id
            WHERE ar.id = ANY(CAST(:ids AS integer[])) AND ar.is_duplicate = FALSE
              AND an.sentiment IS NULL
              AND (an.model_used = 'keyword_filter'
                   OR (COALESCE(an.model_used, '') = '' AND an.is_relevant = FALSE))
            ORDER BY ar.id
        """), {"ids": ids}).fetchall()
    report = {"apply": apply, "eligible_ids": [r.id for r in rows], "results": []}
    if not apply or not rows:
        return report
    # One model, no fallback spending, no legacy embedding generation.
    client = BudgetedChat(OPENROUTER_API_KEY, budget)
    def analyze_article(row):
        return analyze._analyze_one(row, sentiment_fn=lambda **kwargs:
            analyze.analyze_sentiment(**kwargs, chat_fn=client.chat))
    for row in rows:
        report["budget"] = check_budget(budget)
        status = repair_row(row, analyze_article=analyze_article)
        report["results"].append({"article_id": row.id, "status": status})
        if status == "provider_failed":
            break
    report.update(reserved_usd=str(client.reserved), cost_usd=str(client.actual_cost),
                  requests=client.requests)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--article-ids", type=int, nargs="+", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-usd", type=Decimal, default=Decimal("5"))
    args = parser.parse_args()
    print(json.dumps(recover(args.article_ids, apply=args.apply, budget=args.max_usd)))


if __name__ == "__main__":
    main()
