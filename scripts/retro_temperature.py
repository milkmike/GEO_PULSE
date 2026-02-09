"""Retrospective temperature calculation.

Recalculates temperature for each month in history,
creating historical temperature records so the dashboard
can show trends over time.
"""
import argparse
import logging
import math
import statistics
from datetime import datetime, timezone, timedelta

from sqlalchemy import text

from src.config import COUNTRY_NAMES, EVENT_TYPE_WEIGHTS
from src.db import get_session, wait_for_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [retro-temp] %(levelname)s: %(message)s",
)
logger = logging.getLogger("retro-temp")

WINDOW_DAYS = 30  # Use 30-day window for monthly snapshots
TAU = WINDOW_DAYS * 86400

ACTION_MULTIPLIERS = {
    1: 1, 2: 3, 3: 5, 4: 8, 5: 12, 6: 15,
}


def calculate_temperature_at(country_code: str, as_of: datetime, window_days: int = 30) -> dict | None:
    """Calculate what the temperature would have been at a given point in time."""
    tau = window_days * 86400

    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT a.sentiment, a.event_type, a.sentiment_confidence,
                       a.action_level, a.event_key,
                       ar.published_at, s.weight, s.id as source_id,
                       COALESCE(ar.reprint_count, 0) as reprint_count
                FROM analysis a
                JOIN articles ar ON a.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE s.country_code = :cc
                  AND a.is_relevant = true
                  AND a.sentiment IS NOT NULL
                  AND ar.published_at <= :as_of
                  AND ar.published_at > :window_start
            """),
            {
                "cc": country_code,
                "as_of": as_of,
                "window_start": as_of - timedelta(days=window_days),
            },
        ).fetchall()

        if len(rows) < 3:  # Need at least 3 articles for meaningful temperature
            return None

        # Group by event_key
        event_clusters = {}
        unclustered = []
        for row in rows:
            ek = getattr(row, 'event_key', None)
            if ek and len(str(ek)) > 3:
                event_clusters.setdefault(str(ek).lower().strip(), []).append(row)
            else:
                unclustered.append(row)

        clustered_rows = []
        for ek, cluster in event_clusters.items():
            cluster.sort(key=lambda r: float(r.weight or 1.0), reverse=True)
            for i, row in enumerate(cluster):
                clustered_rows.append((row, 0.2 ** i))
        for row in unclustered:
            clustered_rows.append((row, 1.0))

        numerator = 0.0
        denominator = 0.0
        type_sums = {t: 0.0 for t in ("diplomatic", "military", "economic", "cultural", "security")}
        type_counts = {t: 0 for t in type_sums}
        source_ids = set()

        for row, cluster_decay in clustered_rows:
            sentiment = float(row.sentiment)
            published_at = row.published_at
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)

            age = (as_of - published_at).total_seconds()
            if age < 0:
                continue
            decay = math.exp(-age / tau)

            w_source = float(row.weight or 1.0)
            event_type = row.event_type
            w_type = EVENT_TYPE_WEIGHTS.get(event_type, 1.0)
            importance = 1 + math.log1p(row.reprint_count)
            action_mult = ACTION_MULTIPLIERS.get(row.action_level or 1, 1)

            weight = w_source * w_type * decay * importance * action_mult * cluster_decay
            numerator += sentiment * weight
            denominator += abs(weight)

            if event_type in type_sums:
                type_sums[event_type] += sentiment * cluster_decay
                type_counts[event_type] += cluster_decay
            source_ids.add(row.source_id)

        if denominator == 0:
            return None

        raw_sentiment = numerator / denominator
        temperature = round(raw_sentiment * (100 / 3), 1)

        components = {}
        for t in type_sums:
            if type_counts[t] > 0:
                components[t] = round(type_sums[t] / type_counts[t], 2)
            else:
                components[t] = None

        return {
            "time": as_of,
            "country_code": country_code,
            "temperature": temperature,
            "raw_sentiment": round(raw_sentiment, 2),
            "diplomatic": components.get("diplomatic"),
            "military": components.get("military"),
            "economic": components.get("economic"),
            "cultural": components.get("cultural"),
            "security": components.get("security"),
            "article_count": len(rows),
            "source_count": len(source_ids),
            "trend": "stable",
            "anomaly_score": None,
        }


def save_retro_temperature(data: dict):
    """Save retrospective temperature record."""
    with get_session() as session:
        # Upsert — don't overwrite real-time readings
        session.execute(text("""
            INSERT INTO temperature (
                time, country_code, temperature, raw_sentiment,
                diplomatic, military, economic, cultural, security,
                article_count, source_count, trend, anomaly_score
            ) VALUES (
                :time, :country_code, :temperature, :raw_sentiment,
                :diplomatic, :military, :economic, :cultural, :security,
                :article_count, :source_count, :trend, :anomaly_score
            )
            ON CONFLICT (country_code, time) DO UPDATE SET
                temperature = EXCLUDED.temperature,
                raw_sentiment = EXCLUDED.raw_sentiment,
                article_count = EXCLUDED.article_count,
                source_count = EXCLUDED.source_count
        """), data)


def main():
    parser = argparse.ArgumentParser(description="Retrospective temperature calculation")
    parser.add_argument("--country", help="Single country code (default: all)")
    parser.add_argument("--start", default="2022-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", help="End date (default: now)")
    parser.add_argument("--interval", type=int, default=7, help="Days between snapshots")
    parser.add_argument("--window", type=int, default=30, help="Rolling window in days")
    args = parser.parse_args()

    wait_for_db()

    countries = [args.country.upper()] if args.country else list(COUNTRY_NAMES.keys())
    start = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc) if args.end else datetime.now(timezone.utc)

    total_saved = 0
    total_skipped = 0

    for code in countries:
        logger.info(f"Processing {code} ({COUNTRY_NAMES.get(code, code)})")
        current = start
        saved = 0
        skipped = 0

        while current <= end:
            result = calculate_temperature_at(code, current, args.window)
            if result:
                save_retro_temperature(result)
                saved += 1
                if saved % 10 == 0:
                    logger.info(f"  {code}: {saved} snapshots saved (at {current.strftime('%Y-%m-%d')})")
            else:
                skipped += 1

            current += timedelta(days=args.interval)

        logger.info(f"  {code}: {saved} saved, {skipped} skipped (no data)")
        total_saved += saved
        total_skipped += skipped

    logger.info(f"\nDone! Total: {total_saved} temperature records, {total_skipped} skipped")


if __name__ == "__main__":
    main()
