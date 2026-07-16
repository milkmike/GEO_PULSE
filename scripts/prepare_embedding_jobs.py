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
    content_hash,
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


def load_story_candidate_articles(
    session: Any,
    *,
    days: int,
    limit: int,
    profile_id: int | None,
) -> list[Any]:
    """Load bounded scoped-story articles, favoring useful low-coverage threads.

    Candidate eligibility deliberately mirrors ``fetch_story_candidates``:
    membership must resolve back to the thread's canonical publisher country.
    The query only inspects embedding metadata; it never loads vector values.
    """
    rows = session.execute(
        text(
            """
            WITH candidate_articles AS MATERIALIZED (
                SELECT DISTINCT
                       a.id, a.title, a.body, a.summary, a.published_at,
                       t.id AS thread_id,
                       TRIM(t.country_code) AS country_code,
                       COALESCE(NULLIF(an.event_key, ''), t.thread_key)
                           AS event_key,
                       EXISTS (
                           SELECT 1
                           FROM content_embeddings ce
                           WHERE ce.profile_id = :profile_id
                             AND ce.object_type = 'article'
                             AND ce.object_id = a.id::text
                             AND ce.status = 'ready'
                             AND ce.embedding IS NOT NULL
                       ) AS has_active_ready
                FROM threads t
                JOIN thread_articles ta ON ta.thread_id = t.id
                JOIN articles a ON a.id = ta.article_id
                JOIN article_country_facts country_fact
                  ON country_fact.article_id = a.id
                 AND TRIM(country_fact.country_code) = TRIM(t.country_code)
                LEFT JOIN analysis an ON an.article_id = a.id
                WHERE t.article_count > 0
                  AND a.published_at >= now() - make_interval(days => :days)
            ), thread_coverage AS (
                SELECT thread_id,
                       COUNT(*) AS candidate_count,
                       COUNT(*) FILTER (WHERE has_active_ready) AS ready_count,
                       COUNT(*) FILTER (WHERE has_active_ready)::numeric
                           / NULLIF(COUNT(*), 0) AS coverage_ratio
                FROM candidate_articles
                GROUP BY thread_id
            ), thread_events AS (
                SELECT DISTINCT thread_id, country_code, event_key
                FROM candidate_articles
                WHERE event_key IS NOT NULL AND event_key <> ''
            ), thread_entities AS (
                SELECT DISTINCT candidate.thread_id, candidate.country_code,
                                mention.entity_id
                FROM candidate_articles candidate
                JOIN article_entity_mentions mention
                  ON mention.article_id = candidate.id
            ), cross_country_pairs AS (
                SELECT left_event.thread_id AS left_thread_id,
                       right_event.thread_id AS right_thread_id
                FROM thread_events left_event
                JOIN thread_events right_event
                  ON right_event.event_key = left_event.event_key
                 AND right_event.country_code <> left_event.country_code
                 AND right_event.thread_id <> left_event.thread_id
                UNION
                SELECT left_entity.thread_id AS left_thread_id,
                       right_entity.thread_id AS right_thread_id
                FROM thread_entities left_entity
                JOIN thread_entities right_entity
                  ON right_entity.entity_id = left_entity.entity_id
                 AND right_entity.country_code <> left_entity.country_code
                 AND right_entity.thread_id <> left_entity.thread_id
            ), peer_counts AS (
                SELECT left_thread_id AS thread_id,
                       COUNT(DISTINCT right_thread_id) AS cross_country_peer_count
                FROM cross_country_pairs
                GROUP BY left_thread_id
            ), ranked AS (
                SELECT candidate.*,
                       coverage.candidate_count,
                       coverage.ready_count,
                       coverage.coverage_ratio,
                       COALESCE(peers.cross_country_peer_count, 0)
                           AS cross_country_peer_count,
                       ROW_NUMBER() OVER (
                           PARTITION BY candidate.thread_id
                           ORDER BY candidate.has_active_ready ASC,
                                    candidate.published_at DESC,
                                    candidate.id DESC
                       ) AS article_rank
                FROM candidate_articles candidate
                JOIN thread_coverage coverage
                  ON coverage.thread_id = candidate.thread_id
                LEFT JOIN peer_counts peers
                  ON peers.thread_id = candidate.thread_id
            )
            SELECT ranked.id, ranked.title, ranked.body, ranked.summary,
                   ranked.thread_id, ranked.country_code,
                   ranked.cross_country_peer_count,
                   ranked.candidate_count, ranked.ready_count,
                   ARRAY(
                       SELECT DISTINCT ce.content_hash
                       FROM content_embeddings ce
                       WHERE ce.profile_id = :profile_id
                         AND ce.object_type = 'article'
                         AND ce.object_id = ranked.id::text
                         AND ce.status = 'ready'
                         AND ce.embedding IS NOT NULL
                       ORDER BY ce.content_hash
                   ) AS ready_content_hashes
            FROM ranked
            ORDER BY (ranked.cross_country_peer_count > 0) DESC,
                     ranked.coverage_ratio ASC,
                     ranked.cross_country_peer_count DESC,
                     ranked.article_rank ASC,
                     ranked.published_at DESC,
                     ranked.id DESC,
                     ranked.thread_id ASC
            LIMIT :limit
            """
        ),
        {"days": days, "limit": limit, "profile_id": profile_id},
    ).fetchall()
    return [_mapping(row) for row in rows]


def prepare_jobs(
    *,
    days: int = 30,
    limit: int = 500,
    dry_run: bool = False,
    story_candidates: bool = False,
    store: EmbeddingStore | None = None,
    session_factory: Callable[[], Any] = get_session,
    profile_factory: Callable[[], EmbeddingProfile] = configured_profile,
    article_loader: Callable[..., list[Any]] | None = None,
) -> dict[str, int | str | bool]:
    """Prepare one idempotent job per profile, article, and content hash."""
    if days < 1:
        raise ValueError("days must be positive")
    if limit < 1:
        raise ValueError("limit must be positive")

    store = store or EmbeddingStore()
    configured = profile_factory()
    prepared: list[tuple[str, str]] = []
    loaded_rows: list[Any] = []
    ready_current = 0

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

        loader = article_loader or (
            load_story_candidate_articles if story_candidates else load_eligible_articles
        )
        loader_options: dict[str, Any] = {"days": days, "limit": limit}
        if story_candidates:
            loader_options["profile_id"] = profile.id
        loaded_rows = list(loader(session, **loader_options))

        for raw in loaded_rows:
            row = _mapping(raw)
            content = prepare_embedding_text(
                row["title"] or "",
                row["body"] or "",
                row["summary"] or "",
            )
            if content.strip():
                if story_candidates and content_hash(content) in set(
                    row.get("ready_content_hashes") or []
                ):
                    ready_current += 1
                    continue
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

    result: dict[str, int | str | bool] = {
        "eligible": len(prepared),
        "enqueued": 0 if dry_run else len(prepared),
        "profile": profile.profile_key,
        "dry_run": dry_run,
    }
    if story_candidates:
        candidate_threads = {
            int(_mapping(row)["thread_id"])
            for row in loaded_rows
            if _mapping(row).get("thread_id") is not None
        }
        cross_country_threads = {
            int(_mapping(row)["thread_id"])
            for row in loaded_rows
            if int(_mapping(row).get("cross_country_peer_count") or 0) > 0
        }
        result.update(
            {
                "mode": "story_candidates",
                "candidate_articles": len(loaded_rows),
                "candidate_threads": len(candidate_threads),
                "cross_country_threads": len(cross_country_threads),
                "ready_current": ready_current,
                "missing_current": len(prepared),
            }
        )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare article embedding jobs")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--story-candidates",
        action="store_true",
        help="prioritize recent canonical-country story candidate articles",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    wait_for_db()
    print(
        json.dumps(
            prepare_jobs(
                days=args.days,
                limit=args.limit,
                dry_run=args.dry_run,
                story_candidates=args.story_candidates,
            ),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
