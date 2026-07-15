"""Explainable article-search API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Callable
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from src.search import (
    SearchQuery,
    SearchTimeoutError,
    decode_cursor,
    encode_cursor,
    validate_search_query,
)


router = APIRouter(prefix="/api/v2/search", tags=["search"])
SearchService = Callable[[SearchQuery], dict]


def get_search_service() -> SearchService:
    """Resolve the database-backed service; replaceable in route tests."""

    from src.search import search_articles

    return search_articles


def _safe_http_url(value: object) -> str | None:
    if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
        return None
    try:
        parsed = urlparse(value)
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return None
        parsed.port
    except ValueError:
        return None
    return value


def _serialize_cursor(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        decode_cursor(value)
        return value
    if not isinstance(value, dict):
        raise ValueError("invalid search cursor value")
    published_at = datetime.fromisoformat(str(value["published_at"]))
    return encode_cursor(
        float(value["relevance_score"]),
        published_at,
        int(value["article_id"]),
        datetime.fromisoformat(str(value["ranking_at"])),
    )


@router.get("/articles")
def search_articles_endpoint(
    q: str = Query(default=""),
    country: str | None = Query(default=None),
    topic: str | None = Query(default=None),
    entity_id: UUID | None = Query(default=None),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    tier: str | None = Query(default=None),
    language: str | None = Query(default=None),
    sort: str = Query(default="relevance", pattern="^(relevance|newest)$"),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    service: SearchService = Depends(get_search_service),
):
    """Search only locally indexed article rows; no provider work is triggered."""

    normalized_country = country.strip().upper() if country and country.strip() else None
    normalized_topic = topic.strip() if topic and topic.strip() else None
    normalized_tier = tier.strip() if tier and tier.strip() else None
    normalized_language = language.strip() if language and language.strip() else None
    filters = {
        "country": normalized_country,
        "topic": normalized_topic,
        "entity_id": entity_id,
        "from": date_from,
        "to": date_to,
        "tier": normalized_tier,
        "language": normalized_language,
    }
    try:
        normalized_query = validate_search_query(q, filters)
        if date_from and date_to and date_from > date_to:
            raise ValueError("from must not be after to")
        if cursor:
            decode_cursor(cursor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    query = SearchQuery(
        q=normalized_query,
        country=normalized_country,
        topic=normalized_topic,
        entity_id=str(entity_id) if entity_id else None,
        date_from=date_from,
        date_to=date_to,
        tier=normalized_tier,
        language=normalized_language,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )
    try:
        page = service(query)
    except SearchTimeoutError as exc:
        raise HTTPException(
            status_code=503,
            detail="article search timed out; retry the request",
        ) from exc
    items = []
    for raw_item in page.get("items", []):
        item = dict(raw_item)
        item["url"] = _safe_http_url(item.get("url"))
        items.append(item)

    return {
        "query": normalized_query,
        "filters": filters,
        "sort": sort,
        "limit": limit,
        "semantic_search": "unavailable",
        "items": items,
        "candidate_count": int(page.get("candidate_count", len(items))),
        "next_cursor": _serialize_cursor(page.get("next_cursor")),
    }
