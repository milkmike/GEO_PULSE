"""Reanalyze articles without prompt_version (not yet analyzed by v1.3)."""
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

logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "anthropic/claude-sonnet-4"
BATCH_SIZE = 20
MAX_WORKERS = 5


def analyze_one(article_id, title, body, source_name, country_code):
    country_name = COUNTRY_NAMES.get(country_code, country_code)
    prompt = SENTIMENT_PROMPT.format(
        country=country_name, source=source_name,
        title=title[:200], body=body[:3000],
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
        sentiment = float(result.get("sentiment", 0)) if is_relevant else None
        action_level = int(result.get("action_level", 1)) if is_relevant else 1
        action_level = max(1, min(6, action_level))
        if sentiment is not None:
            sentiment = max(-3, min(3, sentiment))
        confidence = float(result.get("confidence", 0.5))
        event_type = result.get("event_type", "diplomatic")

        return {
            "article_id": article_id,
            "is_relevant": is_relevant,
            "sentiment": sentiment,
            "action_level": action_level,
            "confidence": confidence,
            "event_type": event_type,
        }
    except Exception as e:
        logger.error(f"Error analyzing {article_id}: {e}")
        return None


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [reanalyze] %(levelname)s: %(message)s")

    with get_session() as session:
        rows = session.execute(text("""
            SELECT an.id, an.article_id, a.title, a.body,
                   s.name as source_name, s.country_code
            FROM analysis an
            JOIN articles a ON an.article_id = a.id
            JOIN sources s ON a.source_id = s.id
            WHERE (an.prompt_version IS NULL OR an.prompt_version = '')
              AND a.is_duplicate = FALSE
            ORDER BY an.id
        """)).fetchall()

    total = len(rows)
    logger.info(f"Reanalyzing {total} remaining articles with prompt {PROMPT_VERSION}")

    updated = 0
    relevant = 0

    for batch_start in range(0, total, BATCH_SIZE):
        batch = rows[batch_start:batch_start + BATCH_SIZE]

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(analyze_one, r.article_id, r.title, r.body or "", r.source_name, r.country_code): r for r in batch}

            for future in as_completed(futures):
                result = future.result()
                if not result:
                    continue

                with get_session() as session:
                    session.execute(text("""
                        UPDATE analysis SET
                            is_relevant = :rel, sentiment = :sent,
                            action_level = :al, sentiment_confidence = :conf,
                            event_type = :et, prompt_version = :pv
                        WHERE article_id = :aid
                    """), {
                        "rel": result["is_relevant"],
                        "sent": result["sentiment"],
                        "al": result["action_level"],
                        "conf": result["confidence"],
                        "et": result["event_type"],
                        "pv": PROMPT_VERSION,
                        "aid": result["article_id"],
                    })
                    session.commit()
                    updated += 1
                    if result["is_relevant"]:
                        relevant += 1

        logger.info(f"Progress: {min(batch_start + BATCH_SIZE, total)}/{total} "
                     f"(relevant: {relevant}/{updated})")
        time.sleep(0.5)

    logger.info(f"Done! Updated: {updated}, Relevant: {relevant}")


if __name__ == "__main__":
    main()
