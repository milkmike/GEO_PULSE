"""Backfill temperature: iterate day-by-day, calculate temperature as-of each date."""
import argparse
import logging
import math
import statistics
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

# Add project root to path
sys.path.insert(0, "/app")

from src.config import COUNTRY_NAMES, EVENT_TYPE_WEIGHTS
from src.db import get_session, Temperature

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [backfill] %(levelname)s: %(message)s",
)
logger = logging.getLogger("backfill")

WINDOW_DAYS = 14
TAU = WINDOW_DAYS * 86400

ACTION_MULTIPLIERS = {1: 1, 2: 3, 3: 5, 4: 8, 5: 12, 6: 15}


def calculate_temperature_at(country_code: str, as_of: datetime) -> dict | None:
    """Calculate temperature for a country as-of a specific datetime."""
    with get_session() as session:
        window_start = as_of - timedelta(days=WINDOW_DAYS)
        
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
                  AND ar.published_at > :window_start
                  AND ar.published_at <= :as_of
            """),
            {"cc": country_code, "window_start": window_start, "as_of": as_of},
        ).fetchall()

        if not rows:
            return None

        numerator = 0.0
        denominator = 0.0
        type_sums = {t: 0.0 for t in ("diplomatic", "military", "economic", "cultural", "security")}
        type_counts = {t: 0 for t in type_sums}
        source_ids = set()

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
        
        for row, cluster_decay in clustered_rows:
            sentiment = float(row.sentiment)
            published_at = row.published_at
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            
            age = (as_of - published_at).total_seconds()
            if age < 0:
                continue
            decay = math.exp(-age / TAU)
            
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


def main():
    parser = argparse.ArgumentParser(description="Backfill temperature data")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="Don't save, just print")
    args = parser.parse_args()

    start = datetime.strptime(args.start, "%Y-%m-%d").replace(
        hour=23, minute=59, second=59, tzinfo=timezone.utc
    )
    if args.end:
        end = datetime.strptime(args.end, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59, tzinfo=timezone.utc
        )
    else:
        end = datetime.now(timezone.utc)

    countries = list(COUNTRY_NAMES.keys())
    current = start
    total_saved = 0
    total_skipped = 0

    logger.info(f"Backfill: {start.date()} → {end.date()}, {len(countries)} countries")

    while current <= end:
        day_saved = 0
        day_line = []
        
        for cc in countries:
            result = calculate_temperature_at(cc, current)
            if result:
                if not args.dry_run:
                    with get_session() as session:
                        temp = Temperature(**result)
                        session.merge(temp)
                day_saved += 1
                day_line.append(f"{cc}={result['temperature']:+.1f}°({result['article_count']})")
                total_saved += 1
            else:
                total_skipped += 1

        if day_saved > 0:
            logger.info(f"{current.date()} | {day_saved}/{len(countries)} countries | {' '.join(day_line)}")
        else:
            logger.debug(f"{current.date()} | no data")

        current += timedelta(days=1)

    logger.info(f"Done! Saved: {total_saved}, Skipped (no data): {total_skipped}")


if __name__ == "__main__":
    main()
