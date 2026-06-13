"""Reanalyze articles for specific countries with new prompt."""
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from sqlalchemy import text

from src.db import get_session
from src.config import COUNTRY_NAMES, HEAVY_MODEL
from src.pipeline.prompts import SENTIMENT_PROMPT, PROMPT_VERSION

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = HEAVY_MODEL
BATCH_SIZE = 10
MAX_WORKERS = 3


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
                "HTTP-Referer": "https://cis-thermometer.app",
                "X-Title": "CIS Thermometer Reanalyze",
            },
            json={"model": MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 300},
            timeout=60.0,
        )
        resp.raise_for_status()
        text_resp = resp.json()["choices"][0]["message"]["content"].strip()
        if text_resp.startswith("```"):
            text_resp = text_resp.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(text_resp)
        return {
            "article_id": article_id,
            "is_relevant": bool(result.get("is_relevant", False)),
            "sentiment": max(-3.0, min(3.0, float(result.get("sentiment", 0)))),
            "action_level": max(1, min(6, int(result.get("action_level", 1)))),
            "confidence": max(0.0, min(1.0, float(result.get("confidence", 0.5)))),
            "event_type": result.get("event_type", "diplomatic"),
            "event_key": result.get("event_key", ""),
            "reasoning": result.get("reasoning", ""),
        }
    except Exception as e:
        logger.error(f"Error analyzing {article_id}: {e}")
        return None


def main():
    countries = sys.argv[1:] if len(sys.argv) > 1 else ["AM", "MD", "GE"]
    logger.info(f"Reanalyzing countries: {countries} with prompt {PROMPT_VERSION}")

    with get_session() as session:
        placeholders = ",".join(f"'{c}'" for c in countries)
        rows = session.execute(text(f"""
            SELECT an.article_id, a.title, a.body, s.name as source_name, s.country_code,
                   an.is_relevant as old_relevant, an.sentiment as old_sentiment, an.action_level as old_al
            FROM analysis an
            JOIN articles a ON an.article_id = a.id
            JOIN sources s ON a.source_id = s.id
            WHERE s.country_code IN ({placeholders})
              AND a.is_duplicate = FALSE
            ORDER BY a.published_at DESC
        """)).fetchall()

    total = len(rows)
    logger.info(f"Found {total} articles to reanalyze")

    updated = 0
    new_relevant = 0
    al_upgraded = 0

    for batch_start in range(0, total, BATCH_SIZE):
        batch = rows[batch_start:batch_start + BATCH_SIZE]

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {}
            for row in batch:
                f = executor.submit(analyze_one, row.article_id, row.title, row.body or "", row.source_name, row.country_code)
                futures[f] = row

            for future in as_completed(futures):
                row = futures[future]
                result = future.result()
                if not result:
                    continue

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
                            raw_response = raw_response || CAST(:extra AS jsonb)
                        WHERE article_id = :aid
                    """), {
                        "rel": result["is_relevant"],
                        "sent": result["sentiment"],
                        "al": result["action_level"],
                        "conf": result["confidence"],
                        "et": result["event_type"],
                        "ek": result["event_key"],
                        "pv": PROMPT_VERSION,
                        "extra": json.dumps({"event_key": result["event_key"], "reasoning_v17": result["reasoning"]}),
                        "aid": result["article_id"],
                    })
                    session.commit()

                    # Track changes
                    if result["is_relevant"] and not row.old_relevant:
                        new_relevant += 1
                        logger.info(f"  NEW RELEVANT [{row.country_code}]: {row.title[:70]}")
                    if result["action_level"] > (row.old_al or 1):
                        al_upgraded += 1
                        logger.info(f"  AL UPGRADE [{row.country_code}] {row.old_al}->{result['action_level']}: {row.title[:60]}")
                    updated += 1

        logger.info(f"Progress: {min(batch_start + BATCH_SIZE, total)}/{total} | "
                     f"new_relevant: +{new_relevant} | al_upgraded: +{al_upgraded}")
        time.sleep(1)

    logger.info(f"DONE! Updated: {updated}, New relevant: +{new_relevant}, AL upgraded: +{al_upgraded}")


if __name__ == "__main__":
    main()
