"""Read-only APIs for persisted cross-country stories."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.countries import COUNTRIES, country_name_ru
from src.db import get_session
from src.stories import MERGE_THRESHOLD


router = APIRouter(prefix="/api/v2", tags=["stories"])
STORY_CURSOR_VERSION = "stories-v3-activity-relevance"
ARTICLE_CURSOR_VERSION = "story-articles-v2-relevance"
MAX_LINKED_SIGNALS = 5
MAX_STORY_ALIAS_DEPTH = 32
MAX_RRI_SHIFTS = 8
MIN_MEANINGFUL_RRI_DELTA = 3.0

STORY_RANK_FEATURES_CTE = """
WITH story_rank_raw AS (
    SELECT st_snapshot.id AS story_id,
           MIN(ar.published_at) AS first_seen,
           MAX(ar.published_at) AS last_seen,
           COUNT(DISTINCT sa.article_id)::integer AS article_count,
           COUNT(DISTINCT ar.source_id)::integer AS source_count,
           COUNT(DISTINCT TRIM(src.country_code))::integer AS country_count,
           COALESCE(MAX(
               CASE
                   WHEN sa.evidence->>'action_level_snapshot' ~ '^[1-5]$'
                   THEN (sa.evidence->>'action_level_snapshot')::integer
                   ELSE 1
               END
           ), 1)::integer AS highest_action_level,
           array_agg(DISTINCT TRIM(src.country_code)) AS country_codes
    FROM stories st_snapshot
    JOIN story_articles sa
      ON sa.story_id = st_snapshot.id
     AND sa.added_at <= :ranking_at
    JOIN articles ar ON ar.id = sa.article_id
    JOIN sources src ON src.id = ar.source_id
    WHERE st_snapshot.created_at <= :ranking_at
    GROUP BY st_snapshot.id
), story_rank_features AS (
    SELECT raw.*,
           CASE
               WHEN raw.last_seen >= :ranking_at - INTERVAL '2 days'
                    AND raw.highest_action_level >= 4 THEN 0
               WHEN raw.first_seen < :ranking_at - INTERVAL '2 days'
                    AND raw.last_seen >= :ranking_at - INTERVAL '3 days' THEN 1
               WHEN raw.first_seen >= :ranking_at - INTERVAL '2 days' THEN 2
               WHEN raw.last_seen >= :ranking_at - INTERVAL '14 days' THEN 3
               ELSE 4
           END AS lifecycle_rank
    FROM story_rank_raw raw
)
"""

STORY_LIFECYCLE_RANK_SQL = "rf.lifecycle_rank"

STORY_RELEVANCE_SQL = """(
    GREATEST(
        0,
        1 - LEAST(
            GREATEST(EXTRACT(EPOCH FROM (:ranking_at - rf.last_seen)) / 86400, 0),
            30
        ) / 30
    ) * 0.35
    + LEAST(
        rf.article_count::numeric
        / GREATEST(EXTRACT(EPOCH FROM (rf.last_seen - rf.first_seen)) / 86400, 1),
        20
    ) / 20 * 0.25
    + LEAST(rf.highest_action_level, 5)::numeric / 5 * 0.18
    + LEAST(
        rf.source_count::numeric
        / GREATEST(rf.article_count, 1),
        1
    ) * 0.12
    + LEAST(rf.country_count, 6)::numeric / 6 * 0.10
)"""

STORY_FIELDS = """
    st.id, st.slug, st.title_ru, st.title_en, st.summary, st.lifecycle,
    st.first_seen, st.last_seen, st.article_count, st.source_count,
    st.country_count, st.highest_action_level, st.clustering_confidence,
    st.generated_at, st.meta,
    """ + STORY_LIFECYCLE_RANK_SQL + """ AS lifecycle_rank,
    rf.last_seen AS ranking_last_seen,
    """ + STORY_RELEVANCE_SQL + """ AS relevance_score,
    (SELECT COALESCE(jsonb_agg(TRIM(c.country_code) ORDER BY c.country_code), '[]'::jsonb)
     FROM story_countries c WHERE c.story_id = st.id) AS countries,
    (SELECT ar.url
     FROM story_articles primary_membership
     JOIN articles ar ON ar.id = primary_membership.article_id
     WHERE primary_membership.story_id = st.id AND ar.url IS NOT NULL
     ORDER BY ar.published_at DESC NULLS LAST, ar.id DESC LIMIT 1) AS primary_url
"""


class StoryCountryContext(BaseModel):
    country_code: str
    country_name: str
    article_count: int
    source_count: int
    media_tone: float | None = None


class StoryListItem(BaseModel):
    id: int
    slug: str
    title_ru: str
    title_en: str | None = None
    summary: str | None = None
    lifecycle: str
    first_seen: str
    last_seen: str
    article_count: int
    source_count: int
    country_count: int
    highest_action_level: int
    clustering_confidence: float
    generated_at: str | None = None
    countries: list[str] = Field(default_factory=list)
    primary_url: str | None = None
    why_included: list[str] = Field(default_factory=list)
    relevance_score: float
    confidence: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    linked_signal_count: int = 0
    linked_signals: list["StorySignalLink"] = Field(default_factory=list)
    latest_rri_shift: "StoryRriShift | None" = None
    country_context: StoryCountryContext | None = None


class StorySignalLink(BaseModel):
    id: int
    type: str
    severity: str
    title: str
    created_at: str
    confidence: float
    completeness: str
    relation: Literal["explicit_story_evidence", "shared_article_membership"]
    evidence: dict[str, Any] = Field(default_factory=dict)


class StoryRriShift(BaseModel):
    country_code: str
    country_name: str
    at: str
    score: float
    delta_24h: float
    version: str
    relation: Literal["temporal_context"] = "temporal_context"
    why_included: Literal["rri_point_within_story_window"] = (
        "rri_point_within_story_window"
    )
    limitation: str = (
        "Временное совпадение с сюжетом не доказывает причинность."
    )


class StoryCoverage(BaseModel):
    selected_from: str | None = None
    selected_to: str | None = None
    available_from: str | None = None
    available_to: str | None = None


class StoriesListResponse(BaseModel):
    stories: list[StoryListItem]
    next_cursor: str | None = None
    coverage: StoryCoverage


class StoryCountrySlice(StoryCountryContext):
    first_seen: str | None = None
    last_seen: str | None = None
    primary_url: str | None = None


class StoryEntityEvidence(BaseModel):
    entity_id: str
    canonical_name: str
    kind: str
    mentions: int
    confidence: float
    evidence: dict[str, Any] = Field(default_factory=dict)


class StoryEventEvidence(BaseModel):
    entity_id: str
    event_key: str
    event_at: str | None = None
    action_level: int
    confidence: float
    evidence: dict[str, Any] = Field(default_factory=dict)


class StoryArticleEvidence(BaseModel):
    article_id: int
    title: str | None = None
    url: str | None = None
    published_at: str | None = None
    source: str
    country_code: str
    membership_confidence: float
    evidence: dict[str, Any] = Field(default_factory=dict)
    is_primary: bool = False
    why_included: list[str] = Field(default_factory=list)
    relevance_score: float
    confidence: float


class StoryDetailResponse(StoryListItem):
    countries: list[StoryCountrySlice]
    entities: list[StoryEntityEvidence]
    events: list[StoryEventEvidence]
    rri_shifts: list[StoryRriShift] = Field(default_factory=list)
    articles: list[StoryArticleEvidence]
    articles_next_cursor: str | None = None
    redirected_from_story_id: int | None = None


class CountryStoriesResponse(StoriesListResponse):
    country: str
    name: str


def _value(row: Any, name: str, default: Any = None) -> Any:
    if hasattr(row, name):
        return getattr(row, name)
    mapping = getattr(row, "_mapping", row if isinstance(row, dict) else {})
    return mapping.get(name, default)


def _iso(value: datetime | str | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else value


def _lifecycle_rank(lifecycle: str) -> int:
    return {
        "escalating": 0,
        "developing": 1,
        "emerging": 2,
        "cooling": 3,
        "resolved": 4,
    }.get(lifecycle, 5)


def _snapshot_lifecycle_rank(
    *,
    first_seen: datetime,
    last_seen: datetime,
    action_level: int,
    ranking_at: datetime,
) -> int:
    if last_seen >= ranking_at - timedelta(days=2) and action_level >= 4:
        return 0
    if (
        first_seen < ranking_at - timedelta(days=2)
        and last_seen >= ranking_at - timedelta(days=3)
    ):
        return 1
    if first_seen >= ranking_at - timedelta(days=2):
        return 2
    if last_seen >= ranking_at - timedelta(days=14):
        return 3
    return 4


def _story_activity_score(
    *,
    action_level: int,
    article_count: int,
    source_count: int,
    country_count: int,
    first_seen: datetime,
    last_seen: datetime,
    ranking_at: datetime,
) -> tuple[int, float]:
    """Mirror the SQL ordering contract for diagnostics and regression tests."""

    duration_days = max((last_seen - first_seen).total_seconds() / 86400, 1)
    age_days = min(max((ranking_at - last_seen).total_seconds() / 86400, 0), 30)
    recency = max(0.0, 1 - age_days / 30)
    velocity = min(max(article_count, 0) / duration_days, 20) / 20
    action = min(max(action_level, 0), 5) / 5
    diversity = min(max(source_count, 0) / max(article_count, 1), 1)
    breadth = min(max(country_count, 0), 6) / 6
    relevance = (
        recency * 0.35
        + velocity * 0.25
        + action * 0.18
        + diversity * 0.12
        + breadth * 0.10
    )
    return _snapshot_lifecycle_rank(
        first_seen=first_seen,
        last_seen=last_seen,
        action_level=action_level,
        ranking_at=ranking_at,
    ), relevance


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def safe_public_url(value: Any) -> str | None:
    """Return only absolute HTTP(S) URLs with a parseable hostname."""

    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        return None
    try:
        parsed = urlparse(value)
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        parsed.port  # force validation of a supplied port
        hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii")
        if len(hostname) > 253:
            return None
        if ":" not in hostname:
            hostname_pattern = re.compile(
                r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
            )
            if any(not hostname_pattern.fullmatch(label) for label in hostname.split(".")):
                return None
    except ValueError:
        return None
    return value


def story_to_dict(row: Any) -> dict[str, Any]:
    confidence = float(_value(row, "clustering_confidence", 0) or 0)
    relevance_score = _value(row, "relevance_score")
    if relevance_score is None:
        relevance_score = (
            confidence * 0.70
            + min(int(_value(row, "highest_action_level", 1) or 1), 5) / 5 * 0.20
            + min(int(_value(row, "article_count", 0) or 0), 10) / 10 * 0.10
        )
    lifecycle = _value(row, "lifecycle")
    country_count = int(_value(row, "country_count", 0) or 0)
    action_level = int(_value(row, "highest_action_level", 1) or 1)
    why_included = ["cross_country"] if country_count >= 2 else []
    if lifecycle != "resolved":
        why_included.append("active_lifecycle")
    if confidence >= MERGE_THRESHOLD:
        why_included.append("cluster_confidence")
    if action_level >= 3:
        why_included.append("high_action_level")
    return {
        "id": _value(row, "id"),
        "slug": _value(row, "slug"),
        "title_ru": _value(row, "title_ru"),
        "title_en": _value(row, "title_en"),
        "summary": _value(row, "summary"),
        "lifecycle": lifecycle,
        "first_seen": _iso(_value(row, "first_seen")),
        "last_seen": _iso(_value(row, "last_seen")),
        "article_count": int(_value(row, "article_count", 0) or 0),
        "source_count": int(_value(row, "source_count", 0) or 0),
        "country_count": country_count,
        "highest_action_level": action_level,
        "clustering_confidence": confidence,
        "generated_at": _iso(_value(row, "generated_at")),
        "countries": [str(code).strip() for code in _json_list(_value(row, "countries"))],
        "primary_url": safe_public_url(_value(row, "primary_url")),
        "why_included": why_included,
        "relevance_score": round(float(relevance_score), 3),
        "confidence": confidence,
        "evidence": _json_object(_value(row, "meta")),
        "linked_signal_count": 0,
        "linked_signals": [],
        "latest_rri_shift": None,
        "country_context": None,
    }


def _load_linked_signals(
    session: Any,
    story_ids: list[int],
) -> dict[int, dict[str, Any]]:
    """Load bounded, persisted signal links for a whole story page in one query."""

    if not story_ids:
        return {}
    rows = session.execute(text("""
        WITH RECURSIVE story_aliases AS (
            SELECT requested.story_id AS canonical_story_id,
                   requested.story_id AS alias_story_id,
                   ARRAY[requested.story_id]::bigint[] AS path
            FROM unnest(CAST(:story_ids AS bigint[])) AS requested(story_id)

            UNION ALL

            SELECT aliases.canonical_story_id,
                   candidate.id AS alias_story_id,
                   aliases.path || candidate.id
            FROM story_aliases aliases
            JOIN stories candidate
              ON candidate.meta->>'merged_into_story_id' = aliases.alias_story_id::text
            WHERE NOT candidate.id = ANY(aliases.path)
              AND cardinality(aliases.path) < :max_alias_depth
        ), alias_scope AS (
            SELECT COALESCE(
                       array_agg(DISTINCT alias_story_id),
                       '{}'::bigint[]
                   ) AS story_ids
            FROM story_aliases
        ), story_article_scope AS (
            SELECT COALESCE(
                       array_agg(DISTINCT sa.article_id),
                       '{}'::integer[]
                   ) AS article_ids
            FROM story_articles sa
            WHERE sa.story_id = ANY(CAST(:story_ids AS bigint[]))
        ), candidate_links AS (
            SELECT aliases.canonical_story_id AS story_id,
                   s.id AS signal_id, s.signal_type, s.severity, s.title,
                   s.created_at, se.confidence, se.completeness,
                   'explicit_story_evidence'::text AS link_relation,
                   0 AS relation_priority,
                   jsonb_build_object(
                       'source', 'signal_evidence.story_ids',
                       'story_id', aliases.canonical_story_id
                   ) || CASE
                       WHEN aliases.alias_story_id <> aliases.canonical_story_id
                       THEN jsonb_build_object(
                           'matched_story_id', aliases.alias_story_id
                       )
                       ELSE '{}'::jsonb
                   END AS link_evidence
            FROM signal_evidence se
            JOIN signals s ON s.id = se.signal_id
            JOIN alias_scope scope ON se.story_ids && scope.story_ids
            JOIN LATERAL unnest(se.story_ids) AS linked(story_id) ON TRUE
            JOIN story_aliases aliases
              ON aliases.alias_story_id = linked.story_id

            UNION ALL

            SELECT DISTINCT sa.story_id,
                   s.id AS signal_id, s.signal_type, s.severity, s.title,
                   s.created_at, se.confidence, se.completeness,
                   'shared_article_membership'::text AS link_relation,
                   1 AS relation_priority,
                   jsonb_build_object(
                       'source', 'signal_evidence.article_ids',
                       'shared_article_ids', (
                           SELECT COALESCE(jsonb_agg(shared.article_id ORDER BY shared.article_id), '[]'::jsonb)
                           FROM (
                               SELECT DISTINCT overlap_sa.article_id
                               FROM story_articles overlap_sa
                               WHERE overlap_sa.story_id = sa.story_id
                                 AND overlap_sa.article_id = ANY(se.article_ids)
                           ) shared
                       )
                   ) AS link_evidence
            FROM signal_evidence se
            JOIN signals s ON s.id = se.signal_id
            JOIN story_article_scope scope
              ON se.article_ids && scope.article_ids
            JOIN story_articles sa
              ON sa.story_id = ANY(CAST(:story_ids AS bigint[]))
             AND sa.article_id = ANY(se.article_ids)
            WHERE sa.story_id = ANY(CAST(:story_ids AS bigint[]))
        ), deduplicated AS (
            SELECT DISTINCT ON (story_id, signal_id)
                   story_id, signal_id, signal_type, severity, title,
                   created_at, confidence, completeness,
                   link_relation, link_evidence
            FROM candidate_links
            ORDER BY story_id, signal_id, relation_priority
        ), ranked AS (
            SELECT deduplicated.*,
                   COUNT(*) OVER (PARTITION BY story_id) AS linked_signal_count,
                   ROW_NUMBER() OVER (
                       PARTITION BY story_id
                       ORDER BY created_at DESC, signal_id DESC
                   ) AS link_rank
            FROM deduplicated
        )
        SELECT story_id, MAX(linked_signal_count)::integer AS linked_signal_count,
               COALESCE(
                   jsonb_agg(
                       jsonb_build_object(
                           'id', signal_id,
                           'type', signal_type,
                           'severity', severity,
                           'title', title,
                           'created_at', created_at,
                           'confidence', confidence,
                           'completeness', completeness,
                           'relation', link_relation,
                           'evidence', link_evidence
                       ) ORDER BY created_at DESC, signal_id DESC
                   ) FILTER (WHERE link_rank <= :linked_signal_limit),
                   '[]'::jsonb
               ) AS linked_signals
        FROM ranked
        GROUP BY story_id
    """), {
        "story_ids": story_ids,
        "linked_signal_limit": MAX_LINKED_SIGNALS,
        "max_alias_depth": MAX_STORY_ALIAS_DEPTH,
    }).fetchall()
    return {
        int(_value(row, "story_id")): {
            "linked_signal_count": int(_value(row, "linked_signal_count", 0) or 0),
            "linked_signals": _json_list(_value(row, "linked_signals"))[
                :MAX_LINKED_SIGNALS
            ],
        }
        for row in rows
        if _value(row, "story_id") is not None
    }


def _load_rri_shifts(
    session: Any,
    story_ids: list[int],
    *,
    preferred_country: str | None = None,
) -> dict[int, list[dict[str, Any]]]:
    """Load bounded meaningful temporal context, never presented as causation."""

    if not story_ids:
        return {}
    rows = session.execute(text("""
        WITH story_windows AS (
            SELECT st.id AS story_id, st.first_seen, st.last_seen
            FROM stories st
            WHERE st.id = ANY(CAST(:story_ids AS bigint[]))
        ), participating_rri AS (
            SELECT windows.story_id,
                   TRIM(sc.country_code) AS country_code,
                   ri.time AS point_time, ri.score, ri.delta_24h,
                   COALESCE(ri.version, 'v1') AS version,
                   ROW_NUMBER() OVER (
                       PARTITION BY windows.story_id
                       ORDER BY ri.time DESC,
                                ABS(ri.delta_24h) DESC,
                                sc.country_code
                   ) AS point_rank
            FROM story_windows windows
            JOIN story_countries sc ON sc.story_id = windows.story_id
            JOIN ru_index ri ON ri.country_code = sc.country_code
            WHERE ri.time >= windows.first_seen
              AND ri.time <= windows.last_seen
              AND ri.delta_24h IS NOT NULL
              AND ABS(ri.delta_24h) >= :min_meaningful_delta
              AND (
                  CAST(:preferred_country AS text) IS NULL
                  OR TRIM(sc.country_code) = :preferred_country
              )
        )
        SELECT story_id, country_code, point_time, score, delta_24h, version
        FROM participating_rri
        WHERE point_rank <= :rri_shift_limit
        ORDER BY story_id, point_rank
    """), {
        "story_ids": story_ids,
        "preferred_country": preferred_country,
        "min_meaningful_delta": MIN_MEANINGFUL_RRI_DELTA,
        "rri_shift_limit": MAX_RRI_SHIFTS,
    }).fetchall()
    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        story_id = _value(row, "story_id")
        point_time = _iso(_value(row, "point_time"))
        if story_id is None or point_time is None:
            continue
        country_code = str(_value(row, "country_code", "")).strip().upper()
        result.setdefault(int(story_id), []).append({
            "country_code": country_code,
            "country_name": country_name_ru(country_code),
            "at": point_time,
            "score": float(_value(row, "score", 0) or 0),
            "delta_24h": float(_value(row, "delta_24h", 0) or 0),
            "version": str(_value(row, "version", "v1") or "v1"),
            "relation": "temporal_context",
            "why_included": "rri_point_within_story_window",
            "limitation": (
                "Временное совпадение с сюжетом не доказывает причинность."
            ),
        })
    return result


def _load_country_contexts(
    session: Any,
    story_ids: list[int],
    *,
    country_code: str,
) -> dict[int, dict[str, Any]]:
    """Load the country-local count and tone for country-page story cards."""

    if not story_ids:
        return {}
    rows = session.execute(text("""
        SELECT sc.story_id,
               TRIM(sc.country_code) AS country_code,
               sc.article_count, sc.source_count, sc.media_tone
        FROM story_countries sc
        WHERE sc.story_id = ANY(CAST(:story_ids AS bigint[]))
          AND TRIM(sc.country_code) = :country_code
    """), {
        "story_ids": story_ids,
        "country_code": country_code,
    }).fetchall()
    return {
        int(_value(row, "story_id")): {
            "country_code": country_code,
            "country_name": country_name_ru(country_code),
            "article_count": int(_value(row, "article_count", 0) or 0),
            "source_count": int(_value(row, "source_count", 0) or 0),
            "media_tone": (
                float(_value(row, "media_tone"))
                if _value(row, "media_tone") is not None else None
            ),
        }
        for row in rows
        if _value(row, "story_id") is not None
    }


def _load_story_coverage(
    session: Any,
    *,
    country_code: str | None,
    selected_from: datetime | None,
    selected_to: datetime | None,
) -> dict[str, str | None]:
    """Return first/last relevant indexed publication via two bounded probes."""

    query_result = session.execute(text("""
        SELECT (
            SELECT ar.published_at
            FROM articles ar
            JOIN sources source_from ON source_from.id = ar.source_id
            WHERE EXISTS (
                SELECT 1
                FROM analysis analyzed_from
                WHERE analyzed_from.article_id = ar.id
                  AND analyzed_from.is_relevant IS TRUE
            )
              AND (
                  CAST(:coverage_country AS text) IS NULL
                  OR TRIM(source_from.country_code) = :coverage_country
              )
            ORDER BY ar.published_at ASC, ar.id ASC
            LIMIT 1
        ) AS available_from,
        (
            SELECT ar.published_at
            FROM articles ar
            JOIN sources source_to ON source_to.id = ar.source_id
            WHERE EXISTS (
                SELECT 1
                FROM analysis analyzed_to
                WHERE analyzed_to.article_id = ar.id
                  AND analyzed_to.is_relevant IS TRUE
            )
              AND (
                  CAST(:coverage_country AS text) IS NULL
                  OR TRIM(source_to.country_code) = :coverage_country
              )
            ORDER BY ar.published_at DESC, ar.id DESC
            LIMIT 1
        ) AS available_to
    """), {"coverage_country": country_code})
    if hasattr(query_result, "fetchone"):
        row = query_result.fetchone()
    else:
        rows = query_result.fetchall()
        row = rows[0] if rows else None
    return {
        "selected_from": _iso(selected_from),
        "selected_to": _iso(selected_to),
        "available_from": _iso(_value(row, "available_from")) if row else None,
        "available_to": _iso(_value(row, "available_to")) if row else None,
    }


def _attach_story_context(
    session: Any,
    stories: list[dict[str, Any]],
    *,
    preferred_country: str | None = None,
    include_rri_shifts: bool = False,
) -> None:
    story_ids = [int(story["id"]) for story in stories]
    signals = _load_linked_signals(session, story_ids)
    rri_shifts = _load_rri_shifts(
        session,
        story_ids,
        preferred_country=preferred_country,
    )
    country_contexts = (
        _load_country_contexts(
            session,
            story_ids,
            country_code=preferred_country,
        )
        if preferred_country else {}
    )
    for story in stories:
        story_id = int(story["id"])
        story.update(signals.get(story_id, {}))
        story_rri_shifts = rri_shifts.get(story_id, [])[:MAX_RRI_SHIFTS]
        story["latest_rri_shift"] = (
            story_rri_shifts[0] if story_rri_shifts else None
        )
        if include_rri_shifts:
            story["rri_shifts"] = story_rri_shifts
        story["country_context"] = country_contexts.get(story_id)


def _encode_cursor(row: Any, context_hash: str, ranking_at: datetime) -> str:
    lifecycle = str(_value(row, "lifecycle", ""))
    payload = json.dumps([
        STORY_CURSOR_VERSION,
        context_hash,
        _iso(ranking_at),
        int(_value(row, "lifecycle_rank", _lifecycle_rank(lifecycle))),
        float(_value(row, "relevance_score", 0) or 0),
        _iso(_value(row, "ranking_last_seen", _value(row, "last_seen"))),
        int(_value(row, "id")),
    ], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(
    cursor: str,
    *,
    expected_context_hash: str,
) -> tuple[datetime, int, float, datetime, int]:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, list) or len(payload) != 7:
            raise ValueError("wrong cursor shape")
        (
            version,
            context_hash,
            ranking_at,
            lifecycle_rank,
            relevance_score,
            last_seen,
            story_id,
        ) = payload
        if version != STORY_CURSOR_VERSION or context_hash != expected_context_hash:
            raise HTTPException(
                status_code=400, detail="Story cursor does not match query"
            )
        if (
            not isinstance(lifecycle_rank, int)
            or isinstance(lifecycle_rank, bool)
            or lifecycle_rank < 0
            or lifecycle_rank > 5
        ):
            raise ValueError("invalid lifecycle rank")
        if not isinstance(relevance_score, (int, float)) or isinstance(relevance_score, bool):
            raise ValueError("invalid relevance score")
        if not math.isfinite(float(relevance_score)):
            raise ValueError("invalid relevance score")
        if (
            not isinstance(ranking_at, str)
            or not isinstance(last_seen, str)
            or not isinstance(story_id, int)
            or isinstance(story_id, bool)
        ):
            raise ValueError("invalid cursor types")
        return (
            _as_utc_query_datetime(
                datetime.fromisoformat(ranking_at.replace("Z", "+00:00"))
            ),
            lifecycle_rank,
            float(relevance_score),
            datetime.fromisoformat(last_seen.replace("Z", "+00:00")),
            story_id,
        )
    except HTTPException:
        raise
    except (
        AttributeError, binascii.Error, json.JSONDecodeError, OverflowError,
        TypeError, UnicodeDecodeError, ValueError,
    ) as exc:
        raise HTTPException(status_code=400, detail="Invalid story cursor") from exc


def _encode_article_cursor(row: Any, story_id: int) -> str:
    payload = json.dumps([
        ARTICLE_CURSOR_VERSION,
        story_id,
        float(_value(row, "relevance_score", 0) or 0),
        _iso(_value(row, "published_at")),
        int(_value(row, "article_id")),
    ], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_article_cursor(
    cursor: str,
    *,
    expected_story_id: int,
) -> tuple[float, datetime, int]:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, list) or len(payload) != 5:
            raise ValueError("wrong article cursor shape")
        version, story_id, relevance_score, published_at, article_id = payload
        if version != ARTICLE_CURSOR_VERSION or story_id != expected_story_id:
            raise HTTPException(
                status_code=400, detail="Article cursor does not match story"
            )
        if not isinstance(relevance_score, (int, float)) or isinstance(relevance_score, bool):
            raise ValueError("invalid relevance score")
        if not math.isfinite(float(relevance_score)):
            raise ValueError("invalid relevance score")
        if not isinstance(published_at, str) or not isinstance(article_id, int) or isinstance(article_id, bool):
            raise ValueError("invalid article cursor types")
        return (
            float(relevance_score),
            datetime.fromisoformat(published_at.replace("Z", "+00:00")),
            article_id,
        )
    except HTTPException:
        raise
    except (
        AttributeError, binascii.Error, json.JSONDecodeError, OverflowError,
        TypeError, UnicodeDecodeError, ValueError,
    ) as exc:
        raise HTTPException(status_code=400, detail="Invalid article cursor") from exc


def _validate_country(country: str | None) -> str | None:
    if country is None:
        return None
    country = country.upper()
    if country not in COUNTRIES:
        raise HTTPException(status_code=404, detail="Unknown country code")
    return country


def _as_utc_query_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _list_stories(
    *,
    scope: str,
    country: str | None,
    lifecycle: str | None,
    min_confidence: float,
    min_action_level: int,
    since: datetime | None,
    topic: str | None,
    entity_id: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
    cursor: str | None,
    limit: int,
) -> dict[str, Any]:
    since = _as_utc_query_datetime(since)
    date_from = _as_utc_query_datetime(date_from)
    date_to = _as_utc_query_datetime(date_to)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must not exceed date_to")
    context_payload = {
        "version": STORY_CURSOR_VERSION,
        "scope": scope,
        "country": country,
        "lifecycle": lifecycle,
        "min_confidence": min_confidence,
        "min_action_level": min_action_level,
        "since": _iso(since),
        "topic": topic,
        "entity_id": entity_id,
        "date_from": _iso(date_from),
        "date_to": _iso(date_to),
    }
    context_hash = hashlib.sha256(json.dumps(
        context_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    conditions = [
        "rf.country_count >= 2",
        "st.clustering_confidence >= :min_confidence",
        "NOT (COALESCE(st.meta, '{}'::jsonb) ? 'merged_into_story_id')",
    ]
    params: dict[str, Any] = {
        "min_confidence": min_confidence,
        "min_action_level": min_action_level,
        "limit": limit + 1,
        "ranking_at": datetime.now(timezone.utc),
    }
    conditions.append("rf.highest_action_level >= :min_action_level")
    if country:
        conditions.append(":country = ANY(rf.country_codes)")
        params["country"] = country
    if lifecycle:
        conditions.append("st.lifecycle = :lifecycle")
        params["lifecycle"] = lifecycle
    if since:
        conditions.append("rf.last_seen >= :since")
        params["since"] = since
    if topic:
        conditions.append("COALESCE(st.meta->'topics', '[]'::jsonb) ? :topic")
        params["topic"] = topic
    if entity_id:
        conditions.append(
            "EXISTS (SELECT 1 FROM story_entities filter_entity "
            "WHERE filter_entity.story_id = st.id "
            "AND filter_entity.entity_id::text = :entity_id)"
        )
        params["entity_id"] = entity_id
    if date_from:
        conditions.append("rf.last_seen >= :date_from")
        params["date_from"] = date_from
    if date_to:
        conditions.append("rf.first_seen <= :date_to")
        params["date_to"] = date_to
    if cursor:
        (
            cursor_ranking_at,
            cursor_lifecycle_rank,
            cursor_relevance,
            cursor_last_seen,
            cursor_id,
        ) = _decode_cursor(cursor, expected_context_hash=context_hash)
        params["ranking_at"] = cursor_ranking_at
        conditions.append(
            f"(({STORY_LIFECYCLE_RANK_SQL}) > :cursor_lifecycle_rank "
            f"OR (({STORY_LIFECYCLE_RANK_SQL}) = :cursor_lifecycle_rank "
            f"AND ({STORY_RELEVANCE_SQL} < :cursor_relevance "
            f"OR ({STORY_RELEVANCE_SQL} = :cursor_relevance "
            "AND (rf.last_seen < :cursor_last_seen OR "
            "(rf.last_seen = :cursor_last_seen AND st.id < :cursor_id))))))"
        )
        params.update({
            "cursor_lifecycle_rank": cursor_lifecycle_rank,
            "cursor_relevance": cursor_relevance,
            "cursor_last_seen": cursor_last_seen,
            "cursor_id": cursor_id,
        })

    where = " AND ".join(conditions)
    with get_session() as session:
        rows = session.execute(text(f"""
            {STORY_RANK_FEATURES_CTE}
            SELECT {STORY_FIELDS}
            FROM stories st
            JOIN story_rank_features rf ON rf.story_id = st.id
            WHERE {where}
            ORDER BY lifecycle_rank ASC,
                     relevance_score DESC, ranking_last_seen DESC, st.id DESC
            LIMIT :limit
        """), params).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        stories = [story_to_dict(row) for row in page]
        _attach_story_context(
            session,
            stories,
            preferred_country=country,
        )
        coverage = _load_story_coverage(
            session,
            country_code=country,
            selected_from=date_from,
            selected_to=date_to,
        )

    return {
        "stories": stories,
        "next_cursor": (
            _encode_cursor(page[-1], context_hash, params["ranking_at"])
            if has_more and page else None
        ),
        "coverage": coverage,
    }


@router.get("/stories", response_model=StoriesListResponse)
def list_stories(
    country: Optional[str] = Query(default=None),
    lifecycle: Optional[str] = Query(
        default=None,
        pattern="^(emerging|developing|escalating|cooling|resolved)$",
    ),
    min_confidence: float = Query(default=0, ge=0, le=1),
    min_action_level: int = Query(default=1, ge=1),
    since: Optional[datetime] = Query(default=None),
    topic: Optional[str] = Query(default=None, max_length=100),
    entity_id: Optional[str] = Query(default=None, max_length=100),
    date_from: Optional[datetime] = Query(default=None),
    date_to: Optional[datetime] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    """List persisted stories with stable cursor pagination."""

    return _list_stories(
        scope="stories",
        country=_validate_country(country),
        lifecycle=lifecycle,
        min_confidence=min_confidence,
        min_action_level=min_action_level,
        since=since,
        topic=topic,
        entity_id=entity_id,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )


@router.get("/stories/by-slug/{story_slug}", response_model=StoryDetailResponse)
def get_story_by_slug(
    story_slug: str,
    article_cursor: Optional[str] = Query(default=None),
    article_limit: int = Query(default=50, ge=1, le=100),
):
    """Resolve a stable story slug, including superseded story slugs."""

    with get_session() as session:
        matched = session.execute(text("""
            SELECT st.id
            FROM stories st
            WHERE st.slug = :story_slug
        """), {"story_slug": story_slug}).fetchone()
    if not matched:
        raise HTTPException(status_code=404, detail="Story not found")
    return get_story(
        int(_value(matched, "id")),
        article_cursor=article_cursor,
        article_limit=article_limit,
    )


@router.get("/stories/{story_id}", response_model=StoryDetailResponse)
def get_story(
    story_id: int,
    article_cursor: Optional[str] = Query(default=None),
    article_limit: int = Query(default=50, ge=1, le=100),
):
    """Return a story with membership, entity, event, and country evidence."""

    article_params: dict[str, Any] = {
        "story_id": story_id,
        "article_limit": article_limit + 1,
    }
    request_ranking_at = datetime.now(timezone.utc)

    redirected_from_story_id: int | None = None
    with get_session() as session:
        story = session.execute(text(f"""
            {STORY_RANK_FEATURES_CTE}
            SELECT {STORY_FIELDS}
            FROM stories st
            JOIN story_rank_features rf ON rf.story_id = st.id
            WHERE st.id = :story_id
        """), {
            "story_id": story_id,
            "ranking_at": request_ranking_at,
        }).fetchone()
        if not story:
            raise HTTPException(status_code=404, detail="Story not found")

        story_meta = _json_object(_value(story, "meta"))
        canonical_story_id = story_meta.get("merged_into_story_id")
        if (
            isinstance(canonical_story_id, int)
            and not isinstance(canonical_story_id, bool)
            and canonical_story_id != story_id
        ):
            redirected_from_story_id = story_id
            story_id = canonical_story_id
            story = session.execute(text(f"""
                {STORY_RANK_FEATURES_CTE}
                SELECT {STORY_FIELDS}
                FROM stories st
                JOIN story_rank_features rf ON rf.story_id = st.id
                WHERE st.id = :story_id
            """), {
                "story_id": story_id,
                "ranking_at": request_ranking_at,
            }).fetchone()
            if not story:
                raise HTTPException(status_code=404, detail="Canonical story not found")
            article_params["story_id"] = story_id

        article_conditions = []
        if article_cursor:
            cursor_relevance, cursor_published_at, cursor_article_id = (
                _decode_article_cursor(
                    article_cursor, expected_story_id=story_id
                )
            )
            article_conditions.append(
                "(ranked.relevance_score < :article_cursor_relevance OR "
                "(ranked.relevance_score = :article_cursor_relevance AND "
                "(ranked.published_at < :article_cursor_published_at OR "
                "(ranked.published_at = :article_cursor_published_at "
                "AND ranked.article_id < :article_cursor_article_id))))"
            )
            article_params.update({
                "article_cursor_relevance": cursor_relevance,
                "article_cursor_published_at": cursor_published_at,
                "article_cursor_article_id": cursor_article_id,
            })
        article_where = (
            "WHERE " + " AND ".join(article_conditions)
            if article_conditions else ""
        )

        country_rows = session.execute(text("""
            SELECT TRIM(sc.country_code) AS country_code, sc.article_count,
                   sc.source_count, sc.media_tone, sc.first_seen, sc.last_seen,
                   (SELECT ar.url
                    FROM story_articles sa
                    JOIN articles ar ON ar.id = sa.article_id
                    JOIN sources s ON s.id = ar.source_id
                    WHERE sa.story_id = sc.story_id
                      AND s.country_code = sc.country_code
                      AND ar.url IS NOT NULL
                    ORDER BY ar.published_at DESC NULLS LAST, ar.id DESC LIMIT 1
                   ) AS primary_url
            FROM story_countries sc
            WHERE sc.story_id = :story_id
            ORDER BY sc.article_count DESC, sc.country_code
        """), {"story_id": story_id}).fetchall()
        entity_rows = session.execute(text("""
            SELECT se.entity_id::text AS entity_id, se.mentions,
                   se.confidence, se.evidence,
                   COALESCE(
                       to_jsonb(ce)->>'canonical_name',
                       to_jsonb(ce)->>'name_ru',
                       to_jsonb(ce)->>'name',
                       se.entity_id::text
                   ) AS canonical_name,
                   COALESCE(
                       to_jsonb(ce)->>'kind',
                       to_jsonb(ce)->>'entity_type',
                       'unknown'
                   ) AS kind
            FROM story_entities se
            LEFT JOIN canonical_entities ce ON ce.id = se.entity_id
            WHERE se.story_id = :story_id
            ORDER BY se.mentions DESC, se.entity_id
        """), {"story_id": story_id}).fetchall()
        event_rows = session.execute(text("""
            SELECT sve.entity_id::text AS entity_id, sve.event_key,
                   sve.event_at, sve.action_level, sve.evidence,
                   COALESCE(se.confidence, 0) AS confidence
            FROM story_events sve
            LEFT JOIN story_entities se
              ON se.story_id = sve.story_id AND se.entity_id = sve.entity_id
            WHERE sve.story_id = :story_id
            ORDER BY sve.event_at DESC NULLS LAST, sve.entity_id
        """), {"story_id": story_id}).fetchall()
        article_rows = session.execute(text(f"""
            WITH ranked_articles AS (
                SELECT ar.id AS article_id, ar.title, ar.url, ar.published_at,
                       s.name AS source, TRIM(s.country_code) AS country_code,
                       sa.membership_confidence, sa.evidence,
                       ROUND((
                           COALESCE(sa.membership_confidence, 0) * 0.60
                           + COALESCE(an.relevance_score, 0) * 0.40
                       )::numeric, 3) AS relevance_score,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.country_code
                           ORDER BY ar.published_at DESC NULLS LAST, ar.id DESC
                       ) = 1 AS is_primary
                FROM story_articles sa
                JOIN articles ar ON ar.id = sa.article_id
                JOIN sources s ON s.id = ar.source_id
                LEFT JOIN analysis an ON an.article_id = ar.id
                WHERE sa.story_id = :story_id
            )
            SELECT * FROM ranked_articles ranked
            {article_where}
            ORDER BY ranked.relevance_score DESC,
                     ranked.published_at DESC NULLS LAST, ranked.article_id DESC
            LIMIT :article_limit
        """), article_params).fetchall()
        story_context = story_to_dict(story)
        _attach_story_context(
            session,
            [story_context],
            include_rri_shifts=True,
        )

    result = story_context
    result["countries"] = [{
        "country_code": str(_value(row, "country_code")).strip(),
        "country_name": country_name_ru(str(_value(row, "country_code")).strip()),
        "article_count": int(_value(row, "article_count", 0) or 0),
        "source_count": int(_value(row, "source_count", 0) or 0),
        "media_tone": float(_value(row, "media_tone")) if _value(row, "media_tone") is not None else None,
        "first_seen": _iso(_value(row, "first_seen")),
        "last_seen": _iso(_value(row, "last_seen")),
        "primary_url": safe_public_url(_value(row, "primary_url")),
    } for row in country_rows]
    result["entities"] = [{
        "entity_id": str(_value(row, "entity_id")),
        "canonical_name": _value(row, "canonical_name") or str(_value(row, "entity_id")),
        "kind": _value(row, "kind") or "unknown",
        "mentions": int(_value(row, "mentions", 0) or 0),
        "confidence": float(_value(row, "confidence", 0) or 0),
        "evidence": _json_object(_value(row, "evidence")),
    } for row in entity_rows]
    result["events"] = [{
        "entity_id": str(_value(row, "entity_id")),
        "event_key": _value(row, "event_key"),
        "event_at": _iso(_value(row, "event_at")),
        "action_level": int(_value(row, "action_level", 1) or 1),
        "confidence": float(_value(row, "confidence", 0) or 0),
        "evidence": _json_object(_value(row, "evidence")),
    } for row in event_rows]
    has_more_articles = len(article_rows) > article_limit
    article_page = article_rows[:article_limit]
    primary_countries: set[str] = set()
    articles = []
    for row in article_page:
        country_code = str(_value(row, "country_code")).strip()
        row_primary = _value(row, "is_primary")
        is_primary = bool(row_primary) if row_primary is not None else country_code not in primary_countries
        primary_countries.add(country_code)
        evidence = _json_object(_value(row, "evidence"))
        matched_features = evidence.get("matched_features") or []
        why_included = [f"matched_{feature}" for feature in matched_features]
        if is_primary:
            why_included.append("country_primary")
        confidence = float(_value(row, "membership_confidence", 0) or 0)
        articles.append({
            "article_id": _value(row, "article_id"),
            "title": _value(row, "title"),
            "url": safe_public_url(_value(row, "url")),
            "published_at": _iso(_value(row, "published_at")),
            "source": _value(row, "source"),
            "country_code": country_code,
            "membership_confidence": confidence,
            "evidence": evidence,
            "is_primary": is_primary,
            "why_included": why_included or ["story_membership"],
            "relevance_score": float(_value(row, "relevance_score", confidence) or 0),
            "confidence": confidence,
        })
    result["articles"] = articles
    result["articles_next_cursor"] = (
        _encode_article_cursor(article_page[-1], story_id)
        if has_more_articles and article_page else None
    )
    result["redirected_from_story_id"] = redirected_from_story_id
    return result


@router.get("/countries/{code}/stories", response_model=CountryStoriesResponse)
def get_country_stories(
    code: str,
    lifecycle: Optional[str] = Query(
        default=None,
        pattern="^(emerging|developing|escalating|cooling|resolved)$",
    ),
    min_confidence: float = Query(default=0, ge=0, le=1),
    min_action_level: int = Query(default=1, ge=1),
    since: Optional[datetime] = Query(default=None),
    topic: Optional[str] = Query(default=None, max_length=100),
    entity_id: Optional[str] = Query(default=None, max_length=100),
    date_from: Optional[datetime] = Query(default=None),
    date_to: Optional[datetime] = Query(default=None),
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Return cross-country stories containing the requested country slice."""

    country = _validate_country(code)
    payload = _list_stories(
        scope="country_stories",
        country=country,
        lifecycle=lifecycle,
        min_confidence=min_confidence,
        min_action_level=min_action_level,
        since=since,
        topic=topic,
        entity_id=entity_id,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
        limit=limit,
    )
    return {"country": country, "name": country_name_ru(country), **payload}
