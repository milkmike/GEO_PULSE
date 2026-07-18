"""Read-only public API for persisted Early Warning Radar results."""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated, Any, Protocol
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BeforeValidator, WithJsonSchema
from sqlalchemy import text

from src.api.public_urls import safe_public_url
from src.db import SessionLocal
from src.radar.service import DETECTOR_VERSION


router = APIRouter(prefix="/api/v2", tags=["radar"])

_CURSOR_VERSION = 1
_STATES = frozenset({"candidate", "emerging", "confirmed", "cooling", "resolved", "rejected"})
_PUBLIC_FILTER_STATES = frozenset({"emerging", "confirmed", "cooling", "resolved"})
_CONTOURS = frozenset({"media", "action"})
_STATE_RANK = {"confirmed": 0, "emerging": 1, "cooling": 2, "candidate": 3, "resolved": 4, "rejected": 5}
RADAR_METHODOLOGY_UPDATED_AT = datetime(2026, 7, 18, tzinfo=timezone.utc)
_BIGINT_MAX = 2**63 - 1
_INTEGER_MAX = 2**31 - 1
_PUBLIC_EVIDENCE_PREDICATE = (
    "(evidence.evidence->>'_relation_only' IS DISTINCT FROM 'true')"
)


def _canonical_query_id(value: Any, *, maximum: int, field: str) -> int:
    """Validate an ID's raw query representation before integer coercion."""

    if type(value) is int:
        parsed = value
    elif isinstance(value, str) and re.fullmatch(r"[1-9][0-9]*", value):
        parsed = int(value)
    else:
        raise ValueError(f"{field} must be a canonical unsigned decimal")
    if not 1 <= parsed <= maximum:
        raise ValueError(f"{field} is outside its supported range")
    return parsed


def _story_query_id(value: Any) -> int:
    return _canonical_query_id(value, maximum=_BIGINT_MAX, field="story_id")


def _signal_query_id(value: Any) -> int:
    return _canonical_query_id(value, maximum=_INTEGER_MAX, field="signal_id")


StoryQueryId = Annotated[
    int,
    BeforeValidator(_story_query_id),
    WithJsonSchema({"type": "integer", "minimum": 1, "maximum": _BIGINT_MAX}),
]
SignalQueryId = Annotated[
    int,
    BeforeValidator(_signal_query_id),
    WithJsonSchema({"type": "integer", "minimum": 1, "maximum": _INTEGER_MAX}),
]


@dataclass(frozen=True)
class RadarFilters:
    state: str | None = None
    contour: str | None = None
    country: str | None = None
    story_id: int | None = None
    signal_id: int | None = None

    def binding(self) -> dict[str, str | int | None]:
        return {
            "state": self.state,
            "contour": self.contour,
            "country": self.country,
            "story_id": self.story_id,
            "signal_id": self.signal_id,
        }


class RadarReadService(Protocol):
    """Persisted read-model boundary used by public GET handlers."""

    def list_trends(self, *, filters: RadarFilters, cursor: dict[str, Any] | None, limit: int) -> dict[str, Any]: ...
    def trend(self, public_id: UUID) -> dict[str, Any] | None: ...
    def country_trends(self, country_code: str, *, filters: RadarFilters, cursor: dict[str, Any] | None, limit: int) -> dict[str, Any]: ...
    def timeline(self, public_id: UUID) -> dict[str, Any] | None: ...
    def evidence(self, public_id: UUID, *, cursor: dict[str, Any] | None, limit: int) -> dict[str, Any] | None: ...
    def coverage(self) -> dict[str, Any]: ...


@contextmanager
def radar_read_session():
    """Open a dedicated public-read session that can never commit a request."""

    session = SessionLocal()
    try:
        yield session
    finally:
        try:
            session.rollback()
        finally:
            session.close()


def _value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping.get(name, default)
    return getattr(row, name, default)


def _json_object(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return default
    return value if isinstance(value, type(default)) else default


def _is_absolute_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        return bool(parsed.scheme or parsed.netloc)
    except ValueError:
        # Malformed absolute-looking values still pass through safe_public_url.
        return ":" in value


def sanitize_persisted_json(value: Any, *, field_name: str | None = None) -> Any:
    """Recursively retain only safe public URLs from persisted JSON evidence."""

    if isinstance(value, dict):
        return {
            key: sanitize_persisted_json(item, field_name=str(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_persisted_json(item, field_name=field_name) for item in value]
    is_url_field = field_name is not None and any(
        marker in field_name.lower() for marker in ("url", "href", "link")
    )
    if isinstance(value, str) and (is_url_field or _is_absolute_url(value)):
        return safe_public_url(value)
    return value


def _as_iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value is not None else None)


def _serialize_country_wave(row: Any) -> dict[str, Any]:
    return {
        "public_id": str(_value(row, "public_id")),
        "country_code": _value(row, "country_code"),
        "contour": _value(row, "contour"),
        "state": _value(row, "state"),
        "confidence": _number(_value(row, "confidence")),
        "coverage_confidence": _number(_value(row, "coverage_confidence")),
        "velocity": _number(_value(row, "velocity")),
        "first_observed_at": _as_iso(_value(row, "first_observed_at")),
        "detected_at": _as_iso(_value(row, "detected_at")),
        "confirmed_at": _as_iso(_value(row, "confirmed_at")),
        "t0_auto": _as_iso(_value(row, "t0_auto")),
        "t0_effective": _as_iso(_value(row, "t0_effective")),
    }


def _serialize_timeline_item(item: Any) -> dict[str, Any]:
    evidence = sanitize_persisted_json(
        _json_object(_value(item, "evidence"), {})
    )
    revision_kind = _value(item, "revision_kind")
    if revision_kind in {"automatic", "analyst"}:
        evidence = {**evidence, "revision_kind": revision_kind}
    return {
        "kind": _value(item, "kind"),
        "at": _as_iso(_value(item, "at")),
        "state": _value(item, "state"),
        "contour": _value(item, "contour"),
        "evidence": evidence,
    }


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None


def _contours(value: Any) -> dict[str, dict[str, Any]]:
    stored = _json_object(value, {})
    result: dict[str, dict[str, Any]] = {}
    for contour in ("media", "action"):
        raw = stored.get(contour) if isinstance(stored, dict) else None
        if not isinstance(raw, dict):
            result[contour] = {"state": "insufficient", "status": "insufficient"}
            continue
        state = raw.get("state") if raw.get("state") in _STATES else "insufficient"
        status = raw.get("status") if raw.get("status") in {"aligned", "divergent", "insufficient"} else "insufficient"
        result[contour] = {"state": state, "status": status}
    return result


def _serialize_evidence_preview(value: Any) -> dict[str, Any] | None:
    preview = _json_object(value, {})
    if not preview:
        return None
    return {
        "public_id": str(preview["public_id"]) if preview.get("public_id") else None,
        "role": preview.get("role"),
        "title": preview.get("title"),
        "url": safe_public_url(preview.get("url")),
    }


def serialize_trend(row: Any) -> dict[str, Any]:
    """Serialize saved trend rows without deriving a new detector result."""

    waves = _json_object(_value(row, "country_waves"), [])
    return {
        "public_id": str(_value(row, "public_id")),
        "scope": _value(row, "scope"),
        "state": _value(row, "state"),
        "thesis": _value(row, "title_ru"),
        "subject_key": _value(row, "subject_key"),
        "direction": _value(row, "direction"),
        "confidence": _number(_value(row, "confidence")),
        "coverage_confidence": _number(_value(row, "coverage_confidence")),
        "velocity": _number(_value(row, "velocity")),
        "first_observed_at": _as_iso(_value(row, "first_observed_at")),
        "detected_at": _as_iso(_value(row, "detected_at")),
        "confirmed_at": _as_iso(_value(row, "confirmed_at")),
        "t0_auto": _as_iso(_value(row, "t0_auto")),
        "t0_effective": _as_iso(_value(row, "t0_effective")),
        "country_code": _value(row, "country_code"),
        "country_waves": [_serialize_country_wave(item) for item in waves],
        "contours": _contours(_value(row, "contours")),
        "contradiction_marker": bool(_value(row, "contradiction_marker", False)),
        "evidence_preview": _serialize_evidence_preview(_value(row, "evidence_preview")),
        "why_included": _value(row, "why_included") or "prioritized_by_state_and_velocity",
    }


def _validate_filters(
    state: str | None,
    contour: str | None,
    country: str | None,
    story_id: int | None = None,
    signal_id: int | None = None,
) -> RadarFilters:
    normalized_state = state.strip().lower() if state else None
    normalized_contour = contour.strip().lower() if contour else None
    normalized_country = country.strip().upper() if country else None
    if normalized_state and normalized_state not in _PUBLIC_FILTER_STATES:
        raise HTTPException(status_code=422, detail="invalid radar state")
    if normalized_contour and normalized_contour not in _CONTOURS:
        raise HTTPException(status_code=422, detail="invalid radar contour")
    if normalized_country and len(normalized_country) != 2 or normalized_country and not normalized_country.isalpha():
        raise HTTPException(status_code=422, detail="invalid radar country")
    if story_id is not None and (type(story_id) is not int or story_id < 1):
        raise HTTPException(status_code=422, detail="invalid radar story ID")
    if signal_id is not None and (type(signal_id) is not int or signal_id < 1):
        raise HTTPException(status_code=422, detail="invalid radar signal ID")
    return RadarFilters(
        normalized_state,
        normalized_contour,
        normalized_country,
        story_id,
        signal_id,
    )


def _encode_cursor(*, scope: str, binding: dict[str, Any], key: dict[str, Any]) -> str:
    payload = {"v": _CURSOR_VERSION, "scope": scope, "binding": binding, "key": key}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_as_iso).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(token: str, *, scope: str, binding: dict[str, Any], validator) -> dict[str, Any]:
    try:
        raw = base64.b64decode(token + "=" * (-len(token) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"v", "scope", "binding", "key"}:
            raise ValueError
        if payload["v"] != _CURSOR_VERSION or payload["scope"] != scope or payload["binding"] != binding:
            raise ValueError
        return validator(payload["key"])
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid radar cursor") from exc


def _list_cursor_key(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"state_rank", "velocity", "first_observed_at", "public_id"}:
        raise ValueError
    if type(value["state_rank"]) is not int or value["state_rank"] not in set(_STATE_RANK.values()):
        raise ValueError
    if type(value["velocity"]) not in {int, float} or not math.isfinite(float(value["velocity"])):
        raise ValueError
    timestamp = datetime.fromisoformat(value["first_observed_at"])
    if timestamp.tzinfo is None:
        raise ValueError
    UUID(value["public_id"])
    return value


def _evidence_cursor_key(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"id"} or type(value["id"]) is not int or value["id"] < 1:
        raise ValueError
    return value


class SqlRadarReadService:
    """Read-only SQL adapter over the immutable Radar persistence tables."""

    def list_trends(self, *, filters: RadarFilters, cursor: dict[str, Any] | None, limit: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "state": filters.state,
            "public_states": ["emerging", "confirmed", "cooling"],
            "contour": filters.contour,
            "country": filters.country,
            "story_id": filters.story_id,
            "signal_id": filters.signal_id,
            "limit": limit + 1,
        }
        cursor_sql = ""
        if cursor:
            params.update({"cursor_state_rank": cursor["state_rank"], "cursor_velocity": cursor["velocity"], "cursor_first_observed_at": cursor["first_observed_at"], "cursor_public_id": cursor["public_id"]})
            cursor_sql = """
              AND (state_rank > :cursor_state_rank
                OR (state_rank = :cursor_state_rank AND velocity < :cursor_velocity)
                OR (state_rank = :cursor_state_rank AND velocity = :cursor_velocity AND first_observed_at < CAST(:cursor_first_observed_at AS timestamptz))
                OR (state_rank = :cursor_state_rank AND velocity = :cursor_velocity AND first_observed_at = CAST(:cursor_first_observed_at AS timestamptz) AND public_id > CAST(:cursor_public_id AS uuid)))
            """
        with radar_read_session() as session:
            rows = session.execute(text(f"""
                WITH ranked AS (
                  SELECT trend.*, CASE trend.state
                    WHEN 'confirmed' THEN 0 WHEN 'emerging' THEN 1 WHEN 'cooling' THEN 2
                    WHEN 'candidate' THEN 3 WHEN 'resolved' THEN 4 ELSE 5 END AS state_rank
                  FROM radar_trends trend
                  WHERE trend.scope = 'meta'
                    AND ((:state IS NOT NULL AND trend.state = :state)
                      OR (:state IS NULL AND trend.state = ANY(:public_states)))
                    AND (:contour IS NULL OR EXISTS (
                      SELECT 1 FROM radar_trend_members member JOIN radar_trends wave ON wave.id = member.country_trend_id
                      WHERE member.meta_trend_id = trend.id AND member.left_at IS NULL AND wave.contour = :contour))
                    AND (:country IS NULL OR EXISTS (
                      SELECT 1 FROM radar_trend_members member JOIN radar_trends wave ON wave.id = member.country_trend_id
                      WHERE member.meta_trend_id = trend.id AND member.left_at IS NULL AND wave.country_code = :country))
                    /* radar_related_story */
                    AND (:story_id IS NULL OR EXISTS (
                      SELECT 1 FROM radar_trend_evidence related
                      WHERE related.story_id = :story_id
                        AND (related.trend_id = trend.id OR EXISTS (
                          SELECT 1 FROM radar_trend_members related_member
                          WHERE related_member.meta_trend_id = trend.id
                            AND related_member.country_trend_id = related.trend_id
                            AND related_member.left_at IS NULL))))
                    /* radar_related_signal */
                    AND (:signal_id IS NULL OR EXISTS (
                      SELECT 1 FROM radar_trend_evidence related
                      WHERE related.signal_id = :signal_id
                        AND (related.trend_id = trend.id OR EXISTS (
                          SELECT 1 FROM radar_trend_members related_member
                          WHERE related_member.meta_trend_id = trend.id
                            AND related_member.country_trend_id = related.trend_id
                            AND related_member.left_at IS NULL))))
                )
                SELECT ranked.*, {self._wave_json("ranked.id")} AS country_waves,
                       {self._contour_json("ranked.id")} AS contours,
                       {self._contradiction_sql("ranked.id", include_active_members=True)} AS contradiction_marker,
                       {self._preview_json("ranked.id", include_active_members=True)} AS evidence_preview,
                       'prioritized_by_state_and_velocity' AS why_included
                FROM ranked
                WHERE TRUE {cursor_sql}
                ORDER BY state_rank ASC, velocity DESC, first_observed_at DESC, public_id ASC
                LIMIT :limit
            """), params).fetchall()
        return self._page(rows, limit)

    @staticmethod
    def _wave_json(trend_id: str) -> str:
        return f"""COALESCE((SELECT jsonb_agg(jsonb_build_object(
              'public_id', wave.public_id, 'country_code', wave.country_code, 'contour', wave.contour,
              'state', wave.state, 'confidence', wave.confidence, 'coverage_confidence', wave.coverage_confidence,
              'velocity', wave.velocity, 'first_observed_at', wave.first_observed_at, 'detected_at', wave.detected_at,
              'confirmed_at', wave.confirmed_at, 't0_auto', wave.t0_auto, 't0_effective', wave.t0_effective)
              ORDER BY wave.first_observed_at, wave.public_id)
            FROM radar_trend_members member JOIN radar_trends wave ON wave.id = member.country_trend_id
            WHERE member.meta_trend_id = {trend_id} AND member.left_at IS NULL), '[]'::jsonb)"""

    @staticmethod
    def _contour_json(trend_id: str) -> str:
        return f"""COALESCE((SELECT jsonb_object_agg(contour, jsonb_build_object('state', state, 'status', status))
            FROM (SELECT DISTINCT ON (wave.contour) wave.contour, wave.state,
                  COALESCE(link.status, 'insufficient') AS status
                  FROM radar_trend_members member JOIN radar_trends wave ON wave.id = member.country_trend_id
                  LEFT JOIN radar_contour_links link ON link.media_trend_id = wave.id OR link.action_trend_id = wave.id
                  WHERE member.meta_trend_id = {trend_id} AND member.left_at IS NULL
                  ORDER BY wave.contour, CASE wave.state WHEN 'confirmed' THEN 0 WHEN 'emerging' THEN 1 ELSE 2 END, wave.updated_at DESC) contour_rows), '{{}}'::jsonb)"""

    @staticmethod
    def _evidence_scope_sql(trend_id: str, *, include_active_members: bool) -> str:
        direct = f"evidence.trend_id = {trend_id}"
        if not include_active_members:
            return direct
        return f"""({direct} OR EXISTS (
              SELECT 1 FROM radar_trend_members evidence_member
              WHERE evidence_member.meta_trend_id = {trend_id}
                AND evidence_member.country_trend_id = evidence.trend_id
                AND evidence_member.left_at IS NULL))"""

    @classmethod
    def _contradiction_sql(cls, trend_id: str, *, include_active_members: bool) -> str:
        scope = cls._evidence_scope_sql(
            trend_id,
            include_active_members=include_active_members,
        )
        return f"""EXISTS (SELECT 1 FROM radar_trend_evidence evidence
            WHERE {scope} AND evidence.role = 'contradiction'
              AND {_PUBLIC_EVIDENCE_PREDICATE})"""

    @classmethod
    def _preview_json(cls, trend_id: str, *, include_active_members: bool) -> str:
        scope = cls._evidence_scope_sql(
            trend_id,
            include_active_members=include_active_members,
        )
        return f"""(SELECT jsonb_build_object('public_id', evidence.public_id, 'role', evidence.role,
                   'title', COALESCE(article.title, observation.evidence->>'title', event.details->>'title'),
                   'url', COALESCE(article.resolved_url, article.url, observation.evidence->>'url', event.evidence->>'url'))
            FROM radar_trend_evidence evidence
            LEFT JOIN radar_observations observation ON observation.id = evidence.observation_id
            LEFT JOIN action_events event ON event.id = evidence.action_event_id
            LEFT JOIN articles article ON article.id = COALESCE(evidence.article_id, observation.article_id)
            WHERE {scope} AND evidence.role IN ('trigger', 'support')
              AND {_PUBLIC_EVIDENCE_PREDICATE}
            ORDER BY CASE evidence.role WHEN 'trigger' THEN 0 ELSE 1 END, evidence.id LIMIT 1)"""

    def _page(self, rows: list[Any], limit: int) -> dict[str, Any]:
        items = list(rows[:limit])
        next_key = None
        if len(rows) > limit and items:
            last = items[-1]
            next_key = {"state_rank": int(_value(last, "state_rank")), "velocity": float(_value(last, "velocity")), "first_observed_at": _as_iso(_value(last, "first_observed_at")), "public_id": str(_value(last, "public_id"))}
        return {"items": items, "next_key": next_key}

    def trend(self, public_id: UUID) -> dict[str, Any] | None:
        with radar_read_session() as session:
            row = session.execute(text(f"""
                SELECT trend.*, {self._wave_json('trend.id')} AS country_waves,
                       CASE WHEN trend.scope = 'country' THEN jsonb_build_object(trend.contour, jsonb_build_object('state', trend.state, 'status', 'insufficient'))
                            ELSE {self._contour_json('trend.id')} END AS contours,
                       CASE WHEN trend.scope = 'meta'
                            THEN {self._contradiction_sql('trend.id', include_active_members=True)}
                            ELSE {self._contradiction_sql('trend.id', include_active_members=False)} END AS contradiction_marker,
                       CASE WHEN trend.scope = 'meta'
                            THEN {self._preview_json('trend.id', include_active_members=True)}
                            ELSE {self._preview_json('trend.id', include_active_members=False)} END AS evidence_preview
                FROM radar_trends trend
                WHERE trend.public_id = :public_id
                  AND trend.state NOT IN ('candidate', 'rejected')
            """), {"public_id": public_id}).first()
        return row

    def country_trends(self, country_code: str, *, filters: RadarFilters, cursor: dict[str, Any] | None, limit: int) -> dict[str, Any]:
        # Country waves use the same stable sort and cursor key as the meta list.
        country_filters = RadarFilters(
            state=filters.state,
            contour=filters.contour,
            country=country_code,
            story_id=filters.story_id,
            signal_id=filters.signal_id,
        )
        return self._country_page(country_filters, cursor, limit)

    def _country_page(self, filters: RadarFilters, cursor: dict[str, Any] | None, limit: int) -> dict[str, Any]:
        params: dict[str, Any] = {
            "state": filters.state,
            "public_states": ["emerging", "confirmed", "cooling"],
            "contour": filters.contour,
            "country": filters.country,
            "story_id": filters.story_id,
            "signal_id": filters.signal_id,
            "limit": limit + 1,
        }
        cursor_sql = ""
        if cursor:
            params.update({"cursor_state_rank": cursor["state_rank"], "cursor_velocity": cursor["velocity"], "cursor_first_observed_at": cursor["first_observed_at"], "cursor_public_id": cursor["public_id"]})
            cursor_sql = """
              AND (state_rank > :cursor_state_rank
                OR (state_rank = :cursor_state_rank AND velocity < :cursor_velocity)
                OR (state_rank = :cursor_state_rank AND velocity = :cursor_velocity AND first_observed_at < CAST(:cursor_first_observed_at AS timestamptz))
                OR (state_rank = :cursor_state_rank AND velocity = :cursor_velocity AND first_observed_at = CAST(:cursor_first_observed_at AS timestamptz) AND public_id > CAST(:cursor_public_id AS uuid)))
            """
        with radar_read_session() as session:
            rows = session.execute(text(f"""
                WITH ranked AS (
                  SELECT trend.*, CASE trend.state WHEN 'confirmed' THEN 0 WHEN 'emerging' THEN 1 WHEN 'cooling' THEN 2 WHEN 'candidate' THEN 3 WHEN 'resolved' THEN 4 ELSE 5 END AS state_rank
                  FROM radar_trends trend WHERE trend.scope = 'country' AND trend.country_code = :country
                    AND ((:state IS NOT NULL AND trend.state = :state)
                      OR (:state IS NULL AND trend.state = ANY(:public_states)))
                    AND (:contour IS NULL OR trend.contour = :contour)
                    /* radar_related_story */
                    AND (:story_id IS NULL OR EXISTS (
                      SELECT 1 FROM radar_trend_evidence related
                      WHERE related.trend_id = trend.id AND related.story_id = :story_id))
                    /* radar_related_signal */
                    AND (:signal_id IS NULL OR EXISTS (
                      SELECT 1 FROM radar_trend_evidence related
                      WHERE related.trend_id = trend.id AND related.signal_id = :signal_id)))
                SELECT ranked.*, '[]'::jsonb AS country_waves,
                       jsonb_build_object(contour, jsonb_build_object('state', state, 'status', 'insufficient')) AS contours,
                       {self._contradiction_sql('ranked.id', include_active_members=False)} AS contradiction_marker,
                       {self._preview_json('ranked.id', include_active_members=False)} AS evidence_preview,
                       'country_wave_matches_selected_filters' AS why_included
                FROM ranked WHERE TRUE {cursor_sql}
                ORDER BY state_rank ASC, velocity DESC, first_observed_at DESC, public_id ASC LIMIT :limit
            """), params).fetchall()
        return self._page(rows, limit)

    def timeline(self, public_id: UUID) -> dict[str, Any] | None:
        trend = self.trend(public_id)
        if trend is None:
            return None
        trend_id = _value(trend, "id")
        with radar_read_session() as session:
            items = session.execute(text("""
                SELECT 'state' AS kind, occurred_at AS at, to_state AS state,
                       NULL::text AS contour, NULL::text AS revision_kind, evidence
                FROM radar_state_events WHERE trend_id = :trend_id
                UNION ALL
                SELECT 't0_revision' AS kind, created_at AS at, NULL::text AS state,
                       NULL::text AS contour, revision_kind, evidence
                FROM radar_t0_revisions WHERE trend_id = :trend_id
                UNION ALL
                SELECT 'country_joined' AS kind, member.joined_at AS at,
                       wave.state, wave.contour, NULL::text AS revision_kind,
                       COALESCE(member.evidence, '{}'::jsonb) || jsonb_build_object(
                         'country_code', wave.country_code,
                         'country_trend_public_id', wave.public_id)
                FROM radar_trend_members member
                JOIN radar_trends wave ON wave.id = member.country_trend_id
                WHERE member.meta_trend_id = :trend_id
                UNION ALL
                SELECT 'country_left' AS kind, member.left_at AS at,
                       wave.state, wave.contour, NULL::text AS revision_kind,
                       COALESCE(member.evidence, '{}'::jsonb) || jsonb_build_object(
                         'country_code', wave.country_code,
                         'country_trend_public_id', wave.public_id)
                FROM radar_trend_members member
                JOIN radar_trends wave ON wave.id = member.country_trend_id
                WHERE member.meta_trend_id = :trend_id AND member.left_at IS NOT NULL
                UNION ALL
                SELECT 'contour_evaluation' AS kind, link.evaluated_at AS at,
                       NULL::text AS state, NULL::text AS contour,
                       NULL::text AS revision_kind,
                       COALESCE(link.evidence, '{}'::jsonb) || jsonb_build_object(
                         'status', link.status,
                         'country_code', media.country_code,
                         'media_trend_public_id', media.public_id,
                         'action_trend_public_id', action.public_id)
                FROM radar_contour_links link
                JOIN radar_trends media ON media.id = link.media_trend_id
                JOIN radar_trends action ON action.id = link.action_trend_id
                WHERE EXISTS (
                  SELECT 1 FROM radar_trend_members member
                  WHERE member.meta_trend_id = :trend_id
                    AND member.country_trend_id IN (
                      link.media_trend_id, link.action_trend_id))
                ORDER BY at ASC, kind ASC
            """), {"trend_id": trend_id}).fetchall()
        return {"trend": trend, "items": items}

    def evidence(self, public_id: UUID, *, cursor: dict[str, Any] | None, limit: int) -> dict[str, Any] | None:
        trend = self.trend(public_id)
        if trend is None:
            return None
        params: dict[str, Any] = {
            "trend_id": _value(trend, "id"),
            "scope": _value(trend, "scope"),
            "limit": limit + 1,
            "cursor_id": cursor["id"] if cursor else 0,
        }
        with radar_read_session() as session:
            rows = session.execute(text(f"""
                /* radar_related_evidence */
                WITH related_evidence_ids AS (
                    SELECT evidence.id
                    FROM radar_trend_evidence evidence
                    WHERE evidence.trend_id = :trend_id
                    UNION
                    SELECT evidence.id
                    FROM radar_trend_members related_member
                    JOIN radar_trend_evidence evidence
                      ON evidence.trend_id = related_member.country_trend_id
                    WHERE :scope = 'meta'
                      AND related_member.meta_trend_id = :trend_id
                      AND related_member.left_at IS NULL
                ), deduplicated_evidence AS (
                    SELECT DISTINCT ON (evidence.public_id) evidence.id
                    FROM radar_trend_evidence evidence
                    JOIN related_evidence_ids related ON related.id = evidence.id
                    ORDER BY evidence.public_id, evidence.id
                )
                SELECT evidence.id, evidence.public_id, evidence.role, evidence.contribution, evidence.evidence,
                       COALESCE(article.title, observation.evidence->>'title', event.details->>'title') AS title,
                       COALESCE(article.resolved_url, article.url, observation.evidence->>'url', event.evidence->>'url') AS url
                FROM deduplicated_evidence related
                JOIN radar_trend_evidence evidence ON evidence.id = related.id
                LEFT JOIN radar_observations observation ON observation.id = evidence.observation_id
                LEFT JOIN action_events event ON event.id = evidence.action_event_id
                LEFT JOIN articles article ON article.id = COALESCE(evidence.article_id, observation.article_id)
                WHERE evidence.id > :cursor_id
                  AND {_PUBLIC_EVIDENCE_PREDICATE}
                ORDER BY evidence.id ASC LIMIT :limit
            """), params).fetchall()
        items = list(rows[:limit])
        return {"items": items, "next_key": {"id": int(_value(items[-1], "id"))} if len(rows) > limit and items else None}

    def coverage(self) -> dict[str, Any]:
        with radar_read_session() as session:
            rows = session.execute(text("""
                SELECT DISTINCT ON (country_code) country_code, coverage_confidence, updated_at
                FROM radar_trends WHERE scope = 'country'
                ORDER BY country_code, updated_at DESC, id DESC
            """)).fetchall()
        updated = max((_value(row, "updated_at") for row in rows if _value(row, "updated_at") is not None), default=None)
        return {"updated_at": updated, "countries": rows}


def get_radar_service() -> RadarReadService:
    return SqlRadarReadService()


def _page_response(page: dict[str, Any], *, scope: str, binding: dict[str, Any], limit: int) -> dict[str, Any]:
    next_key = page.get("next_key")
    return {"items": [serialize_trend(item) for item in page.get("items", [])], "limit": limit, "next_cursor": _encode_cursor(scope=scope, binding=binding, key=next_key) if next_key else None}


@router.get("/radar")
def get_radar(
    state: str | None = Query(None), contour: str | None = Query(None), country: str | None = Query(None),
    story_id: StoryQueryId | None = Query(None), signal_id: SignalQueryId | None = Query(None),
    cursor: str | None = Query(None, max_length=2048), limit: int = Query(25, ge=1, le=100),
    service: RadarReadService = Depends(get_radar_service),
):
    """List persisted meta trends; story and signal filters combine with AND."""

    filters = _validate_filters(state, contour, country, story_id, signal_id)
    key = _decode_cursor(cursor, scope="radar", binding=filters.binding(), validator=_list_cursor_key) if cursor else None
    return _page_response(service.list_trends(filters=filters, cursor=key, limit=limit), scope="radar", binding=filters.binding(), limit=limit)


@router.get("/radar/trends/{public_id}")
def get_radar_trend(public_id: UUID, service: RadarReadService = Depends(get_radar_service)):
    trend = service.trend(public_id)
    if trend is None:
        raise HTTPException(status_code=404, detail="radar trend not found")
    return serialize_trend(trend)


@router.get("/countries/{code}/radar")
def get_country_radar(
    code: str, state: str | None = Query(None), contour: str | None = Query(None), cursor: str | None = Query(None, max_length=2048),
    story_id: StoryQueryId | None = Query(None), signal_id: SignalQueryId | None = Query(None),
    limit: int = Query(25, ge=1, le=100), service: RadarReadService = Depends(get_radar_service),
):
    """List persisted country waves; story and signal filters combine with AND."""

    country = code.strip().upper()
    filters = _validate_filters(state, contour, country, story_id, signal_id)
    key = _decode_cursor(cursor, scope="country_radar", binding=filters.binding(), validator=_list_cursor_key) if cursor else None
    return _page_response(service.country_trends(country, filters=filters, cursor=key, limit=limit), scope="country_radar", binding=filters.binding(), limit=limit)


@router.get("/radar/trends/{public_id}/timeline")
def get_radar_timeline(public_id: UUID, service: RadarReadService = Depends(get_radar_service)):
    payload = service.timeline(public_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="radar trend not found")
    return {
        "trend": serialize_trend(payload["trend"]),
        "items": [_serialize_timeline_item(item) for item in payload.get("items", [])],
    }


@router.get("/radar/trends/{public_id}/evidence")
def get_radar_evidence(
    public_id: UUID, cursor: str | None = Query(None, max_length=2048), limit: int = Query(25, ge=1, le=100),
    service: RadarReadService = Depends(get_radar_service),
):
    binding = {"trend": str(public_id)}
    key = _decode_cursor(cursor, scope="radar_evidence", binding=binding, validator=_evidence_cursor_key) if cursor else None
    payload = service.evidence(public_id, cursor=key, limit=limit)
    if payload is None:
        raise HTTPException(status_code=404, detail="radar trend not found")
    items = []
    for item in payload.get("items", []):
        role = _value(item, "role")
        if role not in {"trigger", "support", "context", "contradiction"}:
            continue
        evidence = sanitize_persisted_json(_json_object(_value(item, "evidence"), {}))
        items.append({"public_id": str(_value(item, "public_id")), "role": role, "contribution": _number(_value(item, "contribution")), "title": _value(item, "title"), "url": safe_public_url(_value(item, "url")), "evidence": evidence, "why_included": evidence.get("why_included") or f"{role}_evidence"})
    next_key = payload.get("next_key")
    return {"items": items, "limit": limit, "next_cursor": _encode_cursor(scope="radar_evidence", binding=binding, key=next_key) if next_key else None}


@router.get("/radar/coverage")
def get_radar_coverage(service: RadarReadService = Depends(get_radar_service)):
    payload = service.coverage()
    countries = []
    for item in payload.get("countries", []):
        confidence = _number(_value(item, "coverage_confidence")) or 0.0
        countries.append({"country_code": _value(item, "country_code"), "coverage_confidence": confidence, "state": "critical" if confidence <= 0.5 else "degraded" if confidence < 0.75 else "healthy", "blind_spots": sanitize_persisted_json(_json_object(_value(item, "blind_spots"), []))})
    return {"updated_at": _as_iso(payload.get("updated_at")), "coverage_source": "temporary trend-derived proxy; not collection-health snapshots", "countries": countries}


def radar_methodology_payload() -> dict[str, Any]:
    """Immutable public contract for the persisted Radar detector."""
    return {
        "detector_version": DETECTOR_VERSION,
        "updated_at": RADAR_METHODOLOGY_UPDATED_AT.isoformat(),
        "baseline": {"window_days": 90, "acceleration_days": 7},
        "lifecycle_gates": {"media": {"persistent_observation_days": 2, "independent_publisher_families": 2, "minimum_signal_strength": 0.5}, "action": {"authoritative_source": True, "independent_authoritative_sources": 2}},
        "t0_fields": ["t0_auto", "t0_effective"],
        "confidence_factors": ["signal_strength", "source_independence", "coverage_health", "persistence"],
        "coverage_hard_gate": "coverage_confidence <= 0.5 suppresses confirmation while retaining the candidate",
        "action_independence": "analysis.action_level is media classification and never independently confirms action evidence",
        "evidence_roles": ["trigger", "support", "context", "contradiction"],
        "limitations": ["Radar reads only persisted observations and trends.", "Coverage is a temporary trend-derived proxy, not collection-health snapshots.", "Insufficient action evidence remains explicitly insufficient.", "Critical collection gaps can suppress confirmation."],
    }


@router.get("/methodology/radar")
def get_radar_methodology():
    return radar_methodology_payload()
