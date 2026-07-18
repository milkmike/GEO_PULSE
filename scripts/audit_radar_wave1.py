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
from typing import Any, Mapping, Sequence
from uuid import UUID


SCHEMA_VERSION = 1
DETECTOR_VERSION = "radar-wave-1"
LIFECYCLE_STATES = ("candidate", "emerging", "confirmed", "cooling", "resolved", "rejected")
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


def _utc_precise_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


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


def _identity_value(value: Any) -> dict[str, Any]:
    """Lossless typed representation for a PostgreSQL primary-key value."""

    if value is None:
        return {"type": "null", "value": None}
    if isinstance(value, bool):
        return {"type": "bool", "value": value}
    if isinstance(value, int):
        return {"type": "int", "value": str(value)}
    if isinstance(value, Decimal):
        return {"type": "decimal", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value.hex()}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("primary-key timestamp must include a timezone")
        return {"type": "datetime", "value": _utc_precise_text(value)}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, UUID):
        return {"type": "uuid", "value": str(value)}
    if isinstance(value, bytes):
        return {"type": "bytes", "value": value.hex()}
    if isinstance(value, str):
        return {"type": "str", "value": value}
    raise TypeError(f"unsupported primary-key type: {type(value).__name__}")


def serialize_identity(values: Sequence[Any]) -> str:
    return json.dumps(
        [_identity_value(value) for value in values],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


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


def _public_table(value: str) -> str:
    return f"public.{_quote_identifier(value)}"


def _primary_key_columns(connection, table: str) -> list[str]:
    rows = _execute(
        connection,
        """
        /* audit_primary_key_columns */
        SELECT attribute.attname
        FROM pg_catalog.pg_index index_definition
        JOIN pg_catalog.pg_class relation ON relation.oid = index_definition.indrelid
        JOIN pg_catalog.pg_namespace namespace ON namespace.oid = relation.relnamespace
        JOIN pg_catalog.unnest(index_definition.indkey) WITH ORDINALITY key(attnum, position) ON TRUE
        JOIN pg_catalog.pg_attribute attribute
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
    digest.update(serialize_identity(values).encode("utf-8"))
    digest.update(b"\n")


def _primary_key_snapshot(
    connection,
    table: str,
    columns: Sequence[str],
) -> dict[str, Any]:
    selected = ", ".join(_quote_identifier(column) for column in columns)
    ordered = ", ".join(_quote_identifier(column) for column in columns)
    statement = f"/* audit_primary_keys:{table} */ SELECT {selected} FROM {_public_table(table)} ORDER BY {ordered}"
    cursor = _cursor(connection)
    digest = _new_identity_hash()
    count = 0
    try:
        cursor.execute(statement)
        while True:
            rows = cursor.fetchmany(2000)
            if not rows:
                break
            for row in rows:
                values = tuple(row)
                _update_identity_hash(digest, values)
                count += 1
    finally:
        cursor.close()

    result: dict[str, Any] = {
        "columns": list(columns),
        "count": count,
        "fingerprint": "sha256:" + digest.hexdigest(),
    }
    return result


def _audit_protected_table(
    connection,
    table: str,
) -> dict[str, Any]:
    if not _table_exists(connection, table):
        return {"status": "missing", "count": None, "primary_key": None}
    columns = _primary_key_columns(connection, table)
    if not columns:
        count = int(
            _scalar(
                connection,
                f"/* audit_count:{table} */ SELECT count(*) FROM {_public_table(table)}",
                default=0,
            )
            or 0
        )
        return {
            "status": "invalid_no_primary_key",
            "count": count,
            "primary_key": {"columns": [], "count": count, "fingerprint": None},
        }
    identity = _primary_key_snapshot(connection, table, columns)
    return {"status": "available", "count": identity["count"], "primary_key": identity}


def _audit_count_table(connection, table: str) -> dict[str, Any]:
    if not _table_exists(connection, table):
        return {"status": "missing", "count": None}
    count = int(
        _scalar(
            connection,
            f"/* audit_count:{table} */ SELECT count(*) FROM {_public_table(table)}",
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
        if prefix.isdigit() and 27 <= int(prefix) <= 30:
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
            "/* audit_schema_migrations */ SELECT filename FROM public.schema_migrations ORDER BY filename",
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
    if "radar_trends" not in available:
        return {"status": "unavailable", "counts": None}
    rows = _execute(
        connection,
        "/* audit_radar_states */ SELECT scope, state, count(*) FROM public.radar_trends GROUP BY scope, state ORDER BY scope, state",
    )
    country = {state: 0 for state in LIFECYCLE_STATES}
    meta = {state: 0 for state in LIFECYCLE_STATES}
    for scope, state, count in rows:
        target = country if scope == "country" else meta if scope == "meta" else None
        if target is not None:
            target[str(state)] = int(count)
    return {
        "status": "available",
        "country": country,
        "meta": meta,
        "total": {state: country[state] + meta[state] for state in LIFECYCLE_STATES},
    }


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
          SELECT media.country_code, media.alignment_subject, media.alignment_direction,
                 least(count(DISTINCT media.id), count(DISTINCT action.id)) AS possible
          FROM public.radar_trends media
          JOIN public.radar_trends action
            ON action.scope = 'country' AND action.contour = 'action'
           AND action.country_code = media.country_code
           AND action.alignment_subject = media.alignment_subject
           AND action.alignment_direction = media.alignment_direction
          WHERE media.scope = 'country' AND media.contour = 'media'
            AND media.alignment_subject IS NOT NULL
            AND media.alignment_direction IS NOT NULL
          GROUP BY media.country_code, media.alignment_subject, media.alignment_direction
        ), possible_total AS (
          SELECT COALESCE(sum(possible), 0) AS count FROM possible
        ), valid_links AS (
          SELECT count(*) AS count
          FROM public.radar_contour_links link
          JOIN public.radar_trends media ON media.id = link.media_trend_id
          JOIN public.radar_trends action ON action.id = link.action_trend_id
          WHERE media.scope = 'country' AND media.contour = 'media'
            AND action.scope = 'country' AND action.contour = 'action'
            AND media.country_code = action.country_code
            AND action.alignment_subject = media.alignment_subject
            AND action.alignment_direction = media.alignment_direction
        )
        SELECT
          count(*) FILTER (WHERE trend.scope = 'country' AND trend.contour = 'media'),
          count(*) FILTER (WHERE trend.scope = 'country' AND trend.contour = 'action'),
          (SELECT count FROM possible_total),
          (SELECT count FROM valid_links)
        FROM public.radar_trends trend
        """,
    )[0]
    media, action, possible, linked = (int(value or 0) for value in row)
    return {
        "status": "not_applicable_no_pairs" if possible == 0 else "available",
        "media_trends": media,
        "action_trends": action,
        "possible_pairs": possible,
        "linked_pairs": linked,
        "ratio": round(linked / possible, 6) if possible else None,
    }


def _required_mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    result = value.get(key)
    if not isinstance(result, Mapping):
        raise ValueError(f"replay report requires {key}")
    return result


def _required_count(value: Mapping[str, Any], key: str) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int) or result < 0:
        raise ValueError(f"replay report requires non-negative integer {key}")
    return result


def _required_ratio(value: Mapping[str, Any], key: str, *, allow_none: bool = False) -> float | None:
    result = value.get(key)
    if result is None and allow_none:
        return None
    if isinstance(result, bool) or not isinstance(result, (int, float)) or not 0 <= float(result) <= 1:
        raise ValueError(f"replay report requires ratio {key}")
    return float(result)


def _validated_states(replay: Mapping[str, Any], key: str, expected_total: int) -> dict[str, int]:
    raw = _required_mapping(replay, key)
    counts = {state: _required_count(raw, state) for state in LIFECYCLE_STATES}
    if sum(counts.values()) != expected_total:
        raise ValueError(f"{key} must sum to its trend total")
    return counts


def validate_replay_report(replay: Mapping[str, Any]) -> None:
    if replay.get("shadow") is not True:
        raise ValueError("replay report must have shadow=true")
    if replay.get("lookback_days") != 90:
        raise ValueError("replay report lookback_days must be exactly 90")
    if replay.get("detector_version") != DETECTOR_VERSION:
        raise ValueError("replay report detector version is incompatible")
    raw_as_of = replay.get("as_of")
    if not isinstance(raw_as_of, str):
        raise ValueError("replay report as_of timestamp is required")
    try:
        as_of = datetime.fromisoformat(raw_as_of.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("replay report as_of timestamp is invalid") from error
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("replay report as_of timestamp must include a timezone")

    observation_count = _required_count(replay, "observation_count")
    country_count = _required_count(replay, "country_trend_count")
    meta_count = _required_count(replay, "meta_trend_count")
    country_states = _validated_states(replay, "country_state_counts", country_count)
    _validated_states(replay, "meta_state_counts", meta_count)

    evidence = _required_mapping(replay, "evidence_validity")
    evidence_total = _required_count(evidence, "total")
    evidence_valid = _required_count(evidence, "valid")
    evidence_invalid = _required_count(evidence, "invalid")
    evidence_ratio = _required_ratio(evidence, "ratio")
    if evidence_total != observation_count or evidence_total != evidence_valid + evidence_invalid:
        raise ValueError("evidence_validity counts are inconsistent")
    expected_ratio = evidence_valid / evidence_total if evidence_total else 0.0
    if abs(float(evidence_ratio) - expected_ratio) > 0.000001:
        raise ValueError("evidence_validity ratio is inconsistent")

    collector_suppressed = _required_count(replay, "collector_suppressed")
    if collector_suppressed > country_count:
        raise ValueError("collector_suppressed cannot exceed country_trend_count")
    confirmed_below = _required_count(replay, "confirmed_below_coverage_gate")
    if confirmed_below > country_states["confirmed"]:
        raise ValueError("confirmed_below_coverage_gate cannot exceed confirmed country trends")
    t0 = _required_mapping(replay, "t0_sanity")
    automatic = _required_count(t0, "automatic")
    unresolved = _required_count(t0, "unresolved")
    violations = _required_count(t0, "violations")
    trend_total = country_count + meta_count
    if automatic + unresolved != trend_total or violations > trend_total:
        raise ValueError("t0_sanity counts are inconsistent with trend totals")

    contour = _required_mapping(replay, "contour_completeness")
    possible = _required_count(contour, "possible_pairs")
    linked = _required_count(contour, "linked_pairs")
    ratio = _required_ratio(contour, "ratio", allow_none=True)
    if linked > possible:
        raise ValueError("contour linked_pairs cannot exceed possible_pairs")
    if possible == 0:
        if linked != 0 or ratio is not None:
            raise ValueError("empty contour pair set must use linked_pairs=0 and ratio=null")
    elif ratio is None or abs(ratio - linked / possible) > 0.000001:
        raise ValueError("contour completeness ratio is inconsistent")


def _replay_metrics(replay: Mapping[str, Any], minimum: float) -> dict[str, Any]:
    validate_replay_report(replay)
    evidence = dict(_required_mapping(replay, "evidence_validity"))
    country_count = _required_count(replay, "country_trend_count")
    meta_count = _required_count(replay, "meta_trend_count")
    contour = dict(_required_mapping(replay, "contour_completeness"))
    contour["status"] = "not_applicable_no_pairs" if contour["possible_pairs"] == 0 else "available"
    return {
        "observation_count": _required_count(replay, "observation_count"),
        "trend_count": country_count + meta_count,
        "country_trend_count": country_count,
        "meta_trend_count": meta_count,
        "state_distribution": {
            "status": "available",
            "source": "shadow_replay",
            "country": dict(_required_mapping(replay, "country_state_counts")),
            "meta": dict(_required_mapping(replay, "meta_state_counts")),
        },
        "evidence": {
            "source": "shadow_replay",
            "minimum": minimum,
            "observations": {**evidence, "status": "available"},
            "trend_links": {**evidence, "status": "available"},
        },
        "coverage_suppression": {
            "status": "available",
            "collector_suppressed": _required_count(replay, "collector_suppressed"),
            "confirmed_below_hard_gate": _required_count(replay, "confirmed_below_coverage_gate"),
        },
        "t0_sanity": {
            "status": "available",
            "distribution": dict(_required_mapping(replay, "t0_sanity")),
            "invalid": _required_count(_required_mapping(replay, "t0_sanity"), "violations"),
        },
        "contour_completeness": contour,
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
        r"""
        /* audit_observation_evidence */
        WITH validated AS (
          SELECT observation.id,
            jsonb_typeof(observation.evidence) = 'object' AS object_evidence,
            observation.article_id IS NULL OR article.id IS NOT NULL AS direct_article_valid,
            observation.story_id IS NULL OR story.id IS NOT NULL AS direct_story_valid,
            observation.signal_id IS NULL OR signal.id IS NOT NULL AS direct_signal_valid,
            observation.canonical_entity_id IS NULL OR entity.id IS NOT NULL AS direct_entity_valid,
            CASE WHEN observation.evidence ? 'article_ids' THEN
              CASE WHEN jsonb_typeof(observation.evidence->'article_ids') = 'array' THEN
              jsonb_array_length(observation.evidence->'article_ids') > 0 AND NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(observation.evidence->'article_ids') item(value)
                LEFT JOIN public.articles root ON root.id = CASE
                  WHEN jsonb_typeof(item.value) = 'number' AND item.value #>> '{}' ~ '^[1-9][0-9]*$'
                  THEN (item.value #>> '{}')::bigint END
                WHERE root.id IS NULL
              ) ELSE false END ELSE false END AS article_array_valid,
            CASE WHEN observation.evidence ? 'story_ids' THEN
              CASE WHEN jsonb_typeof(observation.evidence->'story_ids') = 'array' THEN
              jsonb_array_length(observation.evidence->'story_ids') > 0 AND NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(observation.evidence->'story_ids') item(value)
                LEFT JOIN public.stories root ON root.id = CASE
                  WHEN jsonb_typeof(item.value) = 'number' AND item.value #>> '{}' ~ '^[1-9][0-9]*$'
                  THEN (item.value #>> '{}')::bigint END
                WHERE root.id IS NULL
              ) ELSE false END ELSE false END AS story_array_valid,
            CASE WHEN observation.evidence ? 'signal_ids' THEN
              CASE WHEN jsonb_typeof(observation.evidence->'signal_ids') = 'array' THEN
              jsonb_array_length(observation.evidence->'signal_ids') > 0 AND NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(observation.evidence->'signal_ids') item(value)
                LEFT JOIN public.signals root ON root.id = CASE
                  WHEN jsonb_typeof(item.value) = 'number' AND item.value #>> '{}' ~ '^[1-9][0-9]*$'
                  THEN (item.value #>> '{}')::bigint END
                WHERE root.id IS NULL
              ) ELSE false END ELSE false END AS signal_array_valid,
            CASE WHEN observation.evidence ? 'entity_ids' THEN
              CASE WHEN jsonb_typeof(observation.evidence->'entity_ids') = 'array' THEN
              jsonb_array_length(observation.evidence->'entity_ids') > 0 AND NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(observation.evidence->'entity_ids') item(value)
                LEFT JOIN public.canonical_entities root ON root.id = CASE
                  WHEN jsonb_typeof(item.value) = 'string'
                   AND item.value #>> '{}' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
                  THEN (item.value #>> '{}')::uuid END
                WHERE root.id IS NULL
              ) ELSE false END ELSE false END AS entity_array_valid,
            CASE WHEN observation.evidence ? 'source_id' THEN
              jsonb_typeof(observation.evidence->'source_id') IN ('string','number')
              AND btrim(observation.evidence->>'source_id') <> '' ELSE false END AS source_id_valid,
            CASE WHEN observation.evidence ? 'source_record_id' THEN
              jsonb_typeof(observation.evidence->'source_record_id') IN ('string','number')
              AND btrim(observation.evidence->>'source_record_id') <> '' ELSE false END AS source_record_id_valid,
            observation.article_id IS NOT NULL AS has_direct_article,
            observation.story_id IS NOT NULL AS has_direct_story,
            observation.signal_id IS NOT NULL AS has_direct_signal,
            observation.canonical_entity_id IS NOT NULL AS has_direct_entity,
            observation.evidence ? 'article_ids' AS has_article_array,
            observation.evidence ? 'story_ids' AS has_story_array,
            observation.evidence ? 'signal_ids' AS has_signal_array,
            observation.evidence ? 'entity_ids' AS has_entity_array,
            observation.evidence ? 'source_id' AS has_source_id,
            observation.evidence ? 'source_record_id' AS has_source_record_id
          FROM public.radar_observations observation
          LEFT JOIN public.articles article ON article.id = observation.article_id
          LEFT JOIN public.stories story ON story.id = observation.story_id
          LEFT JOIN public.signals signal ON signal.id = observation.signal_id
          LEFT JOIN public.canonical_entities entity ON entity.id = observation.canonical_entity_id
        )
        SELECT count(*), count(*) FILTER (WHERE
          object_evidence
          AND direct_article_valid AND direct_story_valid AND direct_signal_valid AND direct_entity_valid
          AND (NOT has_article_array OR article_array_valid)
          AND (NOT has_story_array OR story_array_valid)
          AND (NOT has_signal_array OR signal_array_valid)
          AND (NOT has_entity_array OR entity_array_valid)
          AND (NOT has_source_id OR source_id_valid)
          AND (NOT has_source_record_id OR source_record_id_valid)
          AND (has_direct_article OR has_direct_story OR has_direct_signal OR has_direct_entity
               OR article_array_valid OR story_array_valid OR signal_array_valid OR entity_array_valid
               OR source_id_valid OR source_record_id_valid)
        )
        FROM validated
        """,
    )[0]
    linked = _execute(
        connection,
        """
        /* audit_trend_evidence */
        SELECT count(*), count(*) FILTER (WHERE
          evidence.role IN ('trigger','support','context','contradiction')
          AND evidence.contribution BETWEEN -1 AND 1
          AND (evidence.observation_id IS NOT NULL OR evidence.action_event_id IS NOT NULL
               OR evidence.article_id IS NOT NULL OR evidence.story_id IS NOT NULL
               OR evidence.signal_id IS NOT NULL OR evidence.canonical_entity_id IS NOT NULL)
          AND (evidence.observation_id IS NULL OR observation.id IS NOT NULL)
          AND (evidence.action_event_id IS NULL OR action.id IS NOT NULL)
          AND (evidence.article_id IS NULL OR article.id IS NOT NULL)
          AND (evidence.story_id IS NULL OR story.id IS NOT NULL)
          AND (evidence.signal_id IS NULL OR signal.id IS NOT NULL)
          AND (evidence.canonical_entity_id IS NULL OR entity.id IS NOT NULL)
          AND (NOT (evidence.evidence ? '_relation_only') OR (
            jsonb_typeof(evidence.evidence->'_relation_only') = 'boolean'
            AND evidence.evidence->>'_relation_only' = 'true'
            AND evidence.observation_id IS NOT NULL
            AND (evidence.story_id IS NOT NULL OR evidence.signal_id IS NOT NULL)
          ))
        ), count(*) FILTER (WHERE evidence.evidence ? '_relation_only'),
           count(*) FILTER (WHERE evidence.evidence ? '_relation_only'
             AND jsonb_typeof(evidence.evidence->'_relation_only') = 'boolean'
             AND evidence.evidence->>'_relation_only' = 'true'
             AND evidence.observation_id IS NOT NULL AND observation.id IS NOT NULL
             AND (evidence.story_id IS NOT NULL OR evidence.signal_id IS NOT NULL))
        FROM public.radar_trend_evidence evidence
        LEFT JOIN public.radar_observations observation ON observation.id = evidence.observation_id
        LEFT JOIN public.action_events action ON action.id = evidence.action_event_id
        LEFT JOIN public.articles article ON article.id = evidence.article_id
        LEFT JOIN public.stories story ON story.id = evidence.story_id
        LEFT JOIN public.signals signal ON signal.id = evidence.signal_id
        LEFT JOIN public.canonical_entities entity ON entity.id = evidence.canonical_entity_id
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
            "relation_only_total": int(linked[2] or 0),
            "relation_only_valid": int(linked[3] or 0),
            "status": "available",
        },
    }


def _coverage_suppression(connection, available: set[str]) -> dict[str, Any]:
    if not {"radar_observations", "radar_trends"}.issubset(available):
        return {
            "status": "unavailable",
            "observations_at_or_below_hard_gate": None,
            "trends_at_or_below_hard_gate": None,
            "collector_suppressed": None,
            "confirmed_below_hard_gate": None,
        }
    row = _execute(
        connection,
        """
        /* audit_coverage_suppression */
        SELECT
          (SELECT count(*) FROM public.radar_observations WHERE coverage_confidence <= 0.5),
          count(*) FILTER (WHERE coverage_confidence <= 0.5),
          count(*) FILTER (WHERE coverage_confidence <= 0.5 AND state <> 'confirmed'),
          count(*) FILTER (WHERE coverage_confidence <= 0.5 AND state = 'confirmed')
        FROM public.radar_trends
        """,
    )[0]
    return {
        "status": "available",
        "observations_at_or_below_hard_gate": int(row[0] or 0),
        "trends_at_or_below_hard_gate": int(row[1] or 0),
        "collector_suppressed": int(row[2] or 0),
        "confirmed_below_hard_gate": int(row[3] or 0),
    }


def _t0_sanity(connection, available: set[str]) -> dict[str, Any]:
    if not {"radar_trends", "radar_t0_revisions"}.issubset(available):
        return {"status": "unavailable", "invalid_trends": None, "invalid_revisions": None, "invalid": None}
    trend_invalid = int(
        _scalar(
            connection,
            """
            /* audit_t0_trends */
            SELECT count(*) FROM public.radar_trends
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
            SELECT count(*) FROM public.radar_t0_revisions
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
        f"SELECT public_id::text AS public_id FROM {_public_table(table)}" for table in tables
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
             FROM public.notification_events
             GROUP BY trend_id, transition_id, channel, audience_key HAVING count(*) > 1
           ) duplicate_keys),
          count(*) FILTER (WHERE trend.id IS NULL OR transition.id IS NULL
                            OR transition.trend_id <> notification.trend_id)
        FROM public.notification_events notification
        LEFT JOIN public.radar_trends trend ON trend.id = notification.trend_id
        LEFT JOIN public.radar_state_events transition ON transition.public_id = notification.transition_id
        """,
    )[0]
    return {
        "status": "available",
        "duplicate_delivery_keys": int(row[0] or 0),
        "invalid_references": int(row[1] or 0),
    }


def _write_activity_snapshot(
    connection,
    table_counts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    # This clears only the current backend's cached view of cumulative stats;
    # it does not reset or mutate PostgreSQL counters.
    _execute(connection, "/* audit_stats_snapshot_clear */ SELECT pg_catalog.pg_stat_clear_snapshot()")
    reset_identity = _scalar(
        connection,
        """
        /* audit_stats_reset_identity */
        SELECT COALESCE(database_stats.stats_reset, pg_catalog.pg_postmaster_start_time())
        FROM pg_catalog.pg_stat_database database_stats
        WHERE database_stats.datname = pg_catalog.current_database()
        """,
    )
    reset_text = _utc_precise_text(reset_identity) if isinstance(reset_identity, datetime) else None
    snapshot: dict[str, Any] = {}
    for table in WRITE_SNAPSHOT_TABLES:
        counted = table_counts.get(table, {})
        if counted.get("status") != "available":
            snapshot[table] = {
                "status": "unavailable",
                "count": counted.get("count"),
                "stats": None,
            }
            continue
        row = _execute(
            connection,
            """
            /* audit_table_write_stats */
            SELECT n_tup_ins, n_tup_upd, n_tup_del
            FROM pg_catalog.pg_stat_user_tables
            WHERE schemaname = 'public' AND relname = %s
            """,
            (table,),
        )
        if not row or reset_text is None or any(value is None for value in row[0]):
            snapshot[table] = {
                "status": "stats_unavailable",
                "count": counted.get("count"),
                "stats": None,
            }
            continue
        inserted, updated, deleted = (int(value) for value in row[0])
        snapshot[table] = {
            "status": "available",
            "count": counted.get("count"),
            "stats": {
                "reset_identity": reset_text,
                "n_tup_ins": inserted,
                "n_tup_upd": updated,
                "n_tup_del": deleted,
            },
        }
    return snapshot


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
    before = baseline_report.get("write_activity")
    if not isinstance(before, Mapping):
        return {
            "status": "invalid_baseline",
            "input": {"schema_version": baseline_report.get("schema_version"), "phase": baseline_report.get("phase")},
            "before": None,
            "after": dict(snapshot),
            "changed_tables": list(WRITE_SNAPSHOT_TABLES),
        }
    changed = [table for table in WRITE_SNAPSHOT_TABLES if before.get(table) != snapshot.get(table)]
    before_resets = {
        value.get("stats", {}).get("reset_identity")
        for value in before.values()
        if isinstance(value, Mapping) and isinstance(value.get("stats"), Mapping)
    }
    after_resets = {
        value.get("stats", {}).get("reset_identity")
        for value in snapshot.values()
        if isinstance(value, Mapping) and isinstance(value.get("stats"), Mapping)
    }
    reset_changed = before_resets != after_resets
    return {
        "status": "unchanged" if not changed and not reset_changed else "changed",
        "input": {"schema_version": baseline_report.get("schema_version"), "phase": baseline_report.get("phase")},
        "before": dict(before),
        "after": dict(snapshot),
        "changed_tables": changed,
        "reset_identity_changed": reset_changed,
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
        protected_tables = {
            table: _audit_protected_table(connection, table)
            for table in PROTECTED_TABLES
        }
        radar_tables = {table: _audit_count_table(connection, table) for table in RADAR_TABLES}
        available = {
            table for table, result in radar_tables.items() if result.get("status") == "available"
        }
        if phase == "shadow" and replay_report is not None:
            replay_metrics = _replay_metrics(replay_report, evidence_minimum)
            observation_count = replay_metrics["observation_count"]
            trend_count = replay_metrics["trend_count"]
            country_trend_count = replay_metrics["country_trend_count"]
            meta_trend_count = replay_metrics["meta_trend_count"]
            state_distribution = replay_metrics["state_distribution"]
            contour_completeness = replay_metrics["contour_completeness"]
            evidence = replay_metrics["evidence"]
            coverage_suppression = replay_metrics["coverage_suppression"]
            t0_sanity = replay_metrics["t0_sanity"]
        else:
            evidence = _database_evidence(connection, available, evidence_minimum)
            observation_count = radar_tables["radar_observations"]["count"]
            trend_count = radar_tables["radar_trends"]["count"]
            state_distribution = _state_distribution(connection, available)
            country_states = state_distribution.get("country")
            meta_states = state_distribution.get("meta")
            country_trend_count = sum(country_states.values()) if isinstance(country_states, Mapping) else None
            meta_trend_count = sum(meta_states.values()) if isinstance(meta_states, Mapping) else None
            contour_completeness = _contour_completeness(connection, available)
            coverage_suppression = _coverage_suppression(connection, available)
            t0_sanity = _t0_sanity(connection, available)
        table_counts = {
            **{table: {"status": value["status"], "count": value["count"]} for table, value in protected_tables.items()},
            **{table: dict(value) for table, value in radar_tables.items()},
        }
        write_activity = _write_activity_snapshot(connection, table_counts)
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
                "country_trend_count": country_trend_count,
                "meta_trend_count": meta_trend_count,
                "state_distribution": state_distribution,
                "contour_completeness": contour_completeness,
                "evidence": evidence,
                "coverage_suppression": coverage_suppression,
                "t0_sanity": t0_sanity,
                "duplicate_public_ids": _duplicate_public_ids(connection, available),
                "notification_idempotency": _notification_idempotency(connection, available),
            },
            "write_activity": write_activity,
            "public_get_write_verification": _public_get_verification(
                write_activity, public_get_before
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
    contour_minimum: float = 0.80,
) -> dict[str, Any]:
    """Evaluate pure report data without requiring a live database."""

    failures: list[dict[str, Any]] = []
    if current.get("schema_version") != SCHEMA_VERSION:
        failures.append(_failure("unsupported_schema", "Audit report schema is not supported."))

    phase = current.get("phase")
    if phase not in {"before", "shadow", "after"}:
        failures.append(_failure("invalid_phase", "Audit report phase is invalid.", phase=phase))
    if phase in {"shadow", "after"} and before is None:
        failures.append(_failure("authoritative_before_required", "This phase requires an authoritative before report."))

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
        expected_count = expected.get("count")
        actual_count = actual.get("count")
        if not _is_count(expected_count) or not _is_count(actual_count):
            failures.append(_failure("protected_count_unavailable", f"Protected count is unavailable for {table}.", table=table))
            continue
        if actual_count < expected_count:
            failures.append(
                _failure("protected_count_decreased", f"Protected row count decreased for {table}.", table=table, before=expected_count, after=actual_count)
            )
        elif actual_count > expected_count:
            failures.append(
                _failure("protected_count_increased", f"Protected row count increased for {table} while writers were quiesced.", table=table, before=expected_count, after=actual_count)
            )
        identity = actual.get("primary_key") if isinstance(actual.get("primary_key"), Mapping) else {}
        expected_identity = expected.get("primary_key") if isinstance(expected.get("primary_key"), Mapping) else {}
        if (
            actual_count != expected_count
            or identity.get("columns") != expected_identity.get("columns")
            or not isinstance(identity.get("fingerprint"), str)
            or identity.get("fingerprint") != expected_identity.get("fingerprint")
        ):
            failures.append(
                _failure("protected_primary_key_changed", f"Protected primary-key identity changed for {table}.", table=table)
            )

    migrations = current.get("migrations", {})
    if migrations.get("status") != "complete" or migrations.get("missing_required") != []:
        failures.append(
            _failure("required_migration_missing", "Required Radar migrations are not recorded by filename.", missing=migrations.get("missing_required"))
        )

    radar = current.get("radar", {})
    for table in RADAR_TABLES:
        status = radar.get("tables", {}).get(table, {}).get("status")
        if status != "available":
            failures.append(
                _failure("radar_table_unavailable", f"Radar table {table} is unavailable.", table=table, status=status)
            )

    write_activity = current.get("write_activity", {})
    for table in WRITE_SNAPSHOT_TABLES:
        activity = write_activity.get(table, {}) if isinstance(write_activity, Mapping) else {}
        if activity.get("status") != "available" or not isinstance(activity.get("stats"), Mapping):
            failures.append(
                _failure("write_activity_unavailable", f"PostgreSQL write counters are unavailable for {table}.", table=table)
            )

    if phase == "after" and before is not None:
        before_activity = before.get("write_activity", {})
        changed_protected = sorted(
            table
            for table in PROTECTED_TABLES
            if not isinstance(before_activity, Mapping)
            or before_activity.get(table) != write_activity.get(table)
        )
        if changed_protected:
            failures.append(
                _failure(
                    "protected_write_activity_changed",
                    "Bounded Radar apply changed a protected table or its PostgreSQL stats identity.",
                    changed_tables=changed_protected,
                )
            )

    if phase == "shadow" and before is not None:
        before_radar_tables = before.get("radar", {}).get("tables", {})
        current_radar_tables = radar.get("tables", {})
        before_activity = before.get("write_activity", {})
        changed_tables = sorted({
            table
            for table in WRITE_SNAPSHOT_TABLES
            if not isinstance(before_activity, Mapping)
            or before_activity.get(table) != write_activity.get(table)
        } | {
            table
            for table in RADAR_TABLES
            if before_radar_tables.get(table) != current_radar_tables.get(table)
        })
        if changed_tables:
            failures.append(
                _failure(
                    "shadow_replay_wrote_rows",
                    "Shadow replay changed database row counts or PostgreSQL write counters.",
                    changed_tables=changed_tables,
                )
            )

    if phase in {"shadow", "after"}:
        observations = radar.get("evidence", {}).get("observations", {})
        total = observations.get("total")
        valid = observations.get("valid")
        invalid = observations.get("invalid")
        ratio = observations.get("ratio")
        if observations.get("status") != "available" or not all(_is_count(value) for value in (total, valid, invalid)) or not _is_ratio(ratio):
            failures.append(_failure("evidence_unavailable", "Evidence completeness or validity is unavailable."))
        elif total == 0 or total != valid + invalid or abs(ratio - valid / total) > 0.000001 or ratio < evidence_minimum:
            failures.append(
                _failure("evidence_completeness_below_gate", "Evidence completeness is below the configured gate.", actual=ratio, minimum=evidence_minimum, total=observations.get("total"))
            )
        trend_links = radar.get("evidence", {}).get("trend_links", {})
        link_invalid = trend_links.get("invalid")
        if trend_links.get("status") != "available" or not _is_count(link_invalid):
            failures.append(_failure("evidence_validity_unavailable", "Trend evidence validity is unavailable."))
        elif link_invalid > 0:
            failures.append(
                _failure("evidence_validity_failed", "Persisted trend evidence contains invalid roots.", invalid=trend_links.get("invalid"))
            )
        coverage = radar.get("coverage_suppression", {})
        confirmed_below = coverage.get("confirmed_below_hard_gate")
        if coverage.get("status") != "available" or not _is_count(confirmed_below):
            failures.append(_failure("coverage_suppression_unavailable", "Coverage suppression metrics are unavailable."))
        elif confirmed_below > 0:
            failures.append(
                _failure("coverage_suppression_failed", "A confirmed trend bypassed the coverage hard gate.", count=coverage.get("confirmed_below_hard_gate"))
            )
        duplicates = radar.get("duplicate_public_ids", {})
        duplicate_count = duplicates.get("duplicates")
        if duplicates.get("status", "available") != "available" or not _is_count(duplicate_count):
            failures.append(_failure("duplicate_public_ids_unavailable", "Duplicate public-ID metrics are unavailable."))
        elif duplicate_count > 0:
            failures.append(
                _failure("duplicate_public_ids", "Duplicate Radar public IDs were found.", count=duplicates.get("duplicates"))
            )
        t0 = radar.get("t0_sanity", {})
        t0_invalid = t0.get("invalid")
        if t0.get("status", "available") != "available" or not _is_count(t0_invalid):
            failures.append(_failure("t0_sanity_unavailable", "T0 sanity metrics are unavailable."))
        elif t0_invalid > 0:
            failures.append(
                _failure("t0_invariant_failed", "T0 chronology invariants failed.", count=t0.get("invalid"))
            )
        notification = radar.get("notification_idempotency", {})
        duplicate_keys = notification.get("duplicate_delivery_keys")
        invalid_references = notification.get("invalid_references")
        if notification.get("status", "available") != "available" or not _is_count(duplicate_keys) or not _is_count(invalid_references):
            failures.append(_failure("notification_idempotency_unavailable", "Notification idempotency metrics are unavailable."))
        elif duplicate_keys > 0 or invalid_references > 0:
            failures.append(
                _failure("notification_idempotency_failed", "Notification delivery identities are not idempotent and valid.", duplicate_delivery_keys=notification.get("duplicate_delivery_keys"), invalid_references=notification.get("invalid_references"))
            )

        contour = radar.get("contour_completeness", {})
        possible = contour.get("possible_pairs")
        linked = contour.get("linked_pairs")
        contour_ratio = contour.get("ratio")
        if contour.get("status") == "not_applicable_no_pairs" and possible == 0 and linked == 0 and contour_ratio is None:
            pass
        elif contour.get("status") != "available" or not _is_count(possible) or not _is_count(linked) or not _is_ratio(contour_ratio):
            failures.append(_failure("contour_completeness_unavailable", "Contour completeness is unavailable or inconsistent."))
        elif possible == 0 or linked > possible or abs(contour_ratio - linked / possible) > 0.000001:
            failures.append(_failure("contour_completeness_invalid", "Contour numerator and denominator are inconsistent."))
        elif contour_ratio < contour_minimum:
            failures.append(_failure("contour_completeness_below_gate", "Contour completeness is below the configured gate.", actual=contour_ratio, minimum=contour_minimum))

    public_get = current.get("public_get_write_verification", {})
    if phase == "after" and public_get.get("status") != "unchanged":
        failures.append(
            _failure("public_get_wrote_rows", "Public GET smoke changed row identity or PostgreSQL write counters.", changed_tables=public_get.get("changed_tables"))
        )

    return {"passed": not failures, "failure_count": len(failures), "failures": failures}


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_ratio(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= float(value) <= 1


def _load_report(path: str | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"report must contain a JSON object: {path}")
    return payload


def _validate_audit_report(report: Mapping[str, Any] | None, label: str) -> None:
    if not isinstance(report, Mapping):
        raise ValueError(f"{label} report is required")
    if report.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"{label} report schema_version is incompatible")
    if report.get("phase") != "before":
        raise ValueError(f"{label} report must have phase=before")


def validate_phase_reports(
    phase: str,
    *,
    compare_report: Mapping[str, Any] | None,
    replay_report: Mapping[str, Any] | None,
    public_get_before: Mapping[str, Any] | None,
) -> None:
    if phase == "before":
        if compare_report is not None or replay_report is not None or public_get_before is not None:
            raise ValueError("phase=before is capture-only and accepts no report inputs")
        return
    _validate_audit_report(compare_report, "compare")
    if phase == "shadow":
        if public_get_before is not None:
            raise ValueError("phase=shadow does not accept a public GET baseline")
        if replay_report is None:
            raise ValueError("phase=shadow requires a replay report")
        validate_replay_report(replay_report)
        return
    if phase == "after":
        if replay_report is not None:
            raise ValueError("phase=after does not accept a replay report")
        _validate_audit_report(public_get_before, "public GET baseline")
        return
    raise ValueError("unknown audit phase")


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
    parser.add_argument(
        "--contour-minimum",
        type=_evidence_minimum,
        default=_evidence_minimum(os.environ.get("RADAR_CONTOUR_COMPLETENESS_GATE", "0.80")),
    )
    args = parser.parse_args(argv)
    if args.phase == "before" and (args.compare or args.replay_report or args.public_get_before):
        parser.error("--phase before is capture-only")
    if args.phase == "shadow":
        if not args.compare or not args.replay_report:
            parser.error("--phase shadow requires --compare and --replay-report")
        if args.public_get_before:
            parser.error("--phase shadow does not accept --public-get-before")
    if args.phase == "after":
        if not args.compare or not args.public_get_before:
            parser.error("--phase after requires --compare and --public-get-before")
        if args.replay_report:
            parser.error("--phase after does not accept --replay-report")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.umask(0o077)
    try:
        before = _load_report(args.compare)
        replay = _load_report(args.replay_report)
        public_before = _load_report(args.public_get_before)
        validate_phase_reports(
            args.phase,
            compare_report=before,
            replay_report=replay,
            public_get_before=public_before,
        )
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
        comparison = compare_reports(
            before,
            report,
            evidence_minimum=args.evidence_minimum,
            contour_minimum=args.contour_minimum,
        )
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
