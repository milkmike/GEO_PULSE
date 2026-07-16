#!/usr/bin/env python3
"""Read-only, bounded audit for the global story recovery pipeline."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Any

from sqlalchemy import text

from src.db import SessionLocal, wait_for_db
from src.stories import (
    _scope_candidate_articles,
    cluster_story_candidates,
    derive_reactivation_pairs,
    fetch_story_candidates,
    merge_rejection_reasons,
    score_story_match,
)


PAIR_REASON_KEYS = (
    "pairs_total",
    "pairs_scored",
    "accepted",
    "same_thread",
    "score_below_threshold",
    "insufficient_independent_features",
    "time_window_exceeded",
    "reactivation_requires_event_and_entity",
)


def _value(row: Any, name: str, default: Any = None) -> Any:
    if row is None:
        return default
    if hasattr(row, name):
        return getattr(row, name)
    mapping = getattr(row, "_mapping", row if isinstance(row, dict) else {})
    return mapping.get(name, default)


def _ratio(covered: int, total: int) -> float:
    return round(covered / total, 6) if total else 0.0


def run_audit(
    session: Any,
    *,
    recent_days: int = 30,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the production clustering proposal and diagnostics without writes."""

    if recent_days < 1:
        raise ValueError("recent_days must be positive")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    scope_start = now - timedelta(days=recent_days)

    candidates = []
    for candidate in fetch_story_candidates(
        session,
        published_after=scope_start,
    ):
        scoped = _scope_candidate_articles(
            candidate,
            published_after=scope_start,
        )
        if scoped is not None:
            candidates.append(scoped)

    article_ids = sorted({
        article_id
        for candidate in candidates
        for article_id in candidate.article_ids
    })
    countries = sorted({candidate.country_code for candidate in candidates})
    threads = sorted({candidate.thread_id for candidate in candidates})

    article_entities: dict[int, set[str]] = {article_id: set() for article_id in article_ids}
    for candidate in candidates:
        for article in candidate.articles:
            article_entities.setdefault(article.article_id, set()).update(article.entity_ids)
    articles_with_entities = sum(bool(entity_ids) for entity_ids in article_entities.values())
    candidates_with_entities = sum(bool(candidate.entities) for candidate in candidates)

    embedded_articles = 0
    if article_ids:
        embedding_row = session.execute(text("""
            /* audit_embedding_coverage */
            SELECT COUNT(DISTINCT an.article_id) FILTER (
                       WHERE an.embedding IS NOT NULL
                   ) AS embedded_articles
            FROM analysis an
            WHERE an.article_id = ANY(:article_ids)
        """), {"article_ids": article_ids}).fetchone()
        embedded_articles = int(
            _value(embedding_row, "embedded_articles", 0) or 0
        )

    mismatch_rows = session.execute(text("""
        /* audit_country_mismatches */
        SELECT t.id AS thread_id, ar.id AS article_id,
               TRIM(t.country_code) AS thread_country,
               TRIM(s.country_code) AS article_country
        FROM threads t
        JOIN thread_articles ta ON ta.thread_id = t.id
        JOIN articles ar ON ar.id = ta.article_id
        JOIN article_country_facts s ON s.article_id = ar.id
        WHERE ar.published_at >= :scope_start
          AND TRIM(COALESCE(t.country_code, ''))
              <> TRIM(COALESCE(s.country_code, ''))
        ORDER BY t.id, ar.id, s.country_code
    """), {"scope_start": scope_start}).fetchall()
    country_mismatches = [{
        "thread_id": int(_value(row, "thread_id")),
        "article_id": int(_value(row, "article_id")),
        "thread_country": str(_value(row, "thread_country", "")),
        "article_country": str(_value(row, "article_country", "")),
    } for row in mismatch_rows]

    candidates_by_thread: dict[int, list[Any]] = {}
    for candidate in candidates:
        candidates_by_thread.setdefault(candidate.thread_id, []).append(candidate)
    same_thread_defects = [{
        "thread_id": thread_id,
        "candidate_count": len(items),
        "countries": sorted(item.country_code for item in items),
    } for thread_id, items in sorted(candidates_by_thread.items()) if len(items) > 1]

    reactivation_pairs = derive_reactivation_pairs(session, candidates)
    reason_counts = Counter({key: 0 for key in PAIR_REASON_KEYS})
    for left, right in combinations(candidates, 2):
        reason_counts["pairs_total"] += 1
        if left.thread_id == right.thread_id:
            reason_counts["same_thread"] += 1
            continue
        pair = tuple(sorted((left.thread_id, right.thread_id)))
        similarity = score_story_match(left, right)
        reason_counts["pairs_scored"] += 1
        reasons = merge_rejection_reasons(
            similarity,
            explicit_reactivation=pair in reactivation_pairs,
        )
        if not reasons:
            reason_counts["accepted"] += 1
        for reason in reasons:
            reason_counts[reason] += 1

    clusters = cluster_story_candidates(
        candidates,
        reactivation_pairs=reactivation_pairs,
    )
    proposed_clusters = [{
        "thread_ids": sorted(item.thread_id for item in cluster),
        "countries": sorted({item.country_code for item in cluster}),
        "article_ids": sorted({
            article_id
            for item in cluster
            for article_id in item.article_ids
        }),
    } for cluster in clusters]

    return {
        "candidate_totals": {
            "scope_days": recent_days,
            "candidates": len(candidates),
            "threads": len(threads),
            "countries": len(countries),
            "articles": len(article_ids),
        },
        "canonical_entity_coverage": {
            "candidates_total": len(candidates),
            "candidates_with_entities": candidates_with_entities,
            "candidate_coverage_ratio": _ratio(
                candidates_with_entities, len(candidates)
            ),
            "articles_total": len(article_ids),
            "articles_with_entities": articles_with_entities,
            "article_coverage_ratio": _ratio(
                articles_with_entities, len(article_ids)
            ),
        },
        "embedding_coverage": {
            "articles_total": len(article_ids),
            "articles_with_embeddings": embedded_articles,
            "coverage_ratio": _ratio(embedded_articles, len(article_ids)),
        },
        "pair_rejection_reasons": {
            key: int(reason_counts[key]) for key in PAIR_REASON_KEYS
        },
        "proposed_clusters": proposed_clusters,
        "same_thread_defects": same_thread_defects,
        "country_mismatches": country_mismatches,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit the bounded global story pipeline without writes"
    )
    parser.add_argument("--recent-days", type=int, default=30)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    wait_for_db()
    session = SessionLocal()
    try:
        report = run_audit(session, recent_days=args.recent_days)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    finally:
        session.rollback()
        session.close()


if __name__ == "__main__":
    main()
