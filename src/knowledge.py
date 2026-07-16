"""Canonical knowledge domain and legacy entity backfill helpers.

The curated :mod:`src.entities` registry remains the extraction input.  This
module gives those legacy keys deterministic canonical identities and copies
mentions into the additive knowledge tables without changing ``analysis.entities``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from uuid import UUID, uuid5

from sqlalchemy import text

from src.entities import ENTITIES


ENTITY_KINDS = frozenset({"person", "organization", "location", "event"})
LEGACY_EXTRACTOR = "legacy_registry"
DEFAULT_EXTRACTOR_VERSION = "src.entities-v1"
_REGISTRY_NAMESPACE = UUID("b9296498-ff56-4aac-86c9-4d197842f484")
_PUNCTUATION_RE = re.compile(r"[_\W]+", flags=re.UNICODE)

_LEGACY_KIND_MAP = {
    "person": "person",
    "org_state": "organization",
    "org_bloc": "organization",
    "company": "organization",
    "military": "organization",
    "media": "organization",
    "concept": "event",
}
_LEGACY_LOCATION_KEYS = {
    "crimea",
    "nord_stream",
    "power_of_siberia",
    "russian_base",
    "zaes",
}


def normalize_entity_name(value: str) -> str:
    """Return the comparison form used for canonical names and aliases."""
    folded = (value or "").casefold().replace("ё", "е")
    return " ".join(_PUNCTUATION_RE.sub(" ", folded).split())


@dataclass(frozen=True)
class EntityAlias:
    entity_id: UUID
    alias: str
    normalized_alias: str = ""
    language: str | None = None
    ambiguous: bool = False
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized = normalize_entity_name(self.normalized_alias or self.alias)
        object.__setattr__(self, "normalized_alias", normalized)


@dataclass(frozen=True)
class CanonicalEntity:
    id: UUID
    kind: str
    canonical_name: str
    normalized_name: str
    labels: Mapping[str, str] = field(default_factory=dict)
    country_codes: tuple[str, ...] = ()
    provenance: Mapping[str, Any] = field(default_factory=dict)
    aliases: tuple[EntityAlias, ...] = ()

    @property
    def node_id(self) -> str:
        return stable_node_id(self.kind, self.id)


@dataclass(frozen=True)
class EntityMention:
    article_id: int
    entity_id: UUID
    mention_text: str | None
    char_start: int | None
    char_end: int | None
    extractor: str
    extractor_version: str
    confidence: float
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BackfillBatch:
    analyses_seen: int
    mentions_upserted: int
    last_analysis_id: int
    done: bool


def stable_node_id(kind: str, entity_id: UUID | str) -> str:
    """Build a stable public graph ID such as ``person:<uuid>``."""
    if kind not in ENTITY_KINDS:
        raise ValueError(f"unsupported entity kind: {kind}")
    return f"{kind}:{UUID(str(entity_id))}"


def resolve_alias(
    value: str,
    aliases: Iterable[EntityAlias],
    *,
    disambiguated_entity_id: UUID | None = None,
) -> UUID | None:
    """Resolve only a unique, unambiguous alias.

    An explicit contextual choice may be supplied by an upstream extractor;
    otherwise duplicate or explicitly ambiguous aliases never auto-resolve.
    """
    normalized = normalize_entity_name(value)
    matches = [alias for alias in aliases if alias.normalized_alias == normalized]
    if not matches:
        return None
    entity_ids = {alias.entity_id for alias in matches}
    if disambiguated_entity_id is not None:
        return disambiguated_entity_id if disambiguated_entity_id in entity_ids else None
    if any(alias.ambiguous for alias in matches) or len(entity_ids) != 1:
        return None
    return next(iter(entity_ids))


def _legacy_kind(registry_key: str, category: str) -> str:
    if registry_key in _LEGACY_LOCATION_KEYS:
        return "location"
    return _LEGACY_KIND_MAP.get(category, "event")


def registry_entity(registry_key: str) -> CanonicalEntity:
    """Convert one current registry entry into a deterministic entity record."""
    key = (registry_key or "").casefold()
    try:
        entry = ENTITIES[key]
    except KeyError as exc:
        raise KeyError(f"unknown legacy entity key: {registry_key}") from exc

    entity_id = uuid5(_REGISTRY_NAMESPACE, f"src.entities:{key}")
    canonical_name = entry["name_ru"] or entry["name_en"]
    provenance = {"source": "src.entities", "legacy_key": key}
    seen: set[str] = set()
    aliases: list[EntityAlias] = []
    for alias in (entry["name_ru"], entry["name_en"], *entry["aliases"]):
        normalized = normalize_entity_name(alias)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        aliases.append(
            EntityAlias(
                entity_id=entity_id,
                alias=alias.strip(),
                normalized_alias=normalized,
                provenance=provenance,
            )
        )

    return CanonicalEntity(
        id=entity_id,
        kind=_legacy_kind(key, entry["category"]),
        canonical_name=canonical_name,
        normalized_name=normalize_entity_name(canonical_name),
        labels={"ru": entry["name_ru"], "en": entry["name_en"]},
        provenance=provenance,
        aliases=tuple(aliases),
    )


def mention_from_legacy_key(
    *,
    article_id: int,
    analysis_id: int,
    registry_key: str,
    extractor_version: str = DEFAULT_EXTRACTOR_VERSION,
) -> EntityMention:
    """Create an evidence-bearing mention from one ``analysis.entities`` key."""
    entity = registry_entity(registry_key)
    return EntityMention(
        article_id=article_id,
        entity_id=entity.id,
        mention_text=None,
        char_start=None,
        char_end=None,
        extractor=LEGACY_EXTRACTOR,
        extractor_version=extractor_version,
        confidence=0.9,
        evidence={
            "source": "analysis.entities",
            "analysis_id": analysis_id,
            "legacy_key": registry_key.casefold(),
        },
    )


def legacy_entity_keys(value: Any) -> tuple[str, ...]:
    """Read legacy JSON entity keys without mutating or replacing the payload."""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Mapping):
        nested = value.get("entities")
        if nested is not None:
            return legacy_entity_keys(nested)
        return tuple(str(key) for key in value)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value if isinstance(item, str))
    return ()


def _json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), ensure_ascii=False, sort_keys=True)


class KnowledgeBackfillService:
    """Transactional operations used by the one-off legacy backfill command."""

    def __init__(self, extractor_version: str = DEFAULT_EXTRACTOR_VERSION):
        self.extractor_version = extractor_version
        self._entity_ids: dict[str, UUID] = {}

    @staticmethod
    def registry_keys(entities: Any) -> tuple[str, ...]:
        """Normalize and deduplicate legacy keys that still exist in the registry."""

        return tuple(
            dict.fromkeys(
                key.strip().casefold()
                for key in legacy_entity_keys(entities)
                if key.strip().casefold() in ENTITIES
            )
        )

    def seed_registry(self, session: Any) -> int:
        """Upsert the curated registry in the caller's current transaction."""
        for key in sorted(ENTITIES):
            self.seed_registry_key(session, key)
        return len(self._entity_ids)

    def seed_registry_key(self, session: Any, registry_key: str) -> UUID:
        """Upsert one current registry record and all of its aliases."""

        entity = registry_entity(registry_key)
        stored_id = session.execute(
            text(
                """
                INSERT INTO canonical_entities
                    (id, kind, canonical_name, normalized_name, labels,
                     country_codes, provenance, updated_at)
                VALUES
                    (:id, :kind, :canonical_name, :normalized_name,
                     CAST(:labels AS jsonb), :country_codes,
                     CAST(:provenance AS jsonb), now())
                ON CONFLICT (id) DO UPDATE SET
                    kind = EXCLUDED.kind,
                    canonical_name = EXCLUDED.canonical_name,
                    normalized_name = EXCLUDED.normalized_name,
                    labels = EXCLUDED.labels,
                    provenance = EXCLUDED.provenance,
                    updated_at = now()
                RETURNING id
                """
            ),
            {
                "id": entity.id,
                "kind": entity.kind,
                "canonical_name": entity.canonical_name,
                "normalized_name": entity.normalized_name,
                "labels": _json(entity.labels),
                "country_codes": list(entity.country_codes),
                "provenance": _json(entity.provenance),
            },
        ).scalar_one()
        stored_uuid = UUID(str(stored_id))
        self._entity_ids[registry_key] = stored_uuid
        for alias in entity.aliases:
            session.execute(
                text(
                    """
                    INSERT INTO entity_aliases
                        (entity_id, alias, normalized_alias, language,
                         ambiguous, provenance)
                    VALUES
                        (:entity_id, :alias, :normalized_alias, :language,
                         :ambiguous, CAST(:provenance AS jsonb))
                    ON CONFLICT (entity_id, normalized_alias) DO UPDATE SET
                        alias = EXCLUDED.alias,
                        language = EXCLUDED.language,
                        ambiguous = EXCLUDED.ambiguous,
                        provenance = EXCLUDED.provenance
                    """
                ),
                {
                    "entity_id": stored_uuid,
                    "alias": alias.alias,
                    "normalized_alias": alias.normalized_alias,
                    "language": alias.language,
                    "ambiguous": alias.ambiguous,
                    "provenance": _json(alias.provenance),
                },
            )
        return stored_uuid

    def backfill_batch(
        self,
        session: Any,
        *,
        after_analysis_id: int = 0,
        batch_size: int = 250,
    ) -> BackfillBatch:
        """Upsert one analysis-ID-ordered batch in the caller's transaction."""
        rows = session.execute(
            text(
                """
                SELECT an.id AS analysis_id, an.article_id, an.entities
                FROM analysis an
                WHERE an.id > :after_analysis_id
                  AND an.entities IS NOT NULL
                ORDER BY an.id
                LIMIT :batch_size
                """
            ),
            {
                "after_analysis_id": after_analysis_id,
                "batch_size": batch_size,
            },
        ).fetchall()
        mentions_upserted = 0
        last_analysis_id = after_analysis_id
        for row in rows:
            values = row._mapping if hasattr(row, "_mapping") else row
            analysis_id = int(values["analysis_id"])
            article_id = int(values["article_id"])
            last_analysis_id = analysis_id
            mentions_upserted += self.upsert_analysis_mentions(
                session,
                analysis_id=analysis_id,
                article_id=article_id,
                entities=values["entities"],
            )
        return BackfillBatch(
            analyses_seen=len(rows),
            mentions_upserted=mentions_upserted,
            last_analysis_id=last_analysis_id,
            done=len(rows) < batch_size,
        )

    def upsert_analysis_mentions(
        self,
        session: Any,
        *,
        analysis_id: int,
        article_id: int,
        entities: Any,
    ) -> int:
        """Upsert unique current-registry mentions for one saved analysis."""

        registry_keys = self.registry_keys(entities)
        for registry_key in registry_keys:
            mention = mention_from_legacy_key(
                article_id=article_id,
                analysis_id=analysis_id,
                registry_key=registry_key,
                extractor_version=self.extractor_version,
            )
            entity_id = self._entity_ids.get(registry_key, mention.entity_id)
            session.execute(
                text(
                    """
                    INSERT INTO article_entity_mentions
                        (article_id, entity_id, mention_text, char_start,
                         char_end, extractor, extractor_version, confidence,
                         evidence)
                    VALUES
                        (:article_id, :entity_id, :mention_text, :char_start,
                         :char_end, :extractor, :extractor_version,
                         :confidence, CAST(:evidence AS jsonb))
                    ON CONFLICT (article_id, entity_id, extractor) DO UPDATE SET
                        mention_text = EXCLUDED.mention_text,
                        char_start = EXCLUDED.char_start,
                        char_end = EXCLUDED.char_end,
                        extractor_version = EXCLUDED.extractor_version,
                        confidence = EXCLUDED.confidence,
                        evidence = EXCLUDED.evidence
                    """
                ),
                {
                    "article_id": mention.article_id,
                    "entity_id": entity_id,
                    "mention_text": mention.mention_text,
                    "char_start": mention.char_start,
                    "char_end": mention.char_end,
                    "extractor": mention.extractor,
                    "extractor_version": mention.extractor_version,
                    "confidence": mention.confidence,
                    "evidence": _json(mention.evidence),
                },
            )
        return len(registry_keys)


def upsert_analysis_mentions(
    session: Any,
    analysis_id: int,
    article_id: int,
    entities: Any,
) -> int:
    """Seed canonical registry records and idempotently write one analysis' mentions."""

    service = KnowledgeBackfillService()
    registry_keys = service.registry_keys(entities)
    if not registry_keys:
        return 0
    for registry_key in registry_keys:
        service.seed_registry_key(session, registry_key)
    return service.upsert_analysis_mentions(
        session,
        analysis_id=analysis_id,
        article_id=article_id,
        entities=registry_keys,
    )


RegistryBackfillService = KnowledgeBackfillService
