"""Provider-neutral embedding profiles, jobs, and persistence adapters.

This module prepares background indexing but does not enable vector ranking.
Public GET routes can inspect profile availability without constructing or
calling an embedding provider.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Protocol, runtime_checkable

from sqlalchemy import text


EMBEDDABLE_OBJECT_TYPES = frozenset({"article", "entity", "event", "story"})
# Stable transaction-lock namespace for the singleton active-profile switch.
ACTIVE_PROFILE_LOCK_KEY = 4_383_600_651_739_901_817


@dataclass(frozen=True)
class EmbeddingProfile:
    profile_key: str
    provider: str
    model: str
    dimensions: int
    task: str
    version: str
    active: bool = False
    id: int | None = None

    def __post_init__(self) -> None:
        if self.dimensions <= 0:
            raise ValueError("embedding dimensions must be positive")


@runtime_checkable
class EmbeddingProvider(Protocol):
    def profile(self) -> EmbeddingProfile:
        raise NotImplementedError

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError


class EmbeddingProviderError(RuntimeError):
    """Raised when the configured provider cannot produce a complete result."""


class LegacyEmbeddingProvider:
    """Wrap the existing Jina/OpenAI helper behind the neutral protocol."""

    def __init__(
        self,
        profile: EmbeddingProfile,
        *,
        document_embedder: Callable[[list[str]], list[Any]] | None = None,
        query_embedder: Callable[[str], Any] | None = None,
    ):
        if document_embedder is None or query_embedder is None:
            from src.embeddings import generate_embedding, generate_embeddings_batch

            document_embedder = document_embedder or generate_embeddings_batch
            query_embedder = query_embedder or generate_embedding
        self._profile = profile
        self._document_embedder = document_embedder
        self._query_embedder = query_embedder

    @classmethod
    def from_environment(cls) -> LegacyEmbeddingProvider | None:
        """Build an adapter for the already-configured legacy backend."""
        from src.embeddings import _get_api_config, _provider_name

        url, _headers, model, dimensions = _get_api_config()
        if not url or not model or not dimensions:
            return None
        provider = _provider_name(url, model)
        profile = EmbeddingProfile(
            profile_key=f"{provider}:{model}:{dimensions}:text-matching:v1",
            provider=provider,
            model=model,
            dimensions=dimensions,
            task="text-matching",
            version="v1",
            active=True,
        )
        return cls(profile)

    def profile(self) -> EmbeddingProfile:
        return self._profile

    def _validate_vector(self, vector: Any) -> list[float]:
        if vector is None:
            raise EmbeddingProviderError("provider returned no embedding")
        try:
            return validate_embedding_vector(self._profile, vector)
        except ValueError as exc:
            raise EmbeddingProviderError(str(exc)) from exc

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._document_embedder(texts)
        if len(vectors) != len(texts):
            raise EmbeddingProviderError("provider returned an incomplete document batch")
        return [self._validate_vector(vector) for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self._validate_vector(self._query_embedder(text))


def content_hash(content: str | bytes) -> str:
    """Return the lowercase SHA-256 digest used by embedding records and jobs."""
    payload = content.encode("utf-8") if isinstance(content, str) else content
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class EmbeddingJobKey:
    profile_id: int
    object_type: str
    object_id: str
    content_hash: str

    def __post_init__(self) -> None:
        if self.object_type not in EMBEDDABLE_OBJECT_TYPES:
            raise ValueError(f"unsupported embedding object type: {self.object_type}")
        if re.fullmatch(r"[0-9a-f]{64}", self.content_hash) is None:
            raise ValueError("content hash must be a SHA-256 hex digest")

    @property
    def idempotency_key(self) -> str:
        return ":".join(
            (
                str(self.profile_id),
                self.object_type,
                self.object_id,
                self.content_hash,
            )
        )


def embedding_job_key(
    *,
    profile_id: int,
    object_type: str,
    object_id: str,
    content: str | bytes | None = None,
    digest: str | None = None,
) -> EmbeddingJobKey:
    """Build the same unique job key for the same profile, object, and content."""
    if (content is None) == (digest is None):
        raise ValueError("provide exactly one of content or digest")
    return EmbeddingJobKey(
        profile_id=profile_id,
        object_type=object_type,
        object_id=str(object_id),
        content_hash=digest if digest is not None else content_hash(content or b""),
    )


def semantic_search_response(profile: EmbeddingProfile | None) -> dict[str, str]:
    """Describe capability without generating an embedding or activating ranking."""
    if profile is None or not profile.active:
        return {"semantic_search": "unavailable"}
    return {
        "semantic_search": "available",
        "embedding_profile": profile.profile_key,
    }


def validate_embedding_vector(
    profile: EmbeddingProfile,
    embedding: list[float],
) -> list[float]:
    """Validate dimensionless pgvector data against its explicit profile."""
    try:
        vector = [float(value) for value in embedding]
    except (TypeError, ValueError) as exc:
        raise ValueError("embedding vector must contain only numbers") from exc
    if len(vector) != profile.dimensions:
        raise ValueError(
            "embedding vector dimension mismatch: "
            f"expected {profile.dimensions}, got {len(vector)}"
        )
    if not all(math.isfinite(value) for value in vector):
        raise ValueError("embedding vector must contain only finite numbers")
    return vector


@dataclass(frozen=True)
class EmbeddingJob:
    id: int
    profile_id: int
    object_type: str
    object_id: str
    content_hash: str
    attempts: int


def _row_mapping(row: Any) -> Any:
    return row._mapping if hasattr(row, "_mapping") else row


def _profile_from_row(raw: Any) -> EmbeddingProfile:
    row = _row_mapping(raw)
    return EmbeddingProfile(
        id=row["id"],
        profile_key=row["profile_key"],
        provider=row["provider"],
        model=row["model"],
        dimensions=row["dimensions"],
        task=row["task"],
        version=row["version"],
        active=row["active"],
    )


def _job_from_row(raw: Any) -> EmbeddingJob:
    row = _row_mapping(raw)
    return EmbeddingJob(
        id=int(row["id"]),
        profile_id=int(row["profile_id"]),
        object_type=row["object_type"],
        object_id=str(row["object_id"]),
        content_hash=row["content_hash"],
        attempts=int(row["attempts"]),
    )


class EmbeddingStore:
    """Small SQL adapter for active profiles and idempotent background jobs."""

    def active_profile(self, session: Any) -> EmbeddingProfile | None:
        rows = session.execute(
            text(
                """
                SELECT id, profile_key, provider, model, dimensions, task,
                       version, active
                FROM embedding_profiles
                WHERE active = TRUE
                ORDER BY id
                LIMIT 2
                """
            )
        ).fetchall()
        if len(rows) > 1:
            raise RuntimeError("multiple active embedding profiles are configured")
        return _profile_from_row(rows[0]) if rows else None

    def ensure_active_profile(
        self,
        session: Any,
        profile: EmbeddingProfile,
    ) -> EmbeddingProfile:
        """Create or reuse the configured profile and make it the only active one."""
        session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": ACTIVE_PROFILE_LOCK_KEY},
        )
        raw = session.execute(
            text(
                """
                WITH deactivated AS (
                    UPDATE embedding_profiles
                    SET active = FALSE
                    WHERE active = TRUE
                      AND profile_key <> :profile_key
                    RETURNING id
                ), configured AS (
                    INSERT INTO embedding_profiles
                        (profile_key, provider, model, dimensions, task,
                         version, active)
                    VALUES
                        (:profile_key, :provider, :model, :dimensions, :task,
                         :version, TRUE)
                    ON CONFLICT (profile_key)
                    DO UPDATE SET
                        provider = EXCLUDED.provider,
                        model = EXCLUDED.model,
                        dimensions = EXCLUDED.dimensions,
                        task = EXCLUDED.task,
                        version = EXCLUDED.version,
                        active = TRUE
                    RETURNING id, profile_key, provider, model, dimensions,
                              task, version, active
                )
                SELECT id, profile_key, provider, model, dimensions, task,
                       version, active
                FROM configured
                """
            ),
            {
                "profile_key": profile.profile_key,
                "provider": profile.provider,
                "model": profile.model,
                "dimensions": profile.dimensions,
                "task": profile.task,
                "version": profile.version,
            },
        ).fetchone()
        if raw is None:
            raise RuntimeError("could not create or activate embedding profile")
        return _profile_from_row(raw)

    def count_pending_jobs(self, session: Any, *, profile_id: int) -> int:
        """Count jobs currently eligible for a dry-run of the active profile."""
        return int(
            session.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM embedding_jobs ej
                    JOIN embedding_profiles ep ON ep.id = ej.profile_id
                    WHERE ej.profile_id = :profile_id
                      AND ej.status = 'pending'
                      AND ej.available_at <= now()
                      AND ep.active = TRUE
                    """
                ),
                {"profile_id": profile_id},
            ).scalar_one()
        )

    def recover_stale_jobs(
        self,
        session: Any,
        *,
        profile_id: int,
        lease_seconds: int,
    ) -> int:
        """Requeue expired processing leases before the next pending-only claim."""
        if lease_seconds < 1:
            raise ValueError("lease seconds must be positive")
        return int(
            session.execute(
                text(
                    """
                    WITH stale AS (
                        SELECT ej.id
                        FROM embedding_jobs ej
                        JOIN embedding_profiles ep ON ep.id = ej.profile_id
                        WHERE ej.profile_id = :profile_id
                          AND ej.status = 'processing'
                          AND ep.active = TRUE
                          AND ej.updated_at < now() - make_interval(secs => :lease_seconds)
                        FOR UPDATE OF ej SKIP LOCKED
                    ), recovered AS (
                        UPDATE embedding_jobs ej
                        SET status = 'pending',
                            last_error = 'processing lease expired; requeued',
                            available_at = now(),
                            updated_at = now()
                        FROM stale
                        WHERE ej.id = stale.id
                        RETURNING ej.id
                    )
                    SELECT COUNT(*) FROM recovered
                    """
                ),
                {"profile_id": profile_id, "lease_seconds": lease_seconds},
            ).scalar_one()
        )

    def enqueue_job(self, session: Any, key: EmbeddingJobKey) -> int:
        """Insert or return one job for the schema's unique idempotency tuple."""
        return int(
            session.execute(
                text(
                    """
                    INSERT INTO embedding_jobs
                        (profile_id, object_type, object_id, content_hash)
                    VALUES
                        (:profile_id, :object_type, :object_id, :content_hash)
                    ON CONFLICT (profile_id, object_type, object_id, content_hash)
                    DO UPDATE SET object_id = EXCLUDED.object_id
                    RETURNING id
                    """
                ),
                {
                    "profile_id": key.profile_id,
                    "object_type": key.object_type,
                    "object_id": key.object_id,
                    "content_hash": key.content_hash,
                },
            ).scalar_one()
        )

    def claim_jobs(
        self,
        session: Any,
        *,
        profile_id: int,
        batch_size: int = 50,
        dry_run: bool = False,
    ) -> list[EmbeddingJob]:
        """Atomically claim pending jobs, scoped to one active profile."""
        if batch_size < 1:
            raise ValueError("batch size must be positive")
        if dry_run:
            statement = text(
                """
                SELECT ej.id, ej.profile_id, ej.object_type, ej.object_id,
                       ej.content_hash, ej.attempts
                FROM embedding_jobs ej
                JOIN embedding_profiles ep ON ep.id = ej.profile_id
                WHERE ej.profile_id = :profile_id
                  AND ej.status = 'pending'
                  AND ej.available_at <= now()
                  AND ep.active = TRUE
                ORDER BY ej.available_at, ej.id
                LIMIT :batch_size
                """
            )
        else:
            statement = text(
                """
                WITH candidates AS (
                    SELECT ej.id
                    FROM embedding_jobs ej
                    JOIN embedding_profiles ep ON ep.id = ej.profile_id
                    WHERE ej.profile_id = :profile_id
                      AND ej.status = 'pending'
                      AND ej.available_at <= now()
                      AND ep.active = TRUE
                    ORDER BY ej.available_at, ej.id
                    FOR UPDATE OF ej SKIP LOCKED
                    LIMIT :batch_size
                )
                UPDATE embedding_jobs ej
                SET status = 'processing',
                    attempts = ej.attempts + 1,
                    last_error = NULL,
                    updated_at = now()
                FROM candidates
                WHERE ej.id = candidates.id
                RETURNING ej.id, ej.profile_id, ej.object_type, ej.object_id,
                          ej.content_hash, ej.attempts
                """
            )
        rows = session.execute(
            statement,
            {"profile_id": profile_id, "batch_size": batch_size},
        ).fetchall()
        return [_job_from_row(row) for row in rows]

    def record_success(
        self,
        session: Any,
        *,
        job: EmbeddingJob,
        profile: EmbeddingProfile,
        embedding: list[float],
    ) -> bool:
        if profile.id is None or job.profile_id != profile.id:
            raise ValueError("embedding vector profile does not match the claimed job")
        values = validate_embedding_vector(profile, embedding)
        vector = "[" + ",".join(str(value) for value in values) + "]"
        return bool(
            session.execute(
                text(
                    """
                    WITH owned AS (
                        UPDATE embedding_jobs
                        SET status = 'completed',
                            last_error = NULL,
                            updated_at = now()
                        WHERE id = :job_id
                          AND status = 'processing'
                          AND attempts = :generation
                        RETURNING profile_id, object_type, object_id, content_hash
                    ), stored AS (
                        INSERT INTO content_embeddings
                            (profile_id, object_type, object_id, content_hash,
                             embedding, status, error, updated_at)
                        SELECT profile_id, object_type, object_id, content_hash,
                               CAST(:embedding AS vector), 'ready', NULL, now()
                        FROM owned
                        ON CONFLICT (profile_id, object_type, object_id, content_hash)
                        DO UPDATE SET
                            embedding = EXCLUDED.embedding,
                            status = 'ready',
                            error = NULL,
                            updated_at = now()
                        RETURNING profile_id, object_type, object_id, embedding
                    ), projected AS (
                        UPDATE public.analysis AS legacy_analysis
                        SET embedding = stored.embedding
                        FROM stored
                        JOIN embedding_profiles ep
                          ON ep.id = stored.profile_id
                         AND ep.active = TRUE
                        JOIN pg_catalog.pg_attribute legacy_embedding
                          ON legacy_embedding.attrelid = 'public.analysis'::regclass
                         AND legacy_embedding.attname = 'embedding'
                         AND legacy_embedding.atttypmod = ep.dimensions
                         AND legacy_embedding.attisdropped = FALSE
                        WHERE stored.object_type = 'article'
                          AND legacy_analysis.article_id::text = stored.object_id
                        RETURNING legacy_analysis.article_id
                    )
                    SELECT EXISTS(SELECT 1 FROM stored),
                           (SELECT COUNT(*) FROM projected) AS projected_count
                    """
                ),
                {
                    "job_id": job.id,
                    "generation": job.attempts,
                    "embedding": vector,
                },
            ).scalar_one()
        )

    def record_failure(
        self,
        session: Any,
        *,
        job: EmbeddingJob,
        error: str,
        retry: bool,
    ) -> bool:
        return bool(
            session.execute(
                text(
                    """
                    WITH transitioned AS (
                        UPDATE embedding_jobs
                        SET status = :status,
                            last_error = :error,
                            available_at = CASE
                                WHEN :retry THEN now() + make_interval(
                                    secs => LEAST(3600, attempts * 60)
                                )
                                ELSE available_at
                            END,
                            updated_at = now()
                        WHERE id = :job_id
                          AND status = 'processing'
                          AND attempts = :generation
                        RETURNING 1
                    )
                    SELECT EXISTS(SELECT 1 FROM transitioned)
                    """
                ),
                {
                    "job_id": job.id,
                    "generation": job.attempts,
                    "status": "pending" if retry else "failed",
                    "error": (error or "unknown embedding error")[:2000],
                    "retry": retry,
                },
            ).scalar_one()
        )
