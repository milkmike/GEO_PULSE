"""Read-only APIs for persisted cross-country stories."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.config import COUNTRY_NAMES
from src.db import get_session


router = APIRouter(prefix="/api/v2", tags=["stories"])

STORY_FIELDS = """
    st.id, st.slug, st.title_ru, st.title_en, st.summary, st.lifecycle,
    st.first_seen, st.last_seen, st.article_count, st.source_count,
    st.country_count, st.highest_action_level, st.clustering_confidence,
    st.generated_at,
    (SELECT COALESCE(jsonb_agg(TRIM(c.country_code) ORDER BY c.country_code), '[]'::jsonb)
     FROM story_countries c WHERE c.story_id = st.id) AS countries,
    (SELECT ar.url
     FROM story_articles primary_membership
     JOIN articles ar ON ar.id = primary_membership.article_id
     WHERE primary_membership.story_id = st.id AND ar.url IS NOT NULL
     ORDER BY ar.published_at DESC NULLS LAST, ar.id DESC LIMIT 1) AS primary_url
"""


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


class StoriesListResponse(BaseModel):
    stories: list[StoryListItem]
    next_cursor: str | None = None


class StoryCountrySlice(BaseModel):
    country_code: str
    country_name: str
    article_count: int
    source_count: int
    media_tone: float | None = None
    first_seen: str | None = None
    last_seen: str | None = None
    primary_url: str | None = None


class StoryEntityEvidence(BaseModel):
    entity_id: str
    mentions: int
    confidence: float
    evidence: dict[str, Any] = Field(default_factory=dict)


class StoryEventEvidence(BaseModel):
    entity_id: str
    event_key: str
    event_at: str | None = None
    action_level: int
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


class StoryDetailResponse(StoryListItem):
    countries: list[StoryCountrySlice]
    entities: list[StoryEntityEvidence]
    events: list[StoryEventEvidence]
    articles: list[StoryArticleEvidence]


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


def story_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": _value(row, "id"),
        "slug": _value(row, "slug"),
        "title_ru": _value(row, "title_ru"),
        "title_en": _value(row, "title_en"),
        "summary": _value(row, "summary"),
        "lifecycle": _value(row, "lifecycle"),
        "first_seen": _iso(_value(row, "first_seen")),
        "last_seen": _iso(_value(row, "last_seen")),
        "article_count": int(_value(row, "article_count", 0) or 0),
        "source_count": int(_value(row, "source_count", 0) or 0),
        "country_count": int(_value(row, "country_count", 0) or 0),
        "highest_action_level": int(_value(row, "highest_action_level", 1) or 1),
        "clustering_confidence": float(_value(row, "clustering_confidence", 0) or 0),
        "generated_at": _iso(_value(row, "generated_at")),
        "countries": [str(code).strip() for code in _json_list(_value(row, "countries"))],
        "primary_url": _value(row, "primary_url"),
    }


def _encode_cursor(row: Any) -> str:
    payload = json.dumps([
        _iso(_value(row, "last_seen")),
        int(_value(row, "id")),
    ], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode((cursor + padding).encode("ascii"))
        last_seen, story_id = json.loads(decoded.decode("utf-8"))
        return datetime.fromisoformat(last_seen.replace("Z", "+00:00")), int(story_id)
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid story cursor") from exc


def _validate_country(country: str | None) -> str | None:
    if country is None:
        return None
    country = country.upper()
    if country not in COUNTRY_NAMES:
        raise HTTPException(status_code=404, detail="Unknown country code")
    return country


def _list_stories(
    *,
    country: str | None,
    lifecycle: str | None,
    min_confidence: float,
    min_action_level: int,
    since: datetime | None,
    cursor: str | None,
    limit: int,
) -> dict[str, Any]:
    conditions = ["st.country_count >= 2", "st.clustering_confidence >= :min_confidence"]
    params: dict[str, Any] = {
        "min_confidence": min_confidence,
        "min_action_level": min_action_level,
        "limit": limit + 1,
    }
    conditions.append("st.highest_action_level >= :min_action_level")
    if country:
        conditions.append("EXISTS (SELECT 1 FROM story_countries sc WHERE sc.story_id = st.id AND sc.country_code = :country)")
        params["country"] = country
    if lifecycle:
        conditions.append("st.lifecycle = :lifecycle")
        params["lifecycle"] = lifecycle
    if since:
        conditions.append("st.last_seen >= :since")
        params["since"] = since
    if cursor:
        cursor_last_seen, cursor_id = _decode_cursor(cursor)
        conditions.append(
            "(st.last_seen < :cursor_last_seen OR "
            "(st.last_seen = :cursor_last_seen AND st.id < :cursor_id))"
        )
        params.update({"cursor_last_seen": cursor_last_seen, "cursor_id": cursor_id})

    where = " AND ".join(conditions)
    with get_session() as session:
        rows = session.execute(text(f"""
            SELECT {STORY_FIELDS}
            FROM stories st
            WHERE {where}
            ORDER BY st.last_seen DESC, st.id DESC
            LIMIT :limit
        """), params).fetchall()

    has_more = len(rows) > limit
    page = rows[:limit]
    return {
        "stories": [story_to_dict(row) for row in page],
        "next_cursor": _encode_cursor(page[-1]) if has_more and page else None,
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
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    """List persisted stories with stable cursor pagination."""

    return _list_stories(
        country=_validate_country(country),
        lifecycle=lifecycle,
        min_confidence=min_confidence,
        min_action_level=min_action_level,
        since=since,
        cursor=cursor,
        limit=limit,
    )


@router.get("/stories/{story_id}", response_model=StoryDetailResponse)
def get_story(story_id: int):
    """Return a story with membership, entity, event, and country evidence."""

    with get_session() as session:
        story = session.execute(text(f"""
            SELECT {STORY_FIELDS}
            FROM stories st
            WHERE st.id = :story_id
        """), {"story_id": story_id}).fetchone()
        if not story:
            raise HTTPException(status_code=404, detail="Story not found")

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
                   se.confidence, se.evidence
            FROM story_entities se
            WHERE se.story_id = :story_id
            ORDER BY se.mentions DESC, se.entity_id
        """), {"story_id": story_id}).fetchall()
        event_rows = session.execute(text("""
            SELECT sve.entity_id::text AS entity_id, sve.event_key,
                   sve.event_at, sve.action_level, sve.evidence
            FROM story_events sve
            WHERE sve.story_id = :story_id
            ORDER BY sve.event_at DESC NULLS LAST, sve.entity_id
        """), {"story_id": story_id}).fetchall()
        article_rows = session.execute(text("""
            SELECT ar.id AS article_id, ar.title, ar.url, ar.published_at,
                   s.name AS source, TRIM(s.country_code) AS country_code,
                   sa.membership_confidence, sa.evidence
            FROM story_articles sa
            JOIN articles ar ON ar.id = sa.article_id
            JOIN sources s ON s.id = ar.source_id
            WHERE sa.story_id = :story_id
            ORDER BY s.country_code, ar.published_at DESC NULLS LAST, ar.id DESC
        """), {"story_id": story_id}).fetchall()

    result = story_to_dict(story)
    result["countries"] = [{
        "country_code": str(_value(row, "country_code")).strip(),
        "country_name": COUNTRY_NAMES.get(str(_value(row, "country_code")).strip(), str(_value(row, "country_code")).strip()),
        "article_count": int(_value(row, "article_count", 0) or 0),
        "source_count": int(_value(row, "source_count", 0) or 0),
        "media_tone": float(_value(row, "media_tone")) if _value(row, "media_tone") is not None else None,
        "first_seen": _iso(_value(row, "first_seen")),
        "last_seen": _iso(_value(row, "last_seen")),
        "primary_url": _value(row, "primary_url"),
    } for row in country_rows]
    result["entities"] = [{
        "entity_id": str(_value(row, "entity_id")),
        "mentions": int(_value(row, "mentions", 0) or 0),
        "confidence": float(_value(row, "confidence", 0) or 0),
        "evidence": _json_object(_value(row, "evidence")),
    } for row in entity_rows]
    result["events"] = [{
        "entity_id": str(_value(row, "entity_id")),
        "event_key": _value(row, "event_key"),
        "event_at": _iso(_value(row, "event_at")),
        "action_level": int(_value(row, "action_level", 1) or 1),
        "evidence": _json_object(_value(row, "evidence")),
    } for row in event_rows]
    primary_countries: set[str] = set()
    articles = []
    for row in article_rows:
        country_code = str(_value(row, "country_code")).strip()
        is_primary = country_code not in primary_countries
        primary_countries.add(country_code)
        articles.append({
            "article_id": _value(row, "article_id"),
            "title": _value(row, "title"),
            "url": _value(row, "url"),
            "published_at": _iso(_value(row, "published_at")),
            "source": _value(row, "source"),
            "country_code": country_code,
            "membership_confidence": float(_value(row, "membership_confidence", 0) or 0),
            "evidence": _json_object(_value(row, "evidence")),
            "is_primary": is_primary,
        })
    result["articles"] = articles
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
    cursor: Optional[str] = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
):
    """Return cross-country stories containing the requested country slice."""

    country = _validate_country(code)
    payload = _list_stories(
        country=country,
        lifecycle=lifecycle,
        min_confidence=min_confidence,
        min_action_level=min_action_level,
        since=since,
        cursor=cursor,
        limit=limit,
    )
    return {"country": country, "name": COUNTRY_NAMES[country], **payload}
