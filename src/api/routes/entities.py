"""Canonical entity suggestion and evidence detail endpoints."""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from src.db import get_session
from src.knowledge import normalize_entity_name, stable_node_id


router = APIRouter(prefix="/api/v2/entities", tags=["entities"])


def safe_public_url(value: str | None) -> str | None:
    """Return only absolute HTTP(S) URLs without embedded credentials."""
    if (
        not value
        or "\\" in value
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        return None
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    return value


class EntityQueryService(Protocol):
    def suggest(self, *, query: str, limit: int, offset: int) -> dict[str, Any]: ...

    def detail(
        self,
        *,
        entity_id: UUID,
        limit: int,
        offset: int,
    ) -> dict[str, Any] | None: ...


def _mapping(row: Any) -> Any:
    return row._mapping if hasattr(row, "_mapping") else row


def _label(row: Any) -> str:
    labels = row["labels"] or {}
    return labels.get("ru") or labels.get("en") or row["canonical_name"]


class SqlEntityQueryService:
    """Read-only PostgreSQL query adapter for public entity routes."""

    def suggest(self, *, query: str, limit: int, offset: int) -> dict[str, Any]:
        normalized_query = normalize_entity_name(query)
        if not normalized_query:
            return {"items": [], "limit": limit, "offset": offset, "has_more": False}
        with get_session() as session:
            rows = session.execute(
                text(
                    """
                    SELECT ce.id, ce.kind, ce.canonical_name, ce.normalized_name,
                           ce.labels,
                           ARRAY(
                               SELECT ea.alias
                               FROM entity_aliases ea
                               WHERE ea.entity_id = ce.id
                               ORDER BY ea.normalized_alias, ea.alias
                           ) AS aliases,
                           ARRAY(
                               SELECT ea.normalized_alias
                               FROM entity_aliases ea
                               WHERE ea.entity_id = ce.id
                           ) AS normalized_aliases
                    FROM canonical_entities ce
                    WHERE ce.normalized_name LIKE :prefix
                       OR EXISTS (
                           SELECT 1 FROM entity_aliases ea
                           WHERE ea.entity_id = ce.id
                             AND ea.normalized_alias LIKE :prefix
                       )
                    ORDER BY
                        CASE WHEN ce.normalized_name = :query THEN 0
                             WHEN EXISTS (
                                 SELECT 1 FROM entity_aliases ea
                                 WHERE ea.entity_id = ce.id
                                   AND ea.normalized_alias = :query
                             ) THEN 1
                             ELSE 2 END,
                        ce.canonical_name,
                        ce.id
                    LIMIT :fetch_limit OFFSET :offset
                    """
                ),
                {
                    "query": normalized_query,
                    "prefix": f"{normalized_query}%",
                    "fetch_limit": limit + 1,
                    "offset": offset,
                },
            ).fetchall()

        has_more = len(rows) > limit
        items = []
        for raw in rows[:limit]:
            row = _mapping(raw)
            normalized_aliases = set(row["normalized_aliases"] or ())
            if row["normalized_name"] == normalized_query:
                explanation = "exact canonical name"
            elif normalized_query in normalized_aliases:
                explanation = "exact alias"
            else:
                explanation = "canonical name or alias prefix"
            entity_id = UUID(str(row["id"]))
            items.append(
                {
                    "id": str(entity_id),
                    "node_id": stable_node_id(row["kind"], entity_id),
                    "kind": row["kind"],
                    "label": _label(row),
                    "aliases": list(row["aliases"] or ()),
                    "match_explanation": explanation,
                }
            )
        return {
            "items": items,
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
        }

    def detail(
        self,
        *,
        entity_id: UUID,
        limit: int,
        offset: int,
    ) -> dict[str, Any] | None:
        with get_session() as session:
            raw_entity = session.execute(
                text(
                    """
                    SELECT id, kind, canonical_name, normalized_name, labels,
                           country_codes, provenance, created_at, updated_at
                    FROM canonical_entities
                    WHERE id = :entity_id
                    """
                ),
                {"entity_id": entity_id},
            ).fetchone()
            if raw_entity is None:
                return None
            entity = _mapping(raw_entity)
            aliases = session.execute(
                text(
                    """
                    SELECT alias, normalized_alias, language, ambiguous, provenance
                    FROM entity_aliases
                    WHERE entity_id = :entity_id
                    ORDER BY normalized_alias, alias
                    """
                ),
                {"entity_id": entity_id},
            ).fetchall()
            mentions = session.execute(
                text(
                    """
                    SELECT aem.article_id, aem.mention_text, aem.char_start,
                           aem.char_end, aem.extractor, aem.extractor_version,
                           aem.confidence, aem.evidence, aem.created_at,
                           ar.title, ar.url, ar.published_at
                    FROM article_entity_mentions aem
                    JOIN articles ar ON ar.id = aem.article_id
                    WHERE aem.entity_id = :entity_id
                    ORDER BY ar.published_at DESC NULLS LAST,
                             aem.created_at DESC, aem.article_id DESC
                    LIMIT :fetch_limit OFFSET :offset
                    """
                ),
                {
                    "entity_id": entity_id,
                    "fetch_limit": limit + 1,
                    "offset": offset,
                },
            ).fetchall()

        has_more = len(mentions) > limit
        mention_items = []
        for raw in mentions[:limit]:
            mention = _mapping(raw)
            mention_items.append(
                {
                    "article_id": mention["article_id"],
                    "title": mention["title"],
                    "url": safe_public_url(mention["url"]),
                    "published_at": (
                        mention["published_at"].isoformat()
                        if mention["published_at"]
                        else None
                    ),
                    "mention_text": mention["mention_text"],
                    "char_start": mention["char_start"],
                    "char_end": mention["char_end"],
                    "extractor": mention["extractor"],
                    "extractor_version": mention["extractor_version"],
                    "confidence": float(mention["confidence"]),
                    "evidence": mention["evidence"] or {},
                }
            )

        alias_items = []
        for raw in aliases:
            alias = _mapping(raw)
            alias_items.append(
                {
                    "alias": alias["alias"],
                    "language": alias["language"],
                    "ambiguous": alias["ambiguous"],
                    "provenance": alias["provenance"] or {},
                }
            )
        return {
            "id": str(entity_id),
            "node_id": stable_node_id(entity["kind"], entity_id),
            "kind": entity["kind"],
            "label": _label(entity),
            "canonical_name": entity["canonical_name"],
            "normalized_name": entity["normalized_name"],
            "labels": entity["labels"] or {},
            "country_codes": list(entity["country_codes"] or ()),
            "provenance": entity["provenance"] or {},
            "aliases": alias_items,
            "mentions": {
                "items": mention_items,
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
            },
        }


def get_entity_query_service() -> EntityQueryService:
    return SqlEntityQueryService()


@router.get("/suggest")
def suggest_entities(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: EntityQueryService = Depends(get_entity_query_service),
):
    return service.suggest(query=q, limit=limit, offset=offset)


@router.get("/{entity_id}")
def entity_detail(
    entity_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    service: EntityQueryService = Depends(get_entity_query_service),
):
    result = service.detail(entity_id=entity_id, limit=limit, offset=offset)
    if result is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return result
