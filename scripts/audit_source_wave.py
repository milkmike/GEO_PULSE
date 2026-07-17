"""Read-only canary audit for a configured national-source expansion wave."""

import argparse
import json
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from src.collectors.publisher_attribution import normalize_publisher_domain
from src.db import get_session


PROTECTED_TABLES = (
    "articles",
    "article_discoveries",
    "publisher_domains",
    "analysis",
    "temperature",
    "signals",
    "signal_evidence",
    "briefs",
    "stories",
    "story_articles",
    "story_countries",
    "story_entities",
    "story_events",
    "content_embeddings",
)
REPORT_SCHEMA_VERSION = 1
WAVE_SOURCE_IDENTITIES = {
    "2026-07-17-rss-1": (
        ("AL", "RTSH", "https://rtsh.al/feed/", "rtsh.al"),
        ("AL", "Reporter.al", "https://reporter.al/feed/", "reporter.al"),
        ("CY", "Philenews", "https://www.philenews.com/feed/", "philenews.com"),
        ("CY", "Politis", "https://www.politis.com.cy/feed/", "politis.com.cy"),
        ("DK", "Politiken", "https://politiken.dk/rss/senestenyt.rss", "politiken.dk"),
        ("DK", "Information", "https://www.information.dk/feed", "information.dk"),
        ("IE", "The Irish Times", "https://www.irishtimes.com/arc/outboundfeeds/rss/category/ireland/?outputType=xml", "irishtimes.com"),
        ("IE", "TheJournal.ie", "https://www.thejournal.ie/feed/", "thejournal.ie"),
        ("ME", "RTCG", "https://rtcg.me/vijesti/rss.html", "rtcg.me"),
        ("ME", "Vijesti", "https://www.vijesti.me/rss", "vijesti.me"),
        ("MK", "MRT", "https://www.mrt.com.mk/rss.xml", "mrt.com.mk"),
        ("MK", "Meta.mk", "https://meta.mk/feed/", "meta.mk"),
        ("PT", "Diário de Notícias", "https://www.dn.pt/api/v1/collections/ultimas.rss", "dn.pt"),
        ("PT", "Observador", "https://observador.pt/feed/", "observador.pt"),
        ("SG", "CNA Singapore", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=10416", "channelnewsasia.com"),
        ("SI", "RTV Slovenija", "https://www.rtvslo.si/feeds/01.xml", "rtvslo.si"),
        ("SI", "N1 Slovenija", "https://n1info.si/feed/", "n1info.si"),
    ),
}


def _identity_dict(identity):
    country_code, name, url, publisher_domain = identity
    return {
        "country_code": country_code,
        "name": name,
        "url": url,
        "publisher_domain": publisher_domain,
    }


def _row_identity(row):
    return (
        row.get("country_code"),
        row.get("name"),
        row.get("url"),
        row.get("publisher_domain"),
    )


def validate_source_identities(rows, expected_identities):
    """Require the database wave rows to match the immutable release contract."""
    if expected_identities is None:
        return [], ["unsupported_wave_contract"]

    actual_identities = [_row_identity(row) for row in rows]
    actual_counts = Counter(actual_identities)
    expected_counts = Counter(expected_identities)
    errors = []
    if len(actual_identities) != len(expected_identities):
        errors.append(
            "source_count_mismatch:"
            f"{len(actual_identities)}!={len(expected_identities)}"
        )
    for identity in expected_counts - actual_counts:
        errors.append(
            "missing_source_identity:"
            + json.dumps(_identity_dict(identity), ensure_ascii=False, sort_keys=True)
        )
    for identity in actual_counts - expected_counts:
        errors.append(
            "unexpected_source_identity:"
            + json.dumps(_identity_dict(identity), ensure_ascii=False, sort_keys=True)
        )
    return [_identity_dict(identity) for identity in actual_identities], errors


def validate_baseline(path, wave, expected_identities):
    """Load a comparison baseline and fail closed on schema or wave drift."""
    errors = []
    try:
        baseline = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return None, [f"baseline_unreadable:{type(exc).__name__}"]

    if not isinstance(baseline, Mapping):
        return None, ["baseline_schema_invalid:expected_object"]
    if type(baseline.get("schema_version")) is not int or (
        baseline.get("schema_version") != REPORT_SCHEMA_VERSION
    ):
        errors.append(
            "baseline_schema_version_mismatch:"
            f"{baseline.get('schema_version')!r}!={REPORT_SCHEMA_VERSION}"
        )
    if baseline.get("wave") != wave:
        errors.append(
            f"baseline_wave_mismatch:{baseline.get('wave')!r}!={wave!r}"
        )

    expected_json = (
        [_identity_dict(identity) for identity in expected_identities]
        if expected_identities is not None else []
    )
    if baseline.get("expected_source_identities") != expected_json:
        errors.append("baseline_expected_source_identities_mismatch")

    counts = baseline.get("protected_counts")
    if not isinstance(counts, Mapping):
        errors.append("baseline_protected_counts_invalid:expected_object")
        counts = None
    return counts, errors


def validate_protected_counts(protected_counts, baseline_counts=None):
    """Return fail-closed validation errors for protected count snapshots."""
    errors = []
    if not isinstance(protected_counts, Mapping):
        return ["snapshot_protected_counts_invalid:expected_object"]
    for name in PROTECTED_TABLES:
        if name not in protected_counts:
            errors.append(
                f"snapshot_missing_protected_count:{name}; check the audit "
                "snapshot and database schema before continuing"
            )
        elif type(protected_counts[name]) is not int or protected_counts[name] < 0:
            errors.append(
                f"snapshot_invalid_protected_count:{name}; expected a "
                "non-negative integer"
            )

    if baseline_counts is None:
        return errors
    if not isinstance(baseline_counts, Mapping):
        return [*errors, "baseline_protected_counts_invalid:expected_object"]

    for name in PROTECTED_TABLES:
        if name not in baseline_counts:
            errors.append(
                "baseline_missing_protected_counts:"
                f"{name}; refresh baseline with the current audit before continuing"
            )
        elif type(baseline_counts[name]) is not int or baseline_counts[name] < 0:
            errors.append(
                f"baseline_invalid_protected_count:{name}; refresh baseline "
                "with the current audit before continuing"
            )
        elif (
            name in protected_counts
            and type(protected_counts[name]) is int
            and protected_counts[name] < baseline_counts[name]
        ):
            errors.append(
                f"protected_count_decreased:{name}:"
                f"{protected_counts[name]}<{baseline_counts[name]}"
            )
    return errors


def evaluate_wave(
    rows,
    protected_counts,
    baseline_counts=None,
    *,
    wave=None,
    expected_identities=None,
    baseline_errors=(),
):
    """Evaluate one source wave without mutating source or article records."""
    evaluated = []
    for row in rows:
        reasons = []
        if row["last_status"] != "ok":
            reasons.append("last_status_not_ok")
        if (
            row["last_fetch_age_minutes"] is None
            or not 0 <= row["last_fetch_age_minutes"] <= 60
        ):
            reasons.append("fetch_not_recent")
        if row["foreign_url_count"]:
            reasons.append("foreign_article_url")
        if row["unsafe_geo_count"]:
            reasons.append("unsafe_geo_attribution")
        evaluated.append({**row, "ok": not reasons, "reasons": reasons})

    passed = sum(item["ok"] for item in evaluated)
    protected_count_errors = validate_protected_counts(
        protected_counts,
        baseline_counts,
    )
    actual_identities, identity_errors = validate_source_identities(
        rows,
        expected_identities,
    ) if wave is not None else ([], [])
    protected_ok = not protected_count_errors
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "wave": wave,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total": len(evaluated),
            "passed": passed,
            "failed": len(evaluated) - passed,
        },
        "wave_has_sources": bool(evaluated),
        "expected_source_identities": (
            [_identity_dict(identity) for identity in expected_identities]
            if expected_identities is not None else []
        ),
        "actual_source_identities": actual_identities,
        "source_identities_ok": not identity_errors,
        "source_identity_errors": identity_errors,
        "baseline_ok": not baseline_errors,
        "baseline_errors": list(baseline_errors),
        "protected_counts": protected_counts,
        "protected_counts_ok": protected_ok,
        "protected_count_errors": protected_count_errors,
        "sources": evaluated,
    }


def load_snapshot(wave):
    """Load the live wave state and protected counts with SELECT-only queries."""
    with get_session() as session:
        rows = session.execute(text("""
            SELECT s.id AS source_id, s.name, s.country_code, s.url, s.last_status,
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
            "publisher_domain": normalize_publisher_domain(
                source_config.get("publisher_domain")
            ),
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
    baseline_mode = parser.add_mutually_exclusive_group()
    baseline_mode.add_argument("--baseline", type=Path)
    baseline_mode.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    rows, protected_counts = load_snapshot(args.wave)
    expected_identities = WAVE_SOURCE_IDENTITIES.get(args.wave)
    baseline_counts = None
    baseline_errors = []
    if args.baseline:
        baseline_counts, baseline_errors = validate_baseline(
            args.baseline,
            args.wave,
            expected_identities,
        )
    report = evaluate_wave(
        rows,
        protected_counts,
        baseline_counts,
        wave=args.wave,
        expected_identities=expected_identities,
        baseline_errors=baseline_errors,
    )
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(output)
    print(output)
    if args.baseline_only:
        return 0 if (
            report["protected_counts_ok"]
            and expected_identities is not None
        ) else 1
    return 0 if (
        report["wave_has_sources"]
        and report["summary"]["failed"] == 0
        and report["source_identities_ok"]
        and report["baseline_ok"]
        and report["protected_counts_ok"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
