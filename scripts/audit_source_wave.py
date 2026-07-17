"""Read-only canary audit for a configured national-source expansion wave."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from src.collectors.publisher_attribution import normalize_publisher_domain
from src.db import get_session


PROTECTED_TABLES = ("articles", "analysis", "temperature", "signals", "stories")


def evaluate_wave(rows, protected_counts, baseline_counts=None):
    """Evaluate one source wave without mutating source or article records."""
    evaluated = []
    for row in rows:
        reasons = []
        if row["last_status"] != "ok":
            reasons.append("last_status_not_ok")
        if row["last_fetch_age_minutes"] is None or row["last_fetch_age_minutes"] > 60:
            reasons.append("fetch_not_recent")
        if row["foreign_url_count"]:
            reasons.append("foreign_article_url")
        if row["unsafe_geo_count"]:
            reasons.append("unsafe_geo_attribution")
        evaluated.append({**row, "ok": not reasons, "reasons": reasons})

    passed = sum(item["ok"] for item in evaluated)
    protected_ok = baseline_counts is None or all(
        protected_counts[name] >= baseline_counts[name] for name in PROTECTED_TABLES
    )
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": len(evaluated),
            "passed": passed,
            "failed": len(evaluated) - passed,
        },
        "protected_counts": protected_counts,
        "protected_counts_ok": protected_ok,
        "sources": evaluated,
    }


def load_snapshot(wave):
    """Load the live wave state and protected counts with SELECT-only queries."""
    with get_session() as session:
        rows = session.execute(text("""
            SELECT s.id AS source_id, s.name, s.country_code, s.last_status,
                   s.config,
                   EXTRACT(EPOCH FROM (NOW() - s.last_fetch_at)) / 60 AS last_fetch_age_minutes,
                   COALESCE(
                     JSON_AGG(JSON_BUILD_OBJECT(
                       'url', COALESCE(NULLIF(a.resolved_url, ''), a.url),
                       'geo_status', a.geo_status
                     )) FILTER (WHERE a.id IS NOT NULL),
                     '[]'::json
                   ) AS articles
            FROM sources s
            LEFT JOIN articles a ON a.source_id = s.id
             AND a.collected_at >= s.created_at
            WHERE s.config->>'source_expansion_wave' = :wave
            GROUP BY s.id
            ORDER BY s.country_code, s.name
        """), {"wave": wave}).mappings().all()
        counts = {
            name: int(session.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar())
            for name in PROTECTED_TABLES
        }

    normalized = []
    for raw in rows:
        row = dict(raw)
        source_config = row.pop("config") or {}
        approved = {
            source_config.get("publisher_domain"),
            *(source_config.get("publisher_domain_aliases") or []),
        } - {None, ""}
        articles = row.pop("articles") or []
        foreign = 0
        unsafe = 0
        for article in articles:
            actual = normalize_publisher_domain(article.get("url"))
            if not any(
                actual == domain or (actual and actual.endswith(f".{domain}"))
                for domain in approved
            ):
                foreign += 1
            if article.get("geo_status") not in {
                "source_verified",
                "publisher_verified",
            }:
                unsafe += 1
        normalized.append({
            **row,
            "last_fetch_age_minutes": (
                float(row["last_fetch_age_minutes"])
                if row["last_fetch_age_minutes"] is not None else None
            ),
            "article_count": len(articles),
            "foreign_url_count": foreign,
            "unsafe_geo_count": unsafe,
        })
    return normalized, counts


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--wave", required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    rows, protected_counts = load_snapshot(args.wave)
    baseline_counts = None
    if args.baseline:
        baseline = json.loads(args.baseline.read_text())
        baseline_counts = baseline["protected_counts"]
    report = evaluate_wave(rows, protected_counts, baseline_counts)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(output)
    print(output)
    return 0 if report["summary"]["failed"] == 0 and report["protected_counts_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
