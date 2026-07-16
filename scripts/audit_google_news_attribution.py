"""Read-only Google News publisher-attribution release gates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import text

from src.db import get_session, wait_for_db


def _value(row: Any, name: str, default: Any = None) -> Any:
    if hasattr(row, name):
        return getattr(row, name)
    mapping = getattr(row, "_mapping", row if isinstance(row, dict) else {})
    return mapping.get(name, default)


def _validate_baselines(article_baseline: int, temperature_baseline: int) -> None:
    if article_baseline < 0 or temperature_baseline < 0:
        raise ValueError("baselines must be non-negative")


def build_attribution_audit(
    *,
    article_baseline: int,
    temperature_baseline: int,
) -> dict[str, Any]:
    """Return extraction, attribution, mismatch, and count-delta monitoring."""

    _validate_baselines(article_baseline, temperature_baseline)
    with get_session() as session:
        metadata = session.execute(text("""
            WITH /* attribution_audit:metadata */ google_items AS (
                SELECT ar.publisher_name, ar.publisher_domain, ar.geo_status,
                       ar.publisher_source_id
                FROM articles ar
                JOIN sources discovery ON discovery.id = ar.source_id
                WHERE COALESCE(discovery.config->>'feed_mode', 'publisher')
                      = 'publisher_discovery'
                UNION ALL
                SELECT item.publisher_name, item.publisher_domain,
                       item.geo_status, NULL::integer AS publisher_source_id
                FROM article_discoveries item
                JOIN sources discovery ON discovery.id = item.discovery_source_id
                WHERE COALESCE(discovery.config->>'feed_mode', 'publisher')
                      = 'publisher_discovery'
            )
            SELECT COUNT(*)::bigint AS total,
                   COUNT(*) FILTER (
                       WHERE NULLIF(TRIM(publisher_name), '') IS NOT NULL
                         AND NULLIF(TRIM(publisher_domain), '') IS NOT NULL
                   )::bigint AS metadata_complete,
                   COUNT(*) FILTER (
                       WHERE geo_status = 'publisher_verified'
                   )::bigint AS verified,
                   COUNT(*) FILTER (
                       WHERE geo_status = 'publisher_reassigned'
                   )::bigint AS reassigned,
                   COUNT(*) FILTER (
                       WHERE geo_status IN ('unverified', 'legacy_unverified')
                          OR (
                              geo_status = 'source_verified'
                              AND publisher_source_id IS NULL
                          )
                   )::bigint AS unknown
            FROM google_items
        """)).fetchone()
        matrix_rows = session.execute(text("""
            WITH /* attribution_audit:matrix */ attribution_matrix AS (
                SELECT TRIM(discovery.country_code) AS discovery_country,
                       TRIM(publisher.country_code) AS publisher_country,
                       COUNT(*)::bigint AS count
                FROM articles ar
                JOIN sources discovery ON discovery.id = ar.source_id
                JOIN article_country_facts publisher
                  ON publisher.article_id = ar.id
                WHERE COALESCE(discovery.config->>'feed_mode', 'publisher')
                      = 'publisher_discovery'
                GROUP BY TRIM(discovery.country_code),
                         TRIM(publisher.country_code)
            )
            SELECT discovery_country, publisher_country, count
            FROM attribution_matrix
            ORDER BY discovery_country, publisher_country
        """)).fetchall()
        foreign_ru = session.execute(text("""
            SELECT /* attribution_audit:foreign_ru_domains */
                   COUNT(DISTINCT ar.id)::bigint AS count
            FROM articles ar
            JOIN article_country_facts publisher
              ON publisher.article_id = ar.id
            LEFT JOIN publisher_domains registry
              ON LOWER(registry.domain) = LOWER(ar.publisher_domain)
            WHERE TRIM(publisher.country_code) <> 'RU'
              AND (
                  LOWER(TRIM(ar.publisher_domain)) LIKE '%.ru'
                  OR (
                      registry.status = 'verified'
                      AND TRIM(registry.country_code) = 'RU'
                  )
              )
        """)).fetchone()
        legacy = session.execute(text("""
            SELECT /* attribution_audit:legacy_rows */
                   COUNT(*)::bigint AS count
            FROM articles ar
            JOIN article_country_facts publisher
              ON publisher.article_id = ar.id
            JOIN sources discovery ON discovery.id = ar.source_id
            WHERE ar.geo_status IN ('unverified', 'legacy_unverified')
               OR (
                   COALESCE(discovery.config->>'feed_mode', 'publisher')
                       = 'publisher_discovery'
                   AND ar.publisher_source_id IS NULL
               )
        """)).fetchone()
        counts = session.execute(text("""
            SELECT /* attribution_audit:analytics_counts */
                   (SELECT COUNT(*) FROM articles)::bigint AS article_count,
                   (SELECT COUNT(*) FROM temperature)::bigint
                       AS temperature_count
        """)).fetchone()

    total = int(_value(metadata, "total", 0) or 0)
    metadata_complete = int(_value(metadata, "metadata_complete", 0) or 0)
    article_count = int(_value(counts, "article_count", 0) or 0)
    temperature_count = int(_value(counts, "temperature_count", 0) or 0)
    matrix: dict[str, dict[str, int]] = {}
    for row in matrix_rows:
        discovery_country = str(_value(row, "discovery_country"))
        publisher_country = str(_value(row, "publisher_country"))
        matrix.setdefault(discovery_country, {})[publisher_country] = int(
            _value(row, "count", 0) or 0
        )

    return {
        "publisher_metadata_coverage": round(
            metadata_complete / total if total else 0.0,
            4,
        ),
        "verified": int(_value(metadata, "verified", 0) or 0),
        "reassigned": int(_value(metadata, "reassigned", 0) or 0),
        "unknown": int(_value(metadata, "unknown", 0) or 0),
        "matrix": matrix,
        "foreign_ru_domains_in_country_analytics": int(
            _value(foreign_ru, "count", 0) or 0
        ),
        "legacy_rows_in_country_analytics": int(
            _value(legacy, "count", 0) or 0
        ),
        "article_count": article_count,
        "temperature_count": temperature_count,
        "analytics_deltas": {
            "article_count": {
                "before": article_baseline,
                "after": article_count,
                "delta": article_count - article_baseline,
            },
            "temperature_count": {
                "before": temperature_baseline,
                "after": temperature_count,
                "delta": temperature_count - temperature_baseline,
            },
        },
    }


def evaluate_audit_gates(
    report: dict[str, Any],
    *,
    article_baseline: int,
    temperature_baseline: int,
) -> tuple[str, ...]:
    """Return stable release-gate identifiers for every blocking invariant."""

    _validate_baselines(article_baseline, temperature_baseline)
    failures = []
    if report["foreign_ru_domains_in_country_analytics"]:
        failures.append("foreign_russian_domains_present")
    if report["legacy_rows_in_country_analytics"]:
        failures.append("legacy_rows_present_in_country_analytics")
    if report["article_count"] < article_baseline:
        failures.append("article_count_below_baseline")
    if report["temperature_count"] < temperature_baseline:
        failures.append("temperature_count_below_baseline")
    return tuple(failures)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit Google News publisher attribution and release gates",
    )
    parser.add_argument("--article-baseline", type=int, required=True)
    parser.add_argument("--temperature-baseline", type=int, required=True)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wait_for_db()
    report = build_attribution_audit(
        article_baseline=args.article_baseline,
        temperature_baseline=args.temperature_baseline,
    )
    failures = evaluate_audit_gates(
        report,
        article_baseline=args.article_baseline,
        temperature_baseline=args.temperature_baseline,
    )
    payload = json.dumps(
        report,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )
    print(payload)
    if args.report:
        args.report.write_text(payload + "\n", encoding="utf-8")
    if failures:
        print("attribution audit failed: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
