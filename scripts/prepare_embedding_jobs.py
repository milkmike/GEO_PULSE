#!/usr/bin/env python3
"""Prepare idempotent article embedding jobs for the configured profile."""

from __future__ import annotations

import argparse
import json
from typing import Any, Callable

from sqlalchemy import text

from src.db import get_session, wait_for_db
from src.embedding_store import (
    EmbeddingProfile,
    EmbeddingStore,
    LegacyEmbeddingProvider,
    embedding_job_key,
)
from src.embeddings import prepare_embedding_text


def _mapping(row: Any) -> Any:
    return row._mapping if hasattr(row, "_mapping") else row


def configured_profile() -> EmbeddingProfile:
    """Return the profile described by the configured embedding provider."""
    provider = LegacyEmbeddingProvider.from_environment()
    if provider is None:
        raise RuntimeError("no embedding provider is configured")
    return provider.profile()


def load_eligible_articles(
    session: Any,
    *,
    days: int,
    limit: int,
) -> list[Any]:
    """Load recent relevant originals in a deterministic preparation order."""
    rows = session.execute(
        text(
            """
            SELECT a.id, a.title, a.body, a.summary
            FROM articles a
            JOIN analysis an ON an.article_id = a.id
            WHERE an.is_relevant = TRUE
              AND a.is_duplicate = FALSE
              AND a.published_at >= now() - make_interval(days => :days)
            ORDER BY a.published_at DESC, a.id DESC
            LIMIT :limit
            """
        ),
        {"days": days, "limit": limit},
    ).fetchall()
    return [_mapping(row) for row in rows]


def prepare_jobs(
    *,
    days: int = 30,
    limit: int = 500,
    dry_run: bool = False,
    store: EmbeddingStore | None = None,
    session_factory: Callable[[], Any] = get_session,
    profile_factory: Callable[[], EmbeddingProfile] = configured_profile,
    article_loader: Callable[..., list[Any]] = load_eligible_articles,
) -> dict[str, int | str | bool]:
    """Prepare one idempotent job per profile, article, and content hash."""
    if days < 1:
        raise ValueError("days must be positive")
    if limit < 1:
        raise ValueError("limit must be positive")

    store = store or EmbeddingStore()
    configured = profile_factory()
    prepared: list[tuple[str, str]] = []

    with session_factory() as session:
        if dry_run:
            active = store.active_profile(session)
            profile = (
                active
                if active is not None and active.profile_key == configured.profile_key
                else configured
            )
        else:
            profile = store.ensure_active_profile(session, configured)

        for raw in article_loader(session, days=days, limit=limit):
            row = _mapping(raw)
            content = prepare_embedding_text(
                row["title"] or "",
                row["body"] or "",
                row["summary"] or "",
            )
            if content.strip():
                prepared.append((str(row["id"]), content))

        if not dry_run:
            if profile.id is None:
                raise RuntimeError("active embedding profile has no database id")
            for object_id, content in prepared:
                store.enqueue_job(
                    session,
                    embedding_job_key(
                        profile_id=profile.id,
                        object_type="article",
                        object_id=object_id,
                        content=content,
                    ),
                )

    return {
        "eligible": len(prepared),
        "enqueued": 0 if dry_run else len(prepared),
        "profile": profile.profile_key,
        "dry_run": dry_run,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare article embedding jobs")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    wait_for_db()
    print(
        json.dumps(
            prepare_jobs(days=args.days, limit=args.limit, dry_run=args.dry_run),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
