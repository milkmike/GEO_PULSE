"""Canonical entity suggestion and evidence detail endpoints."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from src.api.public_urls import safe_public_url
from src.db import get_session
from src.knowledge import normalize_entity_name, stable_node_id


router = APIRouter(prefix="/api/v2/entities", tags=["entities"])
_CURSOR_VERSION = 1
_CURSOR_SCOPES = frozenset({"entity_suggest", "entity_mentions"})


def _validate_cursor_key(scope: str, key: Any) -> dict[str, Any]:
    if not isinstance(key, dict):
        raise ValueError("cursor key must be an object")
    if scope == "entity_suggest":
        if set(key) != {"match_rank", "canonical_name", "id"}:
            raise ValueError("cursor key has invalid suggest fields")
        if type(key["match_rank"]) is not int or key["match_rank"] < 0:
            raise ValueError("cursor match rank is invalid")
        if not isinstance(key["canonical_name"], str) or not key["canonical_name"]:
            raise ValueError("cursor canonical name is invalid")
        try:
            UUID(key["id"])
        except (TypeError, ValueError) as exc:
            raise ValueError("cursor entity ID is invalid") from exc
    elif scope == "entity_mentions":
        if set(key) != {
            "published_at",
            "created_at",
            "article_id",
            "extractor",
        }:
            raise ValueError("cursor key has invalid mention fields")
        for field in ("published_at", "created_at"):
            try:
                timestamp = datetime.fromisoformat(key[field])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"cursor {field} is invalid") from exc
            if timestamp.tzinfo is None:
                raise ValueError(f"cursor {field} must include a timezone")
        if type(key["article_id"]) is not int or key["article_id"] < 1:
            raise ValueError("cursor article ID is invalid")
        if not isinstance(key["extractor"], str) or not key["extractor"]:
            raise ValueError("cursor extractor is invalid")
    else:
        raise ValueError("cursor scope is invalid")
    return key


def encode_entity_cursor(
    *,
    scope: str,
    binding: dict[str, str],
    key: dict[str, Any],
) -> str:
    """Encode a versioned opaque cursor bound to a query or entity."""
    if scope not in _CURSOR_SCOPES:
        raise ValueError("cursor scope is invalid")
    if not isinstance(binding, dict) or not all(
        isinstance(name, str) and isinstance(value, str)
        for name, value in binding.items()
    ):
        raise ValueError("cursor binding is invalid")
    payload = {
        "v": _CURSOR_VERSION,
        "scope": scope,
        "binding": binding,
        "key": _validate_cursor_key(scope, key),
    }
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_entity_cursor(
    token: str,
    *,
    scope: str,
    binding: dict[str, str],
) -> dict[str, Any]:
    """Decode and validate an opaque cursor against the current request."""
    try:
        padding = "=" * (-len(token) % 4)
        raw = base64.b64decode(
            token + padding,
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("cursor is malformed") from exc
    if not isinstance(payload, dict):
        raise ValueError("cursor payload is invalid")
    if payload.get("v") != _CURSOR_VERSION or payload.get("scope") != scope:
        raise ValueError("cursor scope or version is invalid")
    if payload.get("binding") != binding:
        raise ValueError("cursor binding does not match the request")
    return _validate_cursor_key(scope, payload.get("key"))


class EntityQueryService(Protocol):
    def suggest(
        self,
        *,
        query: str,
        limit: int,
        offset: int,
        cursor: dict[str, Any] | None,
    ) -> dict[str, Any]: ...

    def detail(
        self,
        *,
        entity_id: UUID,
        limit: int,
        offset: int,
        cursor: dict[str, Any] | None,
    ) -> dict[str, Any] | None: ...


def _mapping(row: Any) -> Any:
    return row._mapping if hasattr(row, "_mapping") else row


def _label(row: Any) -> str:
    labels = row["labels"] or {}
    return labels.get("ru") or labels.get("en") or row["canonical_name"]


class SqlEntityQueryService:
    """Read-only PostgreSQL query adapter for public entity routes."""

    def suggest(
        self,
        *,
        query: str,
        limit: int,
        offset: int,
        cursor: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_query = normalize_entity_name(query)
        if not normalized_query:
            return {
                "items": [],
                "limit": limit,
                "offset": offset,
                "has_more": False,
                "next_cursor": None,
            }
        if cursor is not None and offset:
            raise ValueError("offset cannot be combined with a cursor")
        cursor_rank = cursor["match_rank"] if cursor else None
        cursor_name = cursor["canonical_name"] if cursor else None
        cursor_id = cursor["id"] if cursor else None
        with get_session() as session:
            rows = session.execute(
                text(
                    """
                    WITH candidates AS (
                        SELECT ce.id, ce.kind, ce.canonical_name,
                               ce.normalized_name, ce.labels,
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
                               ) AS normalized_aliases,
                               CASE WHEN ce.normalized_name = :query THEN 0
                                    WHEN EXISTS (
                                        SELECT 1 FROM entity_aliases ea
                                        WHERE ea.entity_id = ce.id
                                          AND ea.normalized_alias = :query
                                    ) THEN 1
                                    ELSE 2 END AS match_rank
                        FROM canonical_entities ce
                        WHERE ce.normalized_name LIKE :prefix
                           OR EXISTS (
                               SELECT 1 FROM entity_aliases ea
                               WHERE ea.entity_id = ce.id
                                 AND ea.normalized_alias LIKE :prefix
                           )
                    )
                    SELECT c.*
                    FROM candidates c
                    WHERE :cursor_rank IS NULL
                       OR c.match_rank > :cursor_rank
                       OR (
                           c.match_rank = :cursor_rank
                           AND c.canonical_name > :cursor_name
                       )
                       OR (
                           c.match_rank = :cursor_rank
                           AND c.canonical_name = :cursor_name
                           AND c.id > CAST(:cursor_id AS uuid)
                       )
                    ORDER BY c.match_rank, c.canonical_name, c.id
                    LIMIT :fetch_limit OFFSET :offset
                    """
                ),
                {
                    "query": normalized_query,
                    "prefix": f"{normalized_query}%",
                    "fetch_limit": limit + 1,
                    "offset": offset,
                    "cursor_rank": cursor_rank,
                    "cursor_name": cursor_name,
                    "cursor_id": cursor_id,
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
        next_cursor = None
        if has_more:
            last = _mapping(rows[limit - 1])
            next_cursor = encode_entity_cursor(
                scope="entity_suggest",
                binding={"q": normalized_query},
                key={
                    "match_rank": int(last["match_rank"]),
                    "canonical_name": last["canonical_name"],
                    "id": str(last["id"]),
                },
            )
        return {
            "items": items,
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "next_cursor": next_cursor,
        }

    def detail(
        self,
        *,
        entity_id: UUID,
        limit: int,
        offset: int,
        cursor: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if cursor is not None and offset:
            raise ValueError("offset cannot be combined with a cursor")
        cursor_published_at = cursor["published_at"] if cursor else None
        cursor_created_at = cursor["created_at"] if cursor else None
        cursor_article_id = cursor["article_id"] if cursor else None
        cursor_extractor = cursor["extractor"] if cursor else None
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
                      AND (
                          :cursor_published_at IS NULL
                          OR ar.published_at < CAST(:cursor_published_at AS timestamptz)
                          OR (
                              ar.published_at = CAST(:cursor_published_at AS timestamptz)
                              AND aem.created_at < CAST(:cursor_created_at AS timestamptz)
                          )
                          OR (
                              ar.published_at = CAST(:cursor_published_at AS timestamptz)
                              AND aem.created_at = CAST(:cursor_created_at AS timestamptz)
                              AND aem.article_id < :cursor_article_id
                          )
                          OR (
                              ar.published_at = CAST(:cursor_published_at AS timestamptz)
                              AND aem.created_at = CAST(:cursor_created_at AS timestamptz)
                              AND aem.article_id = :cursor_article_id
                              AND aem.extractor < :cursor_extractor
                          )
                      )
                    ORDER BY ar.published_at DESC,
                             aem.created_at DESC, aem.article_id DESC,
                             aem.extractor DESC
                    LIMIT :fetch_limit OFFSET :offset
                    """
                ),
                {
                    "entity_id": entity_id,
                    "fetch_limit": limit + 1,
                    "offset": offset,
                    "cursor_published_at": cursor_published_at,
                    "cursor_created_at": cursor_created_at,
                    "cursor_article_id": cursor_article_id,
                    "cursor_extractor": cursor_extractor,
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
        next_cursor = None
        if has_more:
            last = _mapping(mentions[limit - 1])
            next_cursor = encode_entity_cursor(
                scope="entity_mentions",
                binding={"entity_id": str(entity_id)},
                key={
                    "published_at": last["published_at"].isoformat(),
                    "created_at": last["created_at"].isoformat(),
                    "article_id": int(last["article_id"]),
                    "extractor": last["extractor"],
                },
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
            "created_at": entity["created_at"].isoformat(),
            "updated_at": entity["updated_at"].isoformat(),
            "aliases": alias_items,
            "mentions": {
                "items": mention_items,
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
                "next_cursor": next_cursor,
            },
        }


def get_entity_query_service() -> EntityQueryService:
    return SqlEntityQueryService()


@router.get("/suggest")
def suggest_entities(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    cursor: str | None = Query(None, max_length=2048),
    service: EntityQueryService = Depends(get_entity_query_service),
):
    if cursor is not None and offset:
        raise HTTPException(status_code=422, detail="Cursor cannot be combined with offset")
    cursor_key = None
    if cursor is not None:
        try:
            cursor_key = decode_entity_cursor(
                cursor,
                scope="entity_suggest",
                binding={"q": normalize_entity_name(q)},
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid cursor") from exc
    return service.suggest(
        query=q,
        limit=limit,
        offset=offset,
        cursor=cursor_key,
    )


@router.get("/{entity_id}")
def entity_detail(
    entity_id: UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    cursor: str | None = Query(None, max_length=2048),
    service: EntityQueryService = Depends(get_entity_query_service),
):
    if cursor is not None and offset:
        raise HTTPException(status_code=422, detail="Cursor cannot be combined with offset")
    cursor_key = None
    if cursor is not None:
        try:
            cursor_key = decode_entity_cursor(
                cursor,
                scope="entity_mentions",
                binding={"entity_id": str(entity_id)},
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid cursor") from exc
    result = service.detail(
        entity_id=entity_id,
        limit=limit,
        offset=offset,
        cursor=cursor_key,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Entity not found")
    return result
