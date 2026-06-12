"""GDELT collector worker — world-wide Russia coverage (volume + tone).

Iterates the country registry and stores daily aggregates into gdelt_daily.
Run with --days 90 once for initial backfill, then --loop for steady state.
"""
import argparse
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import text

from src.collectors.gdelt import collect_country
from src.countries import COUNTRIES, ensure_countries_in_db, gdelt_countries
from src.db import get_session, wait_for_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("gdelt-collector")


def _upsert_rows(rows: list[dict]) -> int:
    saved = 0
    with get_session() as session:
        for row in rows:
            session.execute(
                text("""
                    INSERT INTO gdelt_daily (day, country_code, volume, volume_share,
                                             tone_avg, article_samples, fetched_at)
                    VALUES (:day, :country_code, :volume, :volume_share,
                            :tone_avg, CAST(:samples AS jsonb), NOW())
                    ON CONFLICT (day, country_code) DO UPDATE SET
                        volume = COALESCE(EXCLUDED.volume, gdelt_daily.volume),
                        volume_share = COALESCE(EXCLUDED.volume_share, gdelt_daily.volume_share),
                        tone_avg = COALESCE(EXCLUDED.tone_avg, gdelt_daily.tone_avg),
                        article_samples = COALESCE(EXCLUDED.article_samples, gdelt_daily.article_samples),
                        fetched_at = NOW()
                """),
                {
                    "day": row["day"],
                    "country_code": row["country_code"],
                    "volume": row["volume"],
                    "volume_share": row["volume_share"],
                    "tone_avg": row["tone_avg"],
                    "samples": __import__("json").dumps(row["article_samples"], ensure_ascii=False)
                               if row["article_samples"] else None,
                },
            )
            saved += 1
    return saved


def collect_all(days: int = 7, with_samples: bool = True):
    """One full pass over all monitored countries."""
    countries = gdelt_countries()
    logger.info(f"GDELT pass: {len(countries)} countries, {days}d window")

    total_rows = 0
    failed = []
    consecutive_failures = 0
    for i, country in enumerate(countries, 1):
        code = country["code"]
        try:
            rows = collect_country(country, days=days, with_samples=with_samples)
            if rows:
                total_rows += _upsert_rows(rows)
                consecutive_failures = 0
                latest = rows[-1]
                logger.info(
                    f"  [{i}/{len(countries)}] {code}: {len(rows)} days, "
                    f"latest tone={latest['tone_avg']}, vol={latest['volume']}"
                )
            else:
                failed.append(code)
                consecutive_failures += 1
        except Exception as e:
            failed.append(code)
            consecutive_failures += 1
            logger.error(f"  [{i}/{len(countries)}] {code}: {e}")

        # Circuit breaker: a streak of empty countries usually means the IP is
        # rate-limited — cool down once instead of burning the whole pass
        if consecutive_failures == 5:
            logger.warning("5 countries failed in a row — cooling down 10 min")
            time.sleep(600)
            consecutive_failures = 0

    logger.info(f"GDELT pass complete: {total_rows} rows upserted, "
                f"{len(failed)} countries without data {failed[:10]}")

    # Freshness marker for the health monitor
    try:
        from src.queue import get_redis
        get_redis().set("stats:gdelt:last_run", datetime.now(timezone.utc).isoformat())
    except Exception:
        pass

    return total_rows


def main():
    parser = argparse.ArgumentParser(description="GEO PULSE GDELT collector")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=21600, help="Loop interval, sec (default 6h)")
    parser.add_argument("--days", type=int, default=7, help="Window per pass (use 90+ for backfill)")
    parser.add_argument("--no-samples", action="store_true", help="Skip artlist sample fetch")
    args = parser.parse_args()

    wait_for_db()
    ensure_countries_in_db()
    logger.info(f"Country registry synced: {len(COUNTRIES)} countries")

    if args.loop:
        logger.info(f"Starting GDELT collector loop (interval: {args.interval}s)")
        while True:
            try:
                collect_all(days=args.days, with_samples=not args.no_samples)
            except Exception as e:
                logger.error(f"GDELT collection error: {e}", exc_info=True)
            logger.info(f"Sleeping {args.interval}s...")
            time.sleep(args.interval)
    else:
        collect_all(days=args.days, with_samples=not args.no_samples)


if __name__ == "__main__":
    main()
