"""Reanalyze ALL articles with prompt v1.8 — fix event_keys for thread clustering."""
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from sqlalchemy import text

from src.db import get_session
from src.config import COUNTRY_NAMES
from src.pipeline.prompts import SENTIMENT_PROMPT, PROMPT_VERSION
from src.pipeline.sentiment import validate_event_key

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "google/gemini-3-flash-preview"  # Same as analyzer — cheap & fast
BATCH_SIZE = 20
MAX_WORKERS = 5
SLEEP_BETWEEN_BATCHES = 0.3


def analyze_one(article_id, title, body, source_name, country_code):
    """Reanalyze one article with v1.8 prompt."""
    country_name = COUNTRY_NAMES.get(country_code, country_code)
    prompt = SENTIMENT_PROMPT.format(
        country=country_name, source=source_name,
        title=title[:200], body=(body or "")[:3000],
    )

    try:
        resp = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 300,
                "temperature": 0,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()

        if content.startswith("```"):
            content = content.split("\n", 1)[1] if "\n" in content else content[3:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        result = json.loads(content)

        is_relevant = result.get("is_relevant", False)
        if isinstance(is_relevant, str):
            is_relevant = is_relevant.lower() not in ("false", "0", "no")

        sentiment = float(result.get("sentiment", 0)) if is_relevant else None
        action_level = int(result.get("action_level", 1)) if is_relevant else 1
        action_level = max(1, min(6, action_level))
        if sentiment is not None:
            sentiment = max(-3, min(3, sentiment))
        confidence = float(result.get("confidence", 0.5))
        event_type = result.get("event_type", "diplomatic")
        if event_type not in ("diplomatic", "military", "economic", "cultural", "security"):
            event_type = "diplomatic"
        reasoning = result.get("reasoning", "")

        # Validate event_key with new logic
        raw_key = result.get("event_key", "")
        event_key = validate_event_key(raw_key, title, is_relevant)

        return {
            "article_id": article_id,
            "is_relevant": is_relevant,
            "sentiment": sentiment,
            "action_level": action_level,
            "confidence": confidence,
            "event_type": event_type,
            "event_key": event_key,
            "reasoning": reasoning,
            "raw_response": result,
        }
    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error for article {article_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error analyzing {article_id}: {e}")
        return None


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [reanalyze-keys] %(levelname)s: %(message)s")

    # Get all articles that have analysis
    with get_session() as session:
        rows = session.execute(text("""
            SELECT an.id as analysis_id, an.article_id, a.title, a.body,
                   s.name as source_name, s.country_code,
                   an.event_key as old_event_key, an.sentiment as old_sentiment
            FROM analysis an
            JOIN articles a ON an.article_id = a.id
            JOIN sources s ON a.source_id = s.id
            WHERE a.is_duplicate = FALSE
            ORDER BY an.id
        """)).fetchall()

    total = len(rows)
    logger.info(f"Reanalyzing {total} articles with prompt {PROMPT_VERSION} (model: {MODEL})")

    updated = 0
    flipped_irrelevant = 0
    key_changed = 0
    errors = 0

    for batch_start in range(0, total, BATCH_SIZE):
        batch = rows[batch_start:batch_start + BATCH_SIZE]

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {}
            for row in batch:
                f = executor.submit(
                    analyze_one,
                    row.article_id, row.title, row.body or "",
                    row.source_name, row.country_code,
                )
                futures[f] = row

            for future in as_completed(futures):
                row = futures[future]
                result = future.result()
                if not result:
                    errors += 1
                    continue

                try:
                    with get_session() as session:
                        session.execute(text("""
                            UPDATE analysis SET
                                is_relevant = :rel,
                                sentiment = :sent,
                                action_level = :al,
                                sentiment_confidence = :conf,
                                event_type = :et,
                                event_key = :ek,
                                prompt_version = :pv,
                                raw_response = :raw
                            WHERE article_id = :aid
                        """), {
                            "rel": result["is_relevant"],
                            "sent": result["sentiment"],
                            "al": result["action_level"],
                            "conf": result["confidence"],
                            "et": result["event_type"],
                            "ek": result["event_key"],
                            "pv": PROMPT_VERSION,
                            "raw": json.dumps(result["raw_response"], ensure_ascii=False),
                            "aid": result["article_id"],
                        })
                        session.commit()

                        # Track changes
                        old_key = (row.old_event_key or "").strip().lower()
                        new_key = (result["event_key"] or "").strip().lower()
                        if old_key != new_key:
                            key_changed += 1

                        if not result["is_relevant"]:
                            flipped_irrelevant += 1

                        updated += 1
                except Exception as e:
                    logger.error(f"DB update error for article {result['article_id']}: {e}")
                    errors += 1

        progress = min(batch_start + BATCH_SIZE, total)
        logger.info(
            f"Progress: {progress}/{total} ({progress*100//total}%) | "
            f"updated: {updated} | key_changed: {key_changed} | "
            f"irrelevant: {flipped_irrelevant} | errors: {errors}"
        )
        time.sleep(SLEEP_BETWEEN_BATCHES)

    logger.info(
        f"═══ DONE ═══ "
        f"Updated: {updated}/{total} | "
        f"Event key changed: {key_changed} | "
        f"Flipped irrelevant: {flipped_irrelevant} | "
        f"Errors: {errors}"
    )
    logger.info("Now run build_threads.py to rebuild clusters with new event_keys")


if __name__ == "__main__":
    main()
