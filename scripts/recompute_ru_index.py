"""Replay persisted RRI v1 inputs through v1.1 before starting the live worker.

Run with ``python -m scripts.recompute_ru_index [--dry-run]``. The backup is
committed before any updates; each country is then updated atomically. A
session advisory lock covers backup, replay and cache refresh across commits.
"""
import argparse
from collections import deque
import json
import logging
from datetime import timedelta

from sqlalchemy import text

from src.countries import COUNTRIES
from src.db import engine as default_engine
from src.engine.ru_index import (
    INDEX_VERSION, LOW_DATA_CONFIDENCE, deltas_from_history,
    gdelt_days_with_volume, level_for, media_evidence, score_components, warm_cache,
)

logger = logging.getLogger(__name__)
RECOMPUTE_LOCK = 7273491101
BATCH_SIZE = 1000


def _replay_row(row, country, gdelt, history):
    details = dict(row.details or {})
    media_detail = dict(details.get("media") or {})
    source = media_detail.get("source", "none")
    evidence = media_evidence(
        source,
        articles=row.article_count if source == "temperature" else None,
        days_with_volume=gdelt_days_with_volume(gdelt, row.time) if source == "gdelt" else None,
    )
    media_detail.update(evidence)
    score, weights, rule = score_components(
        country, float(row.structural),
        float(row.media) if row.media is not None else None,
        float(row.boost or 0), evidence["confidence"],
    )
    delta_24h, delta_7d = deltas_from_history(row.time, score, history)
    details.update(media=media_detail, weights=weights,
                   low_data=row.media is not None and evidence["confidence"] < LOW_DATA_CONFIDENCE)
    details.pop("bound_rule", None)
    if rule:
        details["bound_rule"] = rule
    return {"time": row.time, "cc": row.country_code, "score": score,
            "level": level_for(score), "delta_24h": delta_24h, "delta_7d": delta_7d,
            "version": INDEX_VERSION, "details": json.dumps(details, ensure_ascii=False)}


UPDATE = text("""
    UPDATE ru_index SET score = :score, level = :level, delta_24h = :delta_24h,
        delta_7d = :delta_7d, version = :version, details = CAST(:details AS jsonb)
    WHERE country_code = :cc AND time = :time AND version = 'v1'
""")


def recompute_ru_index(*, dry_run=False, engine=None, refresh_cache=warm_cache):
    """Return an audit report, or None if another replay owns the lock.

    Dry runs take the same lock but do not create a backup, update rows or touch
    Redis. Countries outside the registry are skipped and keep their v1 rows.
    """
    engine = engine or default_engine
    with engine.connect() as lock:
        acquired = lock.execute(text("SELECT pg_try_advisory_lock(:key)"),
                                {"key": RECOMPUTE_LOCK}).scalar_one()
        lock.commit()
        if not acquired:
            return None
        try:
            report = {"dry_run": dry_run, "rows": 0, "countries": 0,
                      "countries_changing_level": 0, "largest_current_changes": []}
            with engine.begin() as connection:
                codes = connection.execute(text("""
                    SELECT DISTINCT country_code FROM ru_index WHERE version = 'v1'
                    ORDER BY country_code
                """)).scalars().all()
                if not dry_run and codes:
                    connection.execute(text("""
                        CREATE TABLE IF NOT EXISTS ru_index_v1_backup AS
                        SELECT * FROM ru_index WHERE version = 'v1'
                    """))

            changes = []
            for code in codes:
                country = COUNTRIES.get(code)
                if country is None:
                    # The API never shows rows outside the registry; leave them as v1.
                    logger.warning("RRI replay skips %s: not in the country registry", code)
                    continue
                with engine.begin() as connection:
                    rows = connection.execute(text("""
                        SELECT * FROM ru_index WHERE country_code = :cc
                          AND version IN ('v1', :version) ORDER BY time
                    """), {"cc": code, "version": INDEX_VERSION}).fetchall()
                    # Load the country's GDELT data only once, never use future days.
                    gdelt = connection.execute(text("""
                        SELECT day, tone_avg, volume FROM gdelt_daily
                        WHERE country_code = :cc ORDER BY day
                    """), {"cc": code}).fetchall()
                    history = deque()
                    batch = []
                    latest_change = None
                    for row in rows:
                        while history and history[0][0] <= row.time - timedelta(days=8):
                            history.popleft()
                        if row.version != 'v1':
                            history.append((row.time, float(row.score)))
                            latest_change = None
                            continue
                        update = _replay_row(row, country, gdelt, history)
                        history.append((row.time, update["score"]))
                        batch.append(update)
                        report["rows"] += 1
                        latest_change = {
                            "country_code": code, "old_score": float(row.score),
                            "new_score": update["score"],
                            "change": round(update["score"] - float(row.score), 2),
                            "old_level": row.level, "new_level": update["level"],
                        }
                        if len(batch) >= BATCH_SIZE:
                            if not dry_run:
                                connection.execute(UPDATE, batch)
                            batch = []
                    if batch and not dry_run:
                        connection.execute(UPDATE, batch)
                    if latest_change:
                        changes.append(latest_change)
                report["countries"] += 1

            report["countries_changing_level"] = sum(
                c["old_level"] != c["new_level"] for c in changes)
            report["largest_current_changes"] = sorted(
                changes, key=lambda c: abs(c["change"]), reverse=True)[:10]
            if not dry_run:
                # Also refresh on a no-op retry after a crash following the last
                # country commit, so the old cached values cannot survive replay.
                try:
                    from src.queue import get_redis
                    get_redis().delete("cache:v2:countries")
                except Exception as exc:
                    logger.warning("Cannot invalidate RRI cache: %s", exc)
                refresh_cache()
            return report
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": RECOMPUTE_LOCK})
            lock.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    report = recompute_ru_index(dry_run=args.dry_run)
    if report is None:
        parser.exit(1, "Another RRI replay is running; retry after it finishes.\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
