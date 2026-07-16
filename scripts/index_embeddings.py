#!/usr/bin/env python3
"""One-off worker for pending embedding jobs of the active profile.

This worker is intentionally not scheduled by Compose.  It only uses the
document side of the embedding protocol; query embeddings remain a search-time
adapter concern for a later release.

Usage:
    python -m scripts.index_embeddings [--batch 50] [--limit 0] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import logging
from typing import Any, Callable

from sqlalchemy import text

from src.db import get_session, wait_for_db
from src.embedding_store import (
    EmbeddingJob,
    EmbeddingProfile,
    EmbeddingProvider,
    EmbeddingStore,
    LegacyEmbeddingProvider,
    content_hash,
    validate_embedding_vector,
)
from src.embeddings import prepare_embedding_text


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("index-embeddings")


def _mapping(row: Any) -> Any:
    return row._mapping if hasattr(row, "_mapping") else row


def load_job_content(session: Any, job: EmbeddingJob) -> str | None:
    """Load the current deterministic text representation for a claimed job."""
    if job.object_type == "article":
        raw = session.execute(
            text("SELECT title, body, summary FROM articles WHERE id = :id"),
            {"id": int(job.object_id)},
        ).fetchone()
        if raw is None:
            return None
        row = _mapping(raw)
        return prepare_embedding_text(
            row["title"] or "",
            row["body"] or "",
            row["summary"] or "",
        )
    if job.object_type in {"entity", "event"}:
        raw = session.execute(
            text(
                """
                SELECT canonical_name, labels
                FROM canonical_entities
                WHERE id = :id
                  AND (:object_type <> 'event' OR kind = 'event')
                """
            ),
            {"id": job.object_id, "object_type": job.object_type},
        ).fetchone()
        if raw is None:
            return None
        row = _mapping(raw)
        labels = json.dumps(row["labels"] or {}, ensure_ascii=False, sort_keys=True)
        return f"{row['canonical_name']}\n{labels}"
    if job.object_type == "story":
        raw = session.execute(
            text("SELECT title_ru, title_en, summary FROM stories WHERE id = :id"),
            {"id": int(job.object_id)},
        ).fetchone()
        if raw is None:
            return None
        row = _mapping(raw)
        return "\n".join(
            part for part in (row["title_ru"], row["title_en"], row["summary"]) if part
        )
    return None


def provider_for_active_profile(profile: EmbeddingProfile) -> EmbeddingProvider:
    """Return the legacy adapter only when environment metadata matches DB."""
    provider = LegacyEmbeddingProvider.from_environment()
    if provider is None:
        raise RuntimeError("no embedding provider is configured")
    configured = provider.profile()
    expected = (
        profile.provider,
        profile.model,
        profile.dimensions,
        profile.task,
        profile.version,
    )
    actual = (
        configured.provider,
        configured.model,
        configured.dimensions,
        configured.task,
        configured.version,
    )
    if actual != expected:
        raise RuntimeError(
            "active embedding profile does not match the configured provider: "
            f"expected {expected}, got {actual}"
        )
    return provider


def index_pending(
    *,
    batch_size: int = 50,
    limit: int = 0,
    max_attempts: int = 3,
    claim_lease_seconds: int = 900,
    dry_run: bool = False,
    store: EmbeddingStore | None = None,
    session_factory: Callable[[], Any] = get_session,
    provider_factory: Callable[[EmbeddingProfile], EmbeddingProvider] = provider_for_active_profile,
    content_loader: Callable[[Any, EmbeddingJob], str | None] = load_job_content,
) -> dict[str, int | str | bool]:
    """Claim and index pending jobs without holding a DB lock over the API call."""
    if batch_size < 1:
        raise ValueError("batch size must be positive")
    if limit < 0:
        raise ValueError("limit cannot be negative")
    if max_attempts < 1:
        raise ValueError("max attempts must be positive")
    if claim_lease_seconds < 1:
        raise ValueError("claim lease seconds must be positive")
    store = store or EmbeddingStore()

    with session_factory() as session:
        profile = store.active_profile(session)
    if profile is None or profile.id is None:
        return {
            "processed": 0,
            "indexed": 0,
            "failed": 0,
            "semantic_search": "unavailable",
        }

    if dry_run:
        with session_factory() as session:
            eligible = store.count_pending_jobs(session, profile_id=profile.id)
        processed = min(eligible, limit) if limit else eligible
        return {
            "processed": processed,
            "indexed": 0,
            "failed": 0,
            "dry_run": True,
        }

    provider = provider_factory(profile)
    provided_profile = provider.profile()
    if (
        provided_profile.provider != profile.provider
        or provided_profile.model != profile.model
        or provided_profile.dimensions != profile.dimensions
        or provided_profile.task != profile.task
        or provided_profile.version != profile.version
    ):
        raise RuntimeError("embedding provider metadata does not match the active profile")

    with session_factory() as session:
        recovered = store.recover_stale_jobs(
            session,
            profile_id=profile.id,
            lease_seconds=claim_lease_seconds,
        )
    if recovered:
        logger.warning("Requeued %d expired embedding job leases", recovered)

    attempted = 0
    processed = 0
    indexed = 0
    failed = 0
    while not limit or attempted < limit:
        fetch_size = batch_size
        if limit:
            fetch_size = min(batch_size, limit - attempted)
        with session_factory() as session:
            jobs = store.claim_jobs(
                session,
                profile_id=profile.id,
                batch_size=fetch_size,
            )
            contents = [(job, content_loader(session, job)) for job in jobs]
        if not jobs:
            break
        attempted += len(jobs)

        valid_jobs: list[EmbeddingJob] = []
        valid_texts: list[str] = []
        invalid: list[tuple[EmbeddingJob, str]] = []
        for job, content in contents:
            if content is None or not content.strip():
                invalid.append((job, "embedding source content is unavailable"))
            elif content_hash(content) != job.content_hash:
                invalid.append((job, "embedding source content hash changed"))
            else:
                valid_jobs.append(job)
                valid_texts.append(content)

        vectors: list[list[float]] = []
        provider_error: str | None = None
        if valid_jobs:
            try:
                vectors = provider.embed_documents(valid_texts)
                if len(vectors) != len(valid_jobs):
                    raise RuntimeError("provider returned an incomplete embedding batch")
            except Exception as exc:
                provider_error = str(exc)

        successful: list[tuple[EmbeddingJob, list[float]]] = []
        if provider_error is None:
            for job, vector in zip(valid_jobs, vectors):
                try:
                    successful.append((job, validate_embedding_vector(profile, vector)))
                except ValueError as exc:
                    invalid.append((job, str(exc)))

        with session_factory() as session:
            for job, error in invalid:
                if store.record_failure(
                    session,
                    job=job,
                    error=error,
                    retry=False,
                ):
                    failed += 1
                    processed += 1
            if provider_error is not None:
                for job in valid_jobs:
                    if store.record_failure(
                        session,
                        job=job,
                        error=provider_error,
                        retry=job.attempts < max_attempts,
                    ):
                        failed += 1
                        processed += 1
            else:
                for job, vector in successful:
                    # Persistence also performs the guarded legacy article
                    # projection in this transaction when the vector is 1536-D.
                    if store.record_success(
                        session,
                        job=job,
                        profile=profile,
                        embedding=vector,
                    ):
                        indexed += 1
                        processed += 1
        logger.info(
            "Embedding batch: %d indexed, %d failed "
            "(processed=%d, attempted=%d)",
            indexed,
            failed,
            processed,
            attempted,
        )

    return {"processed": processed, "indexed": indexed, "failed": failed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Index pending embedding jobs")
    parser.add_argument("--batch", type=int, default=50, help="Jobs per provider call")
    parser.add_argument("--limit", type=int, default=0, help="Maximum jobs (0=all)")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument(
        "--claim-lease",
        type=int,
        default=900,
        help="Seconds before a processing claim is requeued",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    wait_for_db()
    result = index_pending(
        batch_size=args.batch,
        limit=args.limit,
        max_attempts=args.max_attempts,
        claim_lease_seconds=args.claim_lease,
        dry_run=args.dry_run,
    )
    logger.info("Indexing complete: %s", result)


if __name__ == "__main__":
    main()
