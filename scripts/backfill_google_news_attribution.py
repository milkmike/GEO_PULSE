#!/usr/bin/env python3
"""Backfill legacy Google News publisher attribution without provenance rewrites.

The command is read-only unless ``--apply`` is supplied. Apply mode commits
bounded keyset batches and persists the cursor only after each transaction has
committed successfully.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from typing import Any, Mapping

from sqlalchemy import text

from src.collectors.publisher_attribution import normalize_publisher_domain
from src.db import get_session, wait_for_db


logger = logging.getLogger("backfill-google-news-attribution")

CHECKPOINT_VERSION = 2
DEFAULT_CHECKPOINT = Path("backups/google-news-attribution-checkpoint.json")
DEFAULT_SINCE_DAYS = 104
DEFAULT_BATCH_SIZE = 500
MAX_BATCH_SIZE = 1000
_FINAL_SUFFIX_SEPARATOR = re.compile(r" (?:-|–|—) ")


REGISTRY_SQL = """
    /* gnews-backfill:registry */
    SELECT registry.domain,
           registry.publisher_source_id,
           registry.country_code,
           source.name AS source_name,
           source.url AS source_url
    FROM publisher_domains registry
    JOIN sources source ON source.id = registry.publisher_source_id
    WHERE registry.status = 'verified'
    ORDER BY registry.domain, registry.publisher_source_id
"""

ARTICLE_BATCH_SQL = """
    /* gnews-backfill:batch */
    SELECT article.id, article.source_id, article.external_id, article.title,
           article.url, article.resolved_url, article.published_at,
           article.title_normalized, article.publisher_source_id,
           article.publisher_name, article.publisher_domain,
           article.geo_country_code, article.geo_status, article.geo_method,
           article.geo_confidence, article.geo_verified_at,
           source.country_code AS discovery_country_code
    FROM articles article
    JOIN sources source ON source.id = article.source_id
    WHERE source.config->>'feed_mode' = 'publisher_discovery'
      AND article.published_at >= NOW() - make_interval(days => :since_days)
      AND article.publisher_source_id IS NULL
      AND article.geo_method IS NULL
      AND article.geo_verified_at IS NULL
      AND article.geo_status IN (
          'source_verified', 'unverified', 'legacy_unverified'
      )
      AND article.id > :last_id
    ORDER BY article.id
    LIMIT :batch_size
"""

CLASSIFIED_UPDATE_SQL = """
    /* gnews-backfill:update-classified */
    UPDATE articles
    SET publisher_source_id = :publisher_source_id,
        publisher_name = :publisher_name,
        publisher_domain = :publisher_domain,
        geo_country_code = :country_code,
        geo_status = :status,
        geo_method = 'legacy_title_suffix',
        geo_confidence = 1.000,
        geo_verified_at = NOW()
    WHERE id = :article_id
      AND publisher_source_id IS NULL
      AND geo_method IS NULL
      AND geo_verified_at IS NULL
      AND geo_status IN (
          'source_verified', 'unverified', 'legacy_unverified'
      )
      AND (
        publisher_source_id IS DISTINCT FROM :publisher_source_id
        OR publisher_name IS DISTINCT FROM :publisher_name
        OR publisher_domain IS DISTINCT FROM :publisher_domain
        OR geo_country_code IS DISTINCT FROM :country_code
        OR geo_status IS DISTINCT FROM :status
        OR geo_method IS DISTINCT FROM 'legacy_title_suffix'
        OR geo_confidence IS DISTINCT FROM 1.000
        OR geo_verified_at IS NULL
      )
"""

UNCLASSIFIED_UPDATE_SQL = """
    /* gnews-backfill:update-unclassified */
    UPDATE articles
    SET geo_status = 'legacy_unverified'
    WHERE id = ANY(CAST(:article_ids AS INTEGER[]))
      AND publisher_source_id IS NULL
      AND geo_method IS NULL
      AND geo_verified_at IS NULL
      AND geo_status IN (
          'source_verified', 'unverified', 'legacy_unverified'
      )
      AND geo_status IS DISTINCT FROM 'legacy_unverified'
"""

ARTICLE_HIGH_WATER_SQL = """
    /* gnews-backfill:article-high-water */
    SELECT COALESCE(MAX(id), 0) AS max_article_id
    FROM articles
"""

INVARIANT_COUNTS_SQL = """
    /* gnews-backfill:invariant-counts */
    SELECT (SELECT COUNT(*) FROM articles) AS article_rows,
           (SELECT COUNT(*) FROM articles WHERE id <= :max_article_id)
               AS protected_article_rows,
           (SELECT COUNT(*) FROM analysis) AS analysis_rows,
           (SELECT COUNT(*) FROM story_articles) AS story_article_rows
"""

PROVENANCE_SQL = """
    /* gnews-backfill:provenance */
    SELECT id, source_id, external_id, url
    FROM articles
    WHERE id <= :max_article_id
    ORDER BY id
"""

DEDUP_CANDIDATES_SQL = """
    /* gnews-backfill:dedup-candidates */
    WITH affected AS (
        SELECT article.id, publisher.id AS publisher_id,
               publisher.country_code AS country_code,
               article.external_id, article.title_normalized,
               article.published_at
        FROM articles article
        JOIN sources discovery ON discovery.id = article.source_id
        JOIN sources publisher ON publisher.id = CASE
            WHEN COALESCE(
                discovery.config->>'feed_mode', 'publisher'
            ) = 'publisher_discovery'
                THEN article.publisher_source_id
            ELSE COALESCE(article.publisher_source_id, article.source_id)
        END
        WHERE article.id = ANY(CAST(:affected_ids AS INTEGER[]))
          AND article.geo_status IN (
              'source_verified', 'publisher_verified', 'publisher_reassigned'
          )
    ), candidate_ids AS (
        SELECT affected.id
        FROM affected

        UNION

        SELECT candidate.id
        FROM affected
        JOIN articles candidate
          ON candidate.publisher_source_id = affected.publisher_id
         AND candidate.external_id = affected.external_id
        WHERE affected.external_id IS NOT NULL
          AND affected.external_id <> ''

        UNION

        SELECT candidate.id
        FROM affected
        JOIN articles candidate
          ON candidate.source_id = affected.publisher_id
         AND candidate.external_id = affected.external_id
        JOIN sources candidate_discovery
          ON candidate_discovery.id = candidate.source_id
        WHERE affected.external_id IS NOT NULL
          AND affected.external_id <> ''
          AND candidate.publisher_source_id IS NULL
          AND COALESCE(
              candidate_discovery.config->>'feed_mode', 'publisher'
          ) <> 'publisher_discovery'

        UNION

        SELECT candidate.id
        FROM affected
        JOIN articles candidate
          ON candidate.title_normalized = affected.title_normalized
         AND candidate.published_at BETWEEN
             affected.published_at - INTERVAL '48 hours'
             AND affected.published_at + INTERVAL '48 hours'
        JOIN sources candidate_publisher
          ON candidate_publisher.id = candidate.publisher_source_id
         AND candidate_publisher.country_code = affected.country_code
        WHERE affected.title_normalized IS NOT NULL
          AND affected.title_normalized <> ''

        UNION

        SELECT candidate.id
        FROM affected
        JOIN articles candidate
          ON candidate.title_normalized = affected.title_normalized
         AND candidate.published_at BETWEEN
             affected.published_at - INTERVAL '48 hours'
             AND affected.published_at + INTERVAL '48 hours'
        JOIN sources candidate_discovery
          ON candidate_discovery.id = candidate.source_id
         AND candidate_discovery.country_code = affected.country_code
        WHERE affected.title_normalized IS NOT NULL
          AND affected.title_normalized <> ''
          AND candidate.publisher_source_id IS NULL
          AND COALESCE(
              candidate_discovery.config->>'feed_mode', 'publisher'
          ) <> 'publisher_discovery'
    )
    SELECT candidate.id,
           candidate_publisher.id AS publisher_id,
           candidate_publisher.country_code AS country_code,
           candidate.external_id, candidate.title_normalized,
           candidate.published_at, candidate.is_duplicate,
           candidate.duplicate_of, candidate.reprint_count
    FROM candidate_ids
    JOIN articles candidate ON candidate.id = candidate_ids.id
    JOIN sources candidate_discovery
      ON candidate_discovery.id = candidate.source_id
    JOIN sources candidate_publisher ON candidate_publisher.id = CASE
        WHEN COALESCE(
            candidate_discovery.config->>'feed_mode', 'publisher'
        ) = 'publisher_discovery'
            THEN candidate.publisher_source_id
            ELSE COALESCE(candidate.publisher_source_id, candidate.source_id)
        END
    WHERE candidate.geo_status IN (
        'source_verified', 'publisher_verified', 'publisher_reassigned'
    )
    ORDER BY candidate.id
"""

PUBLISHER_EXTERNAL_CONFLICT_SQL = """
    /* gnews-backfill:publisher-external-conflict */
    SELECT id
    FROM articles
    WHERE publisher_source_id = :publisher_source_id
      AND external_id = :external_id
      AND id <> :article_id
    ORDER BY id
    LIMIT 1
"""

DUPLICATE_FAMILY_SQL = """
    /* gnews-backfill:duplicate-family */
    WITH RECURSIVE family AS (
        SELECT id
        FROM articles
        WHERE id = ANY(CAST(:seed_ids AS INTEGER[]))
        UNION
        SELECT related.id
        FROM family current
        JOIN articles member ON member.id = current.id
        JOIN articles related ON (
            related.duplicate_of = current.id
            OR related.id = member.duplicate_of
        )
    )
    SELECT id
    FROM family
    ORDER BY id
"""

DEDUP_UPDATE_SQL = """
    /* gnews-backfill:update-duplicate */
    UPDATE articles
    SET is_duplicate = :is_duplicate,
        duplicate_of = :duplicate_of,
        reprint_count = :reprint_count
    WHERE id = :article_id
      AND (
        is_duplicate IS DISTINCT FROM :is_duplicate
        OR duplicate_of IS DISTINCT FROM :duplicate_of
        OR reprint_count IS DISTINCT FROM :reprint_count
      )
"""


@dataclass(frozen=True)
class BackfillReport:
    mode: str
    scanned: int
    classifiable: int
    unclassified: int
    updated: int
    duplicates_updated: int
    batches: int
    last_id: int
    done: bool
    counts: Mapping[str, Mapping[str, int]]
    matrix: Mapping[str, Mapping[str, int]]
    invariants: Mapping[str, Any]


@dataclass(frozen=True)
class _Publisher:
    source_id: int
    source_name: str
    country_code: str
    domains: tuple[str, ...]


@dataclass(frozen=True)
class _Classification:
    publisher_source_id: int
    publisher_name: str
    publisher_domain: str
    country_code: str
    status: str


class JsonCheckpointStore:
    """Atomic file checkpoint; compatible stores need only load/save methods."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid checkpoint {self.path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"invalid checkpoint {self.path}: expected object")
        return payload

    def save(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            state,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            default=_json_default,
        )
        fd, temporary_name = tempfile.mkstemp(
            dir=self.path.parent,
            prefix=f".{self.path.name}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping.get(name, default)
    return getattr(row, name, default)


def _mapping_rows(result: Any) -> list[Mapping[str, Any]]:
    mappings = getattr(result, "mappings", None)
    if mappings is not None:
        return list(mappings().all())
    return [dict(getattr(row, "_mapping", row)) for row in result.fetchall()]


def _normalize_name(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    normalized = normalized.replace("ё", "е")
    normalized = re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE)
    return " ".join(normalized.split())


def _publisher_suffix(title: str | None) -> str | None:
    if not isinstance(title, str):
        return None
    matches = list(_FINAL_SUFFIX_SEPARATOR.finditer(title))
    if not matches:
        return None
    match = matches[-1]
    if not title[:match.start()].strip():
        return None
    suffix = title[match.end():].strip()
    return suffix or None


def _publisher_map(session: Any) -> dict[str, _Publisher]:
    rows = _mapping_rows(session.execute(text(REGISTRY_SQL)))
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        normalized = _normalize_name(str(_value(row, "source_name", "")))
        if normalized:
            grouped[normalized].append(row)

    result: dict[str, _Publisher] = {}
    for normalized, candidates in grouped.items():
        source_ids = {int(_value(row, "publisher_source_id")) for row in candidates}
        if len(source_ids) != 1:
            continue
        source_id = next(iter(source_ids))
        source_rows = [
            row for row in candidates
            if int(_value(row, "publisher_source_id")) == source_id
        ]
        countries = {
            str(_value(row, "country_code", "")).upper()
            for row in source_rows
        }
        if len(countries) != 1:
            continue
        domains = tuple(sorted({
            domain
            for row in source_rows
            for domain in (normalize_publisher_domain(_value(row, "domain")),)
            if domain
        }))
        if not domains:
            continue
        result[normalized] = _Publisher(
            source_id=source_id,
            source_name=str(_value(source_rows[0], "source_name")),
            country_code=next(iter(countries)),
            domains=domains,
        )
    return result


def _classify(row: Mapping[str, Any], publishers: Mapping[str, _Publisher]) -> (
    _Classification | None
):
    suffix = _publisher_suffix(_value(row, "title"))
    publisher = publishers.get(_normalize_name(suffix)) if suffix else None
    if publisher is None:
        return None
    resolved_domain = normalize_publisher_domain(_value(row, "resolved_url"))
    stored_domain = normalize_publisher_domain(_value(row, "publisher_domain"))
    publisher_domain = next(
        (
            domain for domain in (resolved_domain, stored_domain)
            if domain in publisher.domains
        ),
        publisher.domains[0],
    )
    discovery_country = str(_value(row, "discovery_country_code", "")).upper()
    status = (
        "publisher_verified"
        if discovery_country == publisher.country_code
        else "publisher_reassigned"
    )
    return _Classification(
        publisher_source_id=publisher.source_id,
        publisher_name=publisher.source_name,
        publisher_domain=publisher_domain,
        country_code=publisher.country_code,
        status=status,
    )


def _snapshot_invariants(
    session: Any,
    *,
    max_article_id: int | None = None,
) -> dict[str, Any]:
    if max_article_id is None:
        high_water = _mapping_rows(
            session.execute(text(ARTICLE_HIGH_WATER_SQL))
        )[0]
        max_article_id = int(_value(high_water, "max_article_id", 0))
    counts = _mapping_rows(session.execute(
        text(INVARIANT_COUNTS_SQL),
        {"max_article_id": max_article_id},
    ))[0]
    digest = hashlib.sha256()
    for row in _mapping_rows(session.execute(
        text(PROVENANCE_SQL),
        {"max_article_id": max_article_id},
    )):
        payload = [
            int(_value(row, "id")),
            int(_value(row, "source_id")),
            _value(row, "external_id"),
            _value(row, "url"),
        ]
        digest.update(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        digest.update(b"\n")
    return {
        "max_article_id": max_article_id,
        "article_rows": int(_value(counts, "article_rows", 0)),
        "protected_article_rows": int(
            _value(counts, "protected_article_rows", 0)
        ),
        "analysis_rows": int(_value(counts, "analysis_rows", 0)),
        "story_article_rows": int(_value(counts, "story_article_rows", 0)),
        "provenance_sha256": digest.hexdigest(),
    }


def _invariant_checks(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> dict[str, bool]:
    return {
        "high_water_unchanged": (
            int(after["max_article_id"]) == int(before["max_article_id"])
        ),
        "protected_article_rows_unchanged": (
            int(after["protected_article_rows"])
            == int(before["protected_article_rows"])
        ),
        "provenance_unchanged": (
            after["provenance_sha256"] == before["provenance_sha256"]
        ),
        "article_rows_non_decreasing": (
            int(after["article_rows"]) >= int(before["article_rows"])
        ),
        "analysis_rows_non_decreasing": (
            int(after["analysis_rows"]) >= int(before["analysis_rows"])
        ),
        "story_article_rows_non_decreasing": (
            int(after["story_article_rows"])
            >= int(before["story_article_rows"])
        ),
    }


def _rowcount(result: Any) -> int:
    return max(0, int(getattr(result, "rowcount", 0) or 0))


def _dedup_connected(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_external = _value(left, "external_id")
    right_external = _value(right, "external_id")
    if (
        left_external
        and right_external
        and left_external == right_external
        and int(_value(left, "publisher_id")) == int(_value(right, "publisher_id"))
    ):
        return True
    left_title = _value(left, "title_normalized")
    right_title = _value(right, "title_normalized")
    if not left_title or left_title != right_title:
        return False
    left_country = str(_value(left, "country_code", "")).upper()
    right_country = str(_value(right, "country_code", "")).upper()
    if not left_country or left_country != right_country:
        return False
    left_time = _value(left, "published_at")
    right_time = _value(right, "published_at")
    return bool(
        isinstance(left_time, datetime)
        and isinstance(right_time, datetime)
        and abs(left_time - right_time) <= timedelta(hours=48)
    )


def _claim_publisher_external_id(
    session: Any,
    row: Mapping[str, Any],
    classification: _Classification,
    claims: dict[tuple[int, str], int],
) -> int | None:
    external_id = _value(row, "external_id")
    if external_id is None:
        return None
    external_id = str(external_id)
    article_id = int(_value(row, "id"))
    key = (classification.publisher_source_id, external_id)
    claimed_id = claims.get(key)
    if claimed_id is not None and claimed_id != article_id:
        return claimed_id
    rows = _mapping_rows(session.execute(
        text(PUBLISHER_EXTERNAL_CONFLICT_SQL),
        {
            "publisher_source_id": classification.publisher_source_id,
            "external_id": external_id,
            "article_id": article_id,
        },
    ))
    if rows:
        conflict_id = int(_value(rows[0], "id"))
        claims[key] = conflict_id
        return conflict_id
    claims[key] = article_id
    return None


def _reconcile_exact_conflict(
    session: Any,
    article_id: int,
    conflict_id: int,
) -> int:
    rows = _mapping_rows(session.execute(
        text(DUPLICATE_FAMILY_SQL),
        {"seed_ids": [article_id, conflict_id]},
    ))
    family_ids = sorted({
        article_id,
        conflict_id,
        *(int(_value(row, "id")) for row in rows),
    })
    parent_id = family_ids[0]
    updated = 0
    for member_id in family_ids:
        is_duplicate = member_id != parent_id
        result = session.execute(text(DEDUP_UPDATE_SQL), {
            "article_id": member_id,
            "is_duplicate": is_duplicate,
            "duplicate_of": parent_id if is_duplicate else None,
            "reprint_count": len(family_ids) - 1 if not is_duplicate else 0,
        })
        updated += _rowcount(result)
    return updated


def _reconcile_duplicates(session: Any, affected_ids: list[int]) -> int:
    if not affected_ids:
        return 0
    rows = _mapping_rows(session.execute(
        text(DEDUP_CANDIDATES_SQL),
        {"affected_ids": affected_ids},
    ))
    if len(rows) < 2:
        return 0

    parents = list(range(len(rows)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left in range(len(rows)):
        for right in range(left + 1, len(rows)):
            if _dedup_connected(rows[left], rows[right]):
                union(left, right)

    components: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows):
        components[find(index)].append(row)

    affected = set(affected_ids)
    updated = 0
    for component in components.values():
        if len(component) < 2 or not any(
            int(_value(row, "id")) in affected for row in component
        ):
            continue
        ordered = sorted(component, key=lambda row: int(_value(row, "id")))
        parent_id = int(_value(ordered[0], "id"))
        for index, row in enumerate(ordered):
            article_id = int(_value(row, "id"))
            result = session.execute(text(DEDUP_UPDATE_SQL), {
                "article_id": article_id,
                "is_duplicate": index > 0,
                "duplicate_of": parent_id if index > 0 else None,
                "reprint_count": len(ordered) - 1 if index == 0 else 0,
            })
            updated += _rowcount(result)
    return updated


def _checkpoint_store(checkpoint: Any) -> Any:
    if checkpoint is None:
        return JsonCheckpointStore(DEFAULT_CHECKPOINT)
    if hasattr(checkpoint, "load") and hasattr(checkpoint, "save"):
        return checkpoint
    return JsonCheckpointStore(Path(checkpoint))


def _empty_audit() -> dict[str, Any]:
    return {
        "scanned": 0,
        "classifiable": 0,
        "unclassified": 0,
        "updated": 0,
        "duplicates_updated": 0,
        "batches": 0,
        "counts": {
            "discovery_country": {},
            "publisher_country": {},
            "status": {},
            "domain": {},
        },
        "matrix": {},
    }


def _validated_state(
    store: Any,
    *,
    since_days: int,
    before: Mapping[str, Any],
) -> dict[str, Any]:
    state = store.load()
    if state is None:
        state = {
            "version": CHECKPOINT_VERSION,
            "since_days": since_days,
            "last_id": 0,
            "done": False,
            "before_invariants": dict(before),
            "audit": _empty_audit(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        store.save(state)
        return state
    if state.get("version") != CHECKPOINT_VERSION:
        raise ValueError(f"unsupported checkpoint version: {state.get('version')!r}")
    if int(state.get("since_days", 0)) != since_days:
        raise ValueError("checkpoint since_days does not match this run")
    if not isinstance(state.get("before_invariants"), Mapping):
        raise ValueError("checkpoint is missing before_invariants")
    required_invariants = {
        "max_article_id",
        "article_rows",
        "protected_article_rows",
        "analysis_rows",
        "story_article_rows",
        "provenance_sha256",
    }
    missing_invariants = required_invariants.difference(
        state["before_invariants"]
    )
    if missing_invariants:
        missing = ", ".join(sorted(missing_invariants))
        raise ValueError(
            f"checkpoint before_invariants is missing: {missing}"
        )
    if not isinstance(state.get("audit"), Mapping):
        raise ValueError("checkpoint is missing cumulative audit state")
    return dict(state)


def _sorted_counts(counters: Mapping[str, Counter]) -> dict[str, dict[str, int]]:
    return {
        name: dict(sorted(counter.items()))
        for name, counter in counters.items()
    }


def _sorted_matrix(
    matrix: Mapping[str, Counter],
) -> dict[str, dict[str, int]]:
    return {
        country: dict(sorted(destinations.items()))
        for country, destinations in sorted(matrix.items())
    }


def _audit_payload(
    *,
    scanned: int,
    classifiable: int,
    unclassified: int,
    updated: int,
    duplicates_updated: int,
    batches: int,
    counters: Mapping[str, Counter],
    matrix: Mapping[str, Counter],
) -> dict[str, Any]:
    return {
        "scanned": scanned,
        "classifiable": classifiable,
        "unclassified": unclassified,
        "updated": updated,
        "duplicates_updated": duplicates_updated,
        "batches": batches,
        "counts": _sorted_counts(counters),
        "matrix": _sorted_matrix(matrix),
    }


def run_backfill(
    *,
    since_days: int = DEFAULT_SINCE_DAYS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    apply: bool = False,
    checkpoint: Any = None,
) -> BackfillReport:
    """Classify legacy discovery articles in resumable, bounded transactions."""

    if since_days < 1:
        raise ValueError("since_days must be positive")
    if not 1 <= batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")

    with get_session() as session:
        publishers = _publisher_map(session)
        current_before = _snapshot_invariants(session)

    store = _checkpoint_store(checkpoint)
    if apply:
        state = _validated_state(
            store,
            since_days=since_days,
            before=current_before,
        )
        before = dict(state["before_invariants"])
        last_id = int(state.get("last_id", 0))
        done = bool(state.get("done", False))
    else:
        state = {}
        before = current_before
        last_id = 0
        done = False

    audit = dict(state["audit"]) if apply else _empty_audit()
    saved_counts = audit.get("counts", {})
    counters = {
        name: Counter(saved_counts.get(name, {}))
        for name in (
            "discovery_country", "publisher_country", "status", "domain",
        )
    }
    matrix: defaultdict[str, Counter] = defaultdict(Counter)
    for discovery_country, destinations in audit.get("matrix", {}).items():
        matrix[str(discovery_country)].update(destinations)
    scanned = int(audit.get("scanned", 0))
    classifiable = int(audit.get("classifiable", 0))
    unclassified = int(audit.get("unclassified", 0))
    updated = int(audit.get("updated", 0))
    duplicates_updated = int(audit.get("duplicates_updated", 0))
    batches = int(audit.get("batches", 0))
    publisher_external_claims: dict[tuple[int, str], int] = {}

    while not done:
        with get_session() as session:
            rows = _mapping_rows(session.execute(text(ARTICLE_BATCH_SQL), {
                "since_days": since_days,
                "last_id": last_id,
                "batch_size": batch_size,
            }))
            if not rows:
                done = True
                batch_last_id = last_id
                batch_updated = 0
                batch_duplicates = 0
                classifications: list[int] = []
            else:
                batch_last_id = int(_value(rows[-1], "id"))
                batch_updated = 0
                batch_duplicates = 0
                classifications = []
                unclassified_ids: list[int] = []
                exact_conflicts: list[tuple[int, int]] = []
                for row in rows:
                    scanned += 1
                    discovery_country = str(
                        _value(row, "discovery_country_code", "unknown")
                    ).upper()
                    counters["discovery_country"][discovery_country] += 1
                    classification = _classify(row, publishers)
                    conflict_id = (
                        _claim_publisher_external_id(
                            session,
                            row,
                            classification,
                            publisher_external_claims,
                        )
                        if classification is not None
                        else None
                    )
                    if classification is None or conflict_id is not None:
                        unclassified += 1
                        counters["status"]["legacy_unverified"] += 1
                        counters["domain"]["(unknown)"] += 1
                        if apply:
                            article_id = int(_value(row, "id"))
                            unclassified_ids.append(article_id)
                            if conflict_id is not None:
                                exact_conflicts.append((article_id, conflict_id))
                        continue

                    classifiable += 1
                    classifications.append(int(_value(row, "id")))
                    counters["publisher_country"][classification.country_code] += 1
                    counters["status"][classification.status] += 1
                    counters["domain"][classification.publisher_domain] += 1
                    matrix[discovery_country][classification.country_code] += 1
                    if apply:
                        batch_updated += _rowcount(session.execute(
                            text(CLASSIFIED_UPDATE_SQL),
                            {
                                "article_id": int(_value(row, "id")),
                                "publisher_source_id": (
                                    classification.publisher_source_id
                                ),
                                "publisher_name": classification.publisher_name,
                                "publisher_domain": classification.publisher_domain,
                                "country_code": classification.country_code,
                                "status": classification.status,
                            },
                        ))
                if apply:
                    if unclassified_ids:
                        batch_updated += _rowcount(session.execute(
                            text(UNCLASSIFIED_UPDATE_SQL),
                            {"article_ids": unclassified_ids},
                        ))
                    batch_duplicates += _reconcile_duplicates(
                        session,
                        classifications,
                    )
                    for article_id, conflict_id in exact_conflicts:
                        batch_duplicates += _reconcile_exact_conflict(
                            session,
                            article_id,
                            conflict_id,
                        )
                done = len(rows) < batch_size

        if not rows:
            if apply:
                state.update({
                    "last_id": batch_last_id,
                    "done": True,
                    "audit": _audit_payload(
                        scanned=scanned,
                        classifiable=classifiable,
                        unclassified=unclassified,
                        updated=updated,
                        duplicates_updated=duplicates_updated,
                        batches=batches,
                        counters=counters,
                        matrix=matrix,
                    ),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
                store.save(state)
            break

        batches += 1
        last_id = batch_last_id
        updated += batch_updated
        duplicates_updated += batch_duplicates
        if apply:
            state.update({
                "last_id": last_id,
                "done": done,
                "audit": _audit_payload(
                    scanned=scanned,
                    classifiable=classifiable,
                    unclassified=unclassified,
                    updated=updated,
                    duplicates_updated=duplicates_updated,
                    batches=batches,
                    counters=counters,
                    matrix=matrix,
                ),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })
            store.save(state)

    with get_session() as session:
        after = _snapshot_invariants(
            session,
            max_article_id=int(before["max_article_id"]),
        )
    checks = _invariant_checks(before, after)
    invariants = {
        "before": before,
        "after": after,
        "checks": checks,
        "passed": all(checks.values()),
    }
    if not invariants["passed"]:
        raise RuntimeError("backfill changed protected row counts or provenance")

    return BackfillReport(
        mode="apply" if apply else "dry-run",
        scanned=scanned,
        classifiable=classifiable,
        unclassified=unclassified,
        updated=updated,
        duplicates_updated=duplicates_updated,
        batches=batches,
        last_id=last_id,
        done=done,
        counts=_sorted_counts(counters),
        matrix=_sorted_matrix(matrix),
        invariants=invariants,
    )


def write_report(path: Path, report: BackfillReport) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        asdict(report),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        default=_json_default,
    )
    path.write_text(payload + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill verified Google News publishers conservatively"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit bounded batches; without this flag the command is read-only",
    )
    parser.add_argument("--since-days", type=int, default=DEFAULT_SINCE_DAYS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    parser.add_argument("--report", type=Path)
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    args = build_parser().parse_args()
    wait_for_db()
    report = run_backfill(
        since_days=args.since_days,
        batch_size=args.batch_size,
        apply=args.apply,
        checkpoint=args.checkpoint,
    )
    if args.report:
        write_report(args.report, report)
    print(json.dumps(asdict(report), ensure_ascii=False, sort_keys=True, default=_json_default))


if __name__ == "__main__":
    main()
