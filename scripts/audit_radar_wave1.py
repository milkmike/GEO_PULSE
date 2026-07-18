#!/usr/bin/env python3
"""Read-only release audit for the Early Warning Radar Wave 1 rollout.

The database transaction is repeatable-read and read-only.  The only optional
mutation performed by this program is writing ``--out`` when the operator asks
for a retained report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from uuid import UUID


SCHEMA_VERSION = 1
ROOT = Path(__file__).resolve().parents[1]

PROTECTED_TABLES = (
    "articles",
    "analysis",
    "temperature",
    "ru_index",
    "signals",
    "briefs",
    "threads",
    "thread_articles",
    "stories",
    "story_articles",
)

RADAR_TABLES = (
    "radar_observations",
    "action_events",
    "radar_trends",
    "radar_trend_members",
    "radar_trend_evidence",
    "radar_state_events",
    "radar_t0_revisions",
    "radar_contour_links",
    "analysis_runs",
    "notification_events",
)

PUBLIC_ID_TABLES = RADAR_TABLES
WRITE_SNAPSHOT_TABLES = PROTECTED_TABLES + RADAR_TABLES


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def database_metadata(_database_url: str | None) -> dict[str, str]:
    """Return deliberately non-identifying connection metadata."""

    return {"driver": "postgresql", "identifier": "redacted"}


def _cursor(connection):
    return connection.cursor()


def _execute(connection, statement: str, parameters: Sequence[Any] | None = None):
    cursor = _cursor(connection)
    try:
        cursor.execute(statement, parameters)
        return cursor.fetchall()
    finally:
        cursor.close()


def _scalar(
    connection,
    statement: str,
    parameters: Sequence[Any] | None = None,
    *,
    default: Any = None,
) -> Any:
    cursor = _cursor(connection)
    try:
        cursor.execute(statement, parameters)
        row = cursor.fetchone()
        return default if row is None else row[0]
    finally:
        cursor.close()


def _begin_read_only(connection) -> None:
    cursor = _cursor(connection)
    try:
        cursor.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    finally:
        cursor.close()


def _table_exists(connection, table: str) -> bool:
    value = _scalar(
        connection,
        "/* audit_table_exists */ SELECT to_regclass('public.' || %s)",
        (table,),
    )
    return value is not None


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _primary_key_columns(connection, table: str) -> list[str]:
    rows = _execute(
        connection,
        """
        /* audit_primary_key_columns */
        SELECT attribute.attname
        FROM pg_index index_definition
        JOIN pg_class relation ON relation.oid = index_definition.indrelid
        JOIN pg_namespace namespace ON namespace.oid = relation.relnamespace
        JOIN unnest(index_definition.indkey) WITH ORDINALITY key(attnum, position) ON TRUE
        JOIN pg_attribute attribute
          ON attribute.attrelid = relation.oid AND attribute.attnum = key.attnum
        WHERE namespace.nspname = 'public' AND relation.relname = %s
          AND index_definition.indisprimary
        ORDER BY key.position
        """,
        (table,),
    )
    return [str(row[0]) for row in rows]


def _new_identity_hash():
    return hashlib.sha256()


def _update_identity_hash(digest, values: Sequence[Any]) -> None:
    rendered = json.dumps(
        _json_value(list(values)), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    digest.update(rendered.encode("utf-8"))
    digest.update(b"\n")


def _primary_key_snapshot(
    connection,
    table: str,
    columns: Sequence[str],
    baseline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    selected = ", ".join(_quote_identifier(column) for column in columns)
    ordered = ", ".join(_quote_identifier(column) for column in columns)
    statement = f"/* audit_primary_keys:{table} */ SELECT {selected} FROM {_quote_identifier(table)} ORDER BY {ordered}"
    cursor = _cursor(connection)
    digest = _new_identity_hash()
    baseline_digest = _new_identity_hash()
    count = 0
    last: list[Any] | None = None
    baseline_count = 0
    expected_count = int(baseline.get("count", 0) or 0) if baseline else 0
    boundary_seen = baseline is not None and expected_count == 0
    boundary = _json_value(baseline.get("boundary")) if baseline else None
    try:
        cursor.execute(statement)
        while True:
            rows = cursor.fetchmany(2000)
            if not rows:
                break
            for row in rows:
                values = tuple(row)
                normalized = _json_value(list(values))
                _update_identity_hash(digest, values)
                count += 1
                last = normalized
                if baseline is not None and not boundary_seen:
                    _update_identity_hash(baseline_digest, values)
                    baseline_count += 1
                    if normalized == boundary:
                        boundary_seen = True
    finally:
        cursor.close()

    result: dict[str, Any] = {
        "columns": list(columns),
        "count": count,
        "fingerprint": "sha256:" + digest.hexdigest(),
        "boundary": last,
    }
    if baseline is not None:
        expected_fingerprint = str(baseline.get("fingerprint", ""))
        actual_fingerprint = "sha256:" + baseline_digest.hexdigest()
        unchanged = (
            (expected_count == 0 or boundary_seen)
            and baseline_count == expected_count
            and actual_fingerprint == expected_fingerprint
            and list(columns) == list(baseline.get("columns", ()))
        )
        result["baseline_check"] = {
            "status": "unchanged" if unchanged else "changed",
            "boundary_found": boundary_seen,
            "expected_count": expected_count,
            "actual_count": baseline_count,
            "expected_fingerprint": expected_fingerprint,
            "actual_fingerprint": actual_fingerprint,
        }
    return result


def _audit_protected_table(
    connection,
    table: str,
    baseline: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not _table_exists(connection, table):
        return {"status": "missing", "count": None, "primary_key": None}
    columns = _primary_key_columns(connection, table)
    if not columns:
        count = int(
            _scalar(
                connection,
                f"/* audit_count:{table} */ SELECT count(*) FROM {_quote_identifier(table)}",
                default=0,
            )
            or 0
        )
        return {
            "status": "invalid_no_primary_key",
            "count": count,
            "primary_key": {"columns": [], "count": count, "fingerprint": None, "boundary": None},
        }
    baseline_key = None
    if baseline:
        candidate = baseline.get("primary_key")
        if isinstance(candidate, Mapping):
            baseline_key = candidate
    identity = _primary_key_snapshot(connection, table, columns, baseline_key)
    return {"status": "available", "count": identity["count"], "primary_key": identity}


def _audit_count_table(connection, table: str) -> dict[str, Any]:
    if not _table_exists(connection, table):
        return {"status": "missing", "count": None}
    count = int(
        _scalar(
            connection,
            f"/* audit_count:{table} */ SELECT count(*) FROM {_quote_identifier(table)}",
            default=0,
        )
        or 0
    )
    return {"status": "available", "count": count}


def required_migrations() -> list[str]:
    migration_dir = ROOT / "scripts" / "migrations"
    required: list[str] = []
    for path in migration_dir.glob("*.sql"):
        prefix = path.name.split("_", 1)[0]
        if prefix.isdigit() and 27 <= int(prefix) <= 29:
            required.append(path.name)
    return sorted(required)


def _migration_report(connection) -> dict[str, Any]:
    exists = _scalar(
        connection,
        "/* audit_schema_migrations_exists */ SELECT to_regclass('public.schema_migrations')",
    )
    required = required_migrations()
    if exists is None:
        return {
            "status": "tracking_table_missing",
            "required": required,
            "applied": [],
            "missing_required": required,
        }
    applied = [
        str(row[0])
        for row in _execute(
            connection,
            "/* audit_schema_migrations */ SELECT filename FROM schema_migrations ORDER BY filename",
        )
    ]
    missing = sorted(set(required) - set(applied))
    return {
        "status": "complete" if not missing else "incomplete",
        "required": required,
        "applied": applied,
        "missing_required": missing,
    }


def _state_distribution(connection, available: set[str]) -> dict[str, Any]:
    states = ("candidate", "emerging", "confirmed", "cooling", "resolved", "rejected")
    if "radar_trends" not in available:
        return {"status": "unavailable", "counts": {state: 0 for state in states}}
    rows = _execute(
        connection,
        "/* audit_radar_states */ SELECT state, count(*) FROM radar_trends GROUP BY state ORDER BY state",
    )
    counts = {state: 0 for state in states}
    counts.update({str(row[0]): int(row[1]) for row in rows})
    return {"status": "available", "counts": counts}


def _contour_completeness(connection, available: set[str]) -> dict[str, Any]:
    required = {"radar_trends", "radar_contour_links"}
    if not required.issubset(available):
        return {
            "status": "unavailable",
            "media_trends": None,
            "action_trends": None,
            "possible_pairs": None,
            "linked_pairs": None,
            "ratio": None,
        }
    row = _execute(
        connection,
        """
        /* audit_contour_completeness */
        WITH possible AS (
          SELECT count(*) AS count
          FROM radar_trends media
          JOIN radar_trends action
            ON action.scope = 'country' AND action.contour = 'action'
           AND action.country_code = media.country_code
           AND action.subject_key = media.subject_key
           AND action.direction = media.direction
          WHERE media.scope = 'country' AND media.contour = 'media'
        )
        SELECT
          count(*) FILTER (WHERE trend.scope = 'country' AND trend.contour = 'media'),
          count(*) FILTER (WHERE trend.scope = 'country' AND trend.contour = 'action'),
          (SELECT count FROM possible),
          (SELECT count(*) FROM radar_contour_links)
        FROM radar_trends trend
        """,
    )[0]
    media, action, possible, linked = (int(value or 0) for value in row)
    return {
        "status": "available",
        "media_trends": media,
        "action_trends": action,
        "possible_pairs": possible,
        "linked_pairs": linked,
        "ratio": round(linked / possible, 6) if possible else None,
    }


def _replay_evidence(replay: Mapping[str, Any], minimum: float) -> dict[str, Any]:
    raw = replay.get("evidence_completeness", {})
    complete = int(raw.get("complete", 0) or 0) if isinstance(raw, Mapping) else 0
    incomplete = int(raw.get("incomplete", 0) or 0) if isinstance(raw, Mapping) else 0
    total = complete + incomplete
    ratio = complete / total if total else 0.0
    return {
        "source": "shadow_replay",
        "minimum": minimum,
        "observations": {
            "total": total,
            "valid": complete,
            "invalid": incomplete,
            "ratio": round(ratio, 6),
            "validity_scope": "non-empty replay evidence; persisted roots are checked after apply",
        },
        "trend_links": {"total": None, "valid": None, "invalid": None, "status": "not_persisted"},
    }


def _replay_state_distribution(replay: Mapping[str, Any]) -> dict[str, Any]:
    counts = {
        state: int(replay.get(key, 0) or 0)
        for state, key in (
            ("candidate", "candidates"),
            ("emerging", "emerging"),
            ("confirmed", "confirmed"),
            ("cooling", "cooling"),
            ("resolved", "resolved"),
            ("rejected", "rejected"),
        )
    }
    return {"status": "available", "source": "shadow_replay", "counts": counts}


def _replay_coverage(replay: Mapping[str, Any]) -> dict[str, Any]:
    coverage = replay.get("country_coverage", {})
    return {
        "status": "shadow_replay",
        "collector_suppressed": int(replay.get("collector_suppressed", 0) or 0),
        "country_coverage": dict(coverage) if isinstance(coverage, Mapping) else {},
        "confirmed_below_hard_gate": None,
    }


def _replay_t0(replay: Mapping[str, Any]) -> dict[str, Any]:
    distribution = replay.get("t0_distribution", {})
    return {
        "status": "shadow_replay",
        "distribution": dict(distribution) if isinstance(distribution, Mapping) else {},
        "invalid": None,
        "validity_scope": "distribution only; chronology is checked against persisted rows after apply",
    }


def _database_evidence(connection, available: set[str], minimum: float) -> dict[str, Any]:
    if not {"radar_observations", "radar_trend_evidence"}.issubset(available):
        return {
            "source": "database",
            "minimum": minimum,
            "observations": {"total": 0, "valid": 0, "invalid": 0, "ratio": 0.0, "status": "unavailable"},
            "trend_links": {"total": 0, "valid": 0, "invalid": 0, "status": "unavailable"},
        }
    observation = _execute(
        connection,
        """
        /* audit_observation_evidence */
        SELECT count(*), count(*) FILTER (WHERE
          jsonb_typeof(evidence) = 'object' AND evidence <> '{}'::jsonb AND (
            article_id IS NOT NULL OR story_id IS NOT NULL OR signal_id IS NOT NULL
            OR canonical_entity_id IS NOT NULL
            OR jsonb_path_exists(evidence, '$.article_ids[*]')
            OR jsonb_path_exists(evidence, '$.story_ids[*]')
            OR jsonb_path_exists(evidence, '$.signal_ids[*]')
            OR jsonb_path_exists(evidence, '$.entity_ids[*]')
            OR evidence ? 'source_id' OR evidence ? 'source_record_id'
          )
        )
        FROM radar_observations
        """,
    )[0]
    linked = _execute(
        connection,
        """
        /* audit_trend_evidence */
        SELECT count(*), count(*) FILTER (WHERE
          role IN ('trigger','support','context','contradiction')
          AND contribution BETWEEN -1 AND 1
          AND (observation_id IS NOT NULL OR action_event_id IS NOT NULL
               OR article_id IS NOT NULL OR story_id IS NOT NULL OR signal_id IS NOT NULL
               OR canonical_entity_id IS NOT NULL)
        )
        FROM radar_trend_evidence
        """,
    )[0]
    total, valid = int(observation[0] or 0), int(observation[1] or 0)
    link_total, link_valid = int(linked[0] or 0), int(linked[1] or 0)
    return {
        "source": "database",
        "minimum": minimum,
        "observations": {
            "total": total,
            "valid": valid,
            "invalid": total - valid,
            "ratio": round(valid / total, 6) if total else 0.0,
            "status": "available",
        },
        "trend_links": {
            "total": link_total,
            "valid": link_valid,
            "invalid": link_total - link_valid,
            "status": "available",
        },
    }


def _coverage_suppression(connection, available: set[str]) -> dict[str, Any]:
    if not {"radar_observations", "radar_trends"}.issubset(available):
        return {
            "status": "unavailable",
            "observations_at_or_below_hard_gate": None,
            "trends_at_or_below_hard_gate": None,
            "confirmed_below_hard_gate": None,
        }
    row = _execute(
        connection,
        """
        /* audit_coverage_suppression */
        SELECT
          (SELECT count(*) FROM radar_observations WHERE coverage_confidence <= 0.5),
          count(*) FILTER (WHERE coverage_confidence <= 0.5),
          count(*) FILTER (WHERE coverage_confidence <= 0.5 AND state = 'confirmed')
        FROM radar_trends
        """,
    )[0]
    return {
        "status": "available",
        "observations_at_or_below_hard_gate": int(row[0] or 0),
        "trends_at_or_below_hard_gate": int(row[1] or 0),
        "confirmed_below_hard_gate": int(row[2] or 0),
    }


def _t0_sanity(connection, available: set[str]) -> dict[str, Any]:
    if not {"radar_trends", "radar_t0_revisions"}.issubset(available):
        return {"status": "unavailable", "invalid_trends": None, "invalid_revisions": None, "invalid": None}
    trend_invalid = int(
        _scalar(
            connection,
            """
            /* audit_t0_trends */
            SELECT count(*) FROM radar_trends
            WHERE (detected_at IS NOT NULL AND first_observed_at > detected_at)
               OR (detected_at IS NOT NULL AND t0_auto IS NOT NULL AND t0_auto > detected_at)
               OR (confirmed_at IS NOT NULL AND detected_at IS NOT NULL AND confirmed_at < detected_at)
               OR (confirmed_at IS NOT NULL AND t0_effective IS NOT NULL AND t0_effective > confirmed_at)
            """,
            default=0,
        )
        or 0
    )
    revision_invalid = int(
        _scalar(
            connection,
            """
            /* audit_t0_revisions */
            SELECT count(*) FROM radar_t0_revisions
            WHERE revised_t0 > created_at OR btrim(reason) = ''
            """,
            default=0,
        )
        or 0
    )
    return {
        "status": "available",
        "invalid_trends": trend_invalid,
        "invalid_revisions": revision_invalid,
        "invalid": trend_invalid + revision_invalid,
    }


def _duplicate_public_ids(connection, available: set[str]) -> dict[str, Any]:
    tables = [table for table in PUBLIC_ID_TABLES if table in available]
    if not tables:
        return {"status": "unavailable", "duplicates": None}
    union = " UNION ALL ".join(
        f"SELECT public_id::text AS public_id FROM {_quote_identifier(table)}" for table in tables
    )
    duplicates = int(
        _scalar(
            connection,
            f"""
            /* audit_duplicate_public_ids */
            SELECT count(*) FROM (
              SELECT public_id FROM ({union}) identifiers
              GROUP BY public_id HAVING count(*) > 1
            ) duplicates
            """,
            default=0,
        )
        or 0
    )
    return {"status": "available", "tables": tables, "duplicates": duplicates}


def _notification_idempotency(connection, available: set[str]) -> dict[str, Any]:
    required = {"notification_events", "radar_trends", "radar_state_events"}
    if not required.issubset(available):
        return {
            "status": "unavailable",
            "duplicate_delivery_keys": None,
            "invalid_references": None,
        }
    row = _execute(
        connection,
        """
        /* audit_notification_idempotency */
        SELECT
          (SELECT count(*) FROM (
             SELECT trend_id, transition_id, channel, audience_key
             FROM notification_events
             GROUP BY trend_id, transition_id, channel, audience_key HAVING count(*) > 1
           ) duplicate_keys),
          count(*) FILTER (WHERE trend.id IS NULL OR transition.id IS NULL
                            OR transition.trend_id <> notification.trend_id)
        FROM notification_events notification
        LEFT JOIN radar_trends trend ON trend.id = notification.trend_id
        LEFT JOIN radar_state_events transition ON transition.public_id = notification.transition_id
        """,
    )[0]
    return {
        "status": "available",
        "duplicate_delivery_keys": int(row[0] or 0),
        "invalid_references": int(row[1] or 0),
    }


def _public_get_verification(
    snapshot: Mapping[str, Any],
    baseline_report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if baseline_report is None:
        return {
            "status": "not_requested",
            "input": None,
            "before": None,
            "after": dict(snapshot),
            "changed_tables": [],
        }
    container = baseline_report.get("public_get_write_verification", {})
    before = container.get("after") if isinstance(container, Mapping) else None
    if not isinstance(before, Mapping):
        return {
            "status": "invalid_baseline",
            "input": {"schema_version": baseline_report.get("schema_version"), "phase": baseline_report.get("phase")},
            "before": None,
            "after": dict(snapshot),
            "changed_tables": list(WRITE_SNAPSHOT_TABLES),
        }
    changed = [
        table
        for table in WRITE_SNAPSHOT_TABLES
        if before.get(table) != snapshot.get(table)
    ]
    return {
        "status": "unchanged" if not changed else "changed",
        "input": {"schema_version": baseline_report.get("schema_version"), "phase": baseline_report.get("phase")},
        "before": dict(before),
        "after": dict(snapshot),
        "changed_tables": changed,
    }


def collect_database_report(
    connection,
    *,
    phase: str,
    evidence_minimum: float,
    compare_report: Mapping[str, Any] | None = None,
    replay_report: Mapping[str, Any] | None = None,
    public_get_before: Mapping[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Collect one sanitized report and always roll its read-only transaction back."""

    generated_at = generated_at or datetime.now(timezone.utc)
    _begin_read_only(connection)
    try:
        before_tables: Mapping[str, Any] = {}
        if compare_report:
            protected = compare_report.get("protected", {})
            if isinstance(protected, Mapping) and isinstance(protected.get("tables"), Mapping):
                before_tables = protected["tables"]
        protected_tables = {
            table: _audit_protected_table(connection, table, before_tables.get(table))
            for table in PROTECTED_TABLES
        }
        radar_tables = {table: _audit_count_table(connection, table) for table in RADAR_TABLES}
        available = {
            table for table, result in radar_tables.items() if result.get("status") == "available"
        }
        if phase == "shadow" and replay_report is not None:
            evidence = _replay_evidence(replay_report, evidence_minimum)
            observation_count = evidence["observations"]["total"]
            trend_count = int(replay_report.get("country_wave_count", 0) or 0) + int(
                replay_report.get("meta_trend_count", 0) or 0
            )
            state_distribution = _replay_state_distribution(replay_report)
            contour_completeness = {
                "status": "not_persisted",
                "media_trends": None,
                "action_trends": None,
                "possible_pairs": None,
                "linked_pairs": None,
                "ratio": None,
            }
            coverage_suppression = _replay_coverage(replay_report)
            t0_sanity = _replay_t0(replay_report)
        else:
            evidence = _database_evidence(connection, available, evidence_minimum)
            observation_count = radar_tables["radar_observations"]["count"]
            trend_count = radar_tables["radar_trends"]["count"]
            state_distribution = _state_distribution(connection, available)
            contour_completeness = _contour_completeness(connection, available)
            coverage_suppression = _coverage_suppression(connection, available)
            t0_sanity = _t0_sanity(connection, available)
        write_snapshot = {
            **{table: {"status": value["status"], "count": value["count"]} for table, value in protected_tables.items()},
            **{table: dict(value) for table, value in radar_tables.items()},
        }
        report = {
            "schema_version": SCHEMA_VERSION,
            "phase": phase,
            "generated_at": _utc_text(generated_at),
            "database": database_metadata(None),
            "configuration": {"evidence_completeness_minimum": evidence_minimum},
            "protected": {"tables": protected_tables},
            "migrations": _migration_report(connection),
            "radar": {
                "tables": radar_tables,
                "observation_count": observation_count,
                "trend_count": trend_count,
                "state_distribution": state_distribution,
                "contour_completeness": contour_completeness,
                "evidence": evidence,
                "coverage_suppression": coverage_suppression,
                "t0_sanity": t0_sanity,
                "duplicate_public_ids": _duplicate_public_ids(connection, available),
                "notification_idempotency": _notification_idempotency(connection, available),
            },
            "public_get_write_verification": _public_get_verification(
                write_snapshot, public_get_before
            ),
        }
        return _json_value(report)
    finally:
        connection.rollback()


def _failure(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"code": code, "message": message, "details": _json_value(details)}


def compare_reports(
    before: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    evidence_minimum: float,
) -> dict[str, Any]:
    """Evaluate pure report data without requiring a live database."""

    failures: list[dict[str, Any]] = []
    if current.get("schema_version") != SCHEMA_VERSION:
        failures.append(_failure("unsupported_schema", "Audit report schema is not supported."))

    current_protected = current.get("protected", {}).get("tables", {})
    before_protected = before.get("protected", {}).get("tables", {}) if before else {}
    for table in PROTECTED_TABLES:
        actual = current_protected.get(table, {})
        if actual.get("status") != "available":
            failures.append(
                _failure("protected_table_unavailable", f"Protected table {table} is not auditable.", table=table, status=actual.get("status"))
            )
            continue
        if not before:
            continue
        expected = before_protected.get(table, {})
        if expected.get("status") != "available":
            failures.append(
                _failure("protected_baseline_unavailable", f"Protected baseline for {table} is unavailable.", table=table)
            )
            continue
        expected_count = int(expected.get("count", 0) or 0)
        actual_count = int(actual.get("count", 0) or 0)
        if actual_count < expected_count:
            failures.append(
                _failure("protected_count_decreased", f"Protected row count decreased for {table}.", table=table, before=expected_count, after=actual_count)
            )
        identity = actual.get("primary_key") or {}
        baseline_check = identity.get("baseline_check") if isinstance(identity, Mapping) else None
        if isinstance(baseline_check, Mapping):
            unchanged = baseline_check.get("status") == "unchanged"
        else:
            expected_identity = expected.get("primary_key") or {}
            unchanged = (
                actual_count == expected_count
                and identity.get("columns") == expected_identity.get("columns")
                and identity.get("fingerprint") == expected_identity.get("fingerprint")
            )
        if not unchanged:
            failures.append(
                _failure("protected_primary_key_changed", f"Protected primary-key identity changed for {table}.", table=table)
            )

    phase = current.get("phase")
    migrations = current.get("migrations", {})
    if phase in {"shadow", "after"} and migrations.get("missing_required"):
        failures.append(
            _failure("required_migration_missing", "Required Radar migrations are not recorded by filename.", missing=migrations.get("missing_required"))
        )

    radar = current.get("radar", {})
    if phase in {"shadow", "after"}:
        for table in RADAR_TABLES:
            status = radar.get("tables", {}).get(table, {}).get("status")
            if status != "available":
                failures.append(
                    _failure("radar_table_unavailable", f"Radar table {table} is unavailable.", table=table, status=status)
                )
        observations = radar.get("evidence", {}).get("observations", {})
        ratio = float(observations.get("ratio", 0.0) or 0.0)
        if int(observations.get("total", 0) or 0) == 0 or ratio < evidence_minimum:
            failures.append(
                _failure("evidence_completeness_below_gate", "Evidence completeness is below the configured gate.", actual=ratio, minimum=evidence_minimum, total=observations.get("total"))
            )
        trend_links = radar.get("evidence", {}).get("trend_links", {})
        if phase == "after" and int(trend_links.get("invalid", 0) or 0) > 0:
            failures.append(
                _failure("evidence_validity_failed", "Persisted trend evidence contains invalid roots.", invalid=trend_links.get("invalid"))
            )
        coverage = radar.get("coverage_suppression", {})
        if int(coverage.get("confirmed_below_hard_gate", 0) or 0) > 0:
            failures.append(
                _failure("coverage_suppression_failed", "A confirmed trend bypassed the coverage hard gate.", count=coverage.get("confirmed_below_hard_gate"))
            )
        duplicates = radar.get("duplicate_public_ids", {})
        if int(duplicates.get("duplicates", 0) or 0) > 0:
            failures.append(
                _failure("duplicate_public_ids", "Duplicate Radar public IDs were found.", count=duplicates.get("duplicates"))
            )
        t0 = radar.get("t0_sanity", {})
        if int(t0.get("invalid", 0) or 0) > 0:
            failures.append(
                _failure("t0_invariant_failed", "T0 chronology invariants failed.", count=t0.get("invalid"))
            )
        notification = radar.get("notification_idempotency", {})
        if int(notification.get("duplicate_delivery_keys", 0) or 0) > 0 or int(notification.get("invalid_references", 0) or 0) > 0:
            failures.append(
                _failure("notification_idempotency_failed", "Notification delivery identities are not idempotent and valid.", duplicate_delivery_keys=notification.get("duplicate_delivery_keys"), invalid_references=notification.get("invalid_references"))
            )

    public_get = current.get("public_get_write_verification", {})
    if public_get.get("status") in {"changed", "invalid_baseline"}:
        failures.append(
            _failure("public_get_wrote_rows", "Public GET smoke changed a database row-count snapshot.", changed_tables=public_get.get("changed_tables"))
        )

    return {"passed": not failures, "failure_count": len(failures), "failures": failures}


def _load_report(path: str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"report must contain a JSON object: {path}")
    return payload


def _evidence_minimum(value: str) -> float:
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("evidence minimum must be between 0 and 1")
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Early Warning Radar Wave 1 release audit")
    parser.add_argument("--phase", choices=("before", "shadow", "after"), required=True)
    parser.add_argument("--json", action="store_true", help="print the stable JSON report")
    parser.add_argument("--compare", help="compare protected identities and counts with this report")
    parser.add_argument("--replay-report", help="shadow JSON emitted by scripts/build_radar.py")
    parser.add_argument("--public-get-before", help="audit report captured immediately before public GET smoke")
    parser.add_argument("--out", help="write the report to this explicit path")
    parser.add_argument("--database-url", help="PostgreSQL URL; defaults to DATABASE_URL, then PG* variables")
    parser.add_argument(
        "--evidence-minimum",
        type=_evidence_minimum,
        default=_evidence_minimum(os.environ.get("RADAR_EVIDENCE_COMPLETENESS_GATE", "0.95")),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    before = _load_report(args.compare)
    replay = _load_report(args.replay_report)
    public_before = _load_report(args.public_get_before)
    try:
        import psycopg2

        connection = psycopg2.connect(args.database_url or os.environ.get("DATABASE_URL") or "")
        try:
            report = collect_database_report(
                connection,
                phase=args.phase,
                evidence_minimum=args.evidence_minimum,
                compare_report=before,
                replay_report=replay,
                public_get_before=public_before,
            )
        finally:
            connection.close()
        comparison = compare_reports(before, report, evidence_minimum=args.evidence_minimum)
        report["gates"] = comparison
    except Exception as error:
        # Driver exceptions may repeat a DSN.  Never risk echoing credentials or
        # database identifiers; operators still get a useful failure class.
        print(f"radar audit failed: {type(error).__name__}", file=sys.stderr)
        return 2

    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(rendered, encoding="utf-8")
    if args.json:
        print(rendered, end="")
    else:
        verdict = "PASS" if comparison["passed"] else "FAIL"
        print(f"Radar Wave 1 audit {verdict}: phase={args.phase}, failures={comparison['failure_count']}")
        for failure in comparison["failures"]:
            print(f"- {failure['code']}: {failure['message']}")
    return 0 if comparison["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
