"""Opt-in full-chain PostgreSQL regression test.

Set GEO_PULSE_TEST_DATABASE_URL to a disposable database and explicitly set
GEO_PULSE_TEST_DATABASE_RESET=1. The test drops and recreates its public schema.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.api.routes.signal_detail import SqlSignalDetailService
from src.engine.signals import SignalEvidence, _emit


ROOT = Path(__file__).resolve().parents[1]


def _execute_file(cursor, path: Path) -> None:
    cursor.execute(path.read_text())


def _apply_pending(cursor) -> list[str]:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            filename TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT now()
        )
    """)
    applied = []
    for path in sorted((ROOT / "scripts" / "migrations").glob("*.sql")):
        cursor.execute(
            "SELECT 1 FROM schema_migrations WHERE filename=%s",
            (path.name,),
        )
        if cursor.fetchone():
            continue
        _execute_file(cursor, path)
        cursor.execute(
            "INSERT INTO schema_migrations(filename) VALUES (%s)",
            (path.name,),
        )
        applied.append(path.name)
    return applied


def test_full_chain_bootstraps_and_second_run_is_a_noop():
    dsn = os.getenv("GEO_PULSE_TEST_DATABASE_URL")
    reset = os.getenv("GEO_PULSE_TEST_DATABASE_RESET")
    if not dsn or reset != "1":
        pytest.skip("requires an explicitly disposable PostgreSQL database")

    psycopg2 = pytest.importorskip("psycopg2")
    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("DROP SCHEMA public CASCADE")
            cursor.execute("CREATE SCHEMA public")
            cursor.execute("""
                CREATE FUNCTION create_hypertable(regclass, name)
                RETURNS text LANGUAGE sql
                AS $$ SELECT 'test-shim-no-timescaledb'::text $$
            """)
            _execute_file(cursor, ROOT / "data" / "init.sql")

            first_run = _apply_pending(cursor)
            second_run = _apply_pending(cursor)

            assert "002_threads.sql" in first_run
            assert "021_signal_evidence_explanations.sql" in first_run
            assert second_run == []

            cursor.execute("""
                SELECT to_regclass('public.threads'),
                       to_regclass('public.thread_articles')
            """)
            assert cursor.fetchone() == ("threads", "thread_articles")

            cursor.execute("""
                SELECT indexname, indexdef FROM pg_indexes
                WHERE schemaname='public'
                  AND indexname IN (
                    'idx_articles_search_snapshot',
                    'idx_embedding_jobs_pending',
                    'idx_embedding_jobs_processing_lease'
                  )
                ORDER BY indexname
            """)
            index_rows = cursor.fetchall()
            assert [row[0] for row in index_rows] == [
                "idx_articles_search_snapshot",
                "idx_embedding_jobs_pending",
                "idx_embedding_jobs_processing_lease",
            ]
            index_definitions = {name: definition for name, definition in index_rows}
            assert "COALESCE(collected_at, published_at) DESC, id DESC" in (
                index_definitions["idx_articles_search_snapshot"]
            )
            assert "(profile_id, available_at, id)" in (
                index_definitions["idx_embedding_jobs_pending"]
            )
            assert "(profile_id, updated_at, id)" in (
                index_definitions["idx_embedding_jobs_processing_lease"]
            )

            cursor.execute("""
                SELECT conname
                FROM pg_constraint
                WHERE conrelid='thread_articles'::regclass
                  AND contype='f'
                ORDER BY conname
            """)
            assert [row[0] for row in cursor.fetchall()] == [
                "thread_articles_article_fk",
                "thread_articles_thread_fk",
            ]

            # Old data/002_threads.sql created this FK without an explicit
            # name, while migration 006 later added the canonical constraint.
            cursor.execute("""
                ALTER TABLE thread_articles
                ADD CONSTRAINT thread_articles_thread_id_fkey
                FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE
            """)
            _execute_file(cursor, ROOT / "scripts" / "migrations" / "022_postgres_hardening.sql")
            cursor.execute("""
                SELECT conname
                FROM pg_constraint
                WHERE conrelid='thread_articles'::regclass
                  AND contype='f'
                ORDER BY conname
            """)
            assert [row[0] for row in cursor.fetchall()] == [
                "thread_articles_article_fk",
                "thread_articles_thread_fk",
            ]

            cursor.execute("SELECT COUNT(*) FROM schema_migrations")
            tracked_count = cursor.fetchone()[0]
            expected_count = len(list(
                (ROOT / "scripts" / "migrations").glob("*.sql")
            ))
            assert tracked_count == expected_count

        app_engine = create_engine(dsn)
        Session = sessionmaker(bind=app_engine, expire_on_commit=False)
        at = datetime.now(timezone.utc).replace(microsecond=0)
        with Session.begin() as session:
            session.execute(text("""
                INSERT INTO sources(
                    id,name,url,country_code,source_type,weight,language,tier
                ) VALUES (
                    1,'Smoke','https://example.test','ES','rss',1,'es','mainstream'
                )
            """))
            session.execute(text("""
                INSERT INTO articles(
                    id,source_id,external_id,title,published_at,collected_at,
                    language,title_normalized,is_duplicate,is_backfill
                ) VALUES (
                    1,1,'smoke','Story evidence',:at,:at,'es',
                    'story evidence',FALSE,FALSE
                )
            """), {"at": at})
            session.execute(text("""
                INSERT INTO stories(
                    id,slug,title_ru,lifecycle,first_seen,last_seen,
                    article_count,source_count,country_count,
                    highest_action_level,clustering_confidence,meta
                ) VALUES
                  (10,'canonical','Canonical','developing',:at,:at,1,1,2,3,0.9,'{}'),
                  (11,'superseded','Superseded','resolved',:at,:at,1,1,1,3,0.7,
                   CAST(:superseded_meta AS jsonb))
            """), {
                "at": at,
                "superseded_meta": '{"merged_into_story_id":10}',
            })
            session.execute(text("""
                INSERT INTO story_articles(
                    story_id,article_id,membership_confidence,evidence
                ) VALUES (10,1,0.9,'{}'),(11,1,0.7,'{}')
            """))
            emitted = _emit(
                session,
                "tier_convergence",
                "ES",
                "tier_convergence:ES:postgres-hardening",
                "Canonical evidence",
                "Canonical evidence",
                {},
                evidence=SignalEvidence(
                    detector="tier_convergence",
                    detector_version="1.0",
                    threshold={"minimum_distinct_tiers": 3},
                    observed={"distinct_tiers": 3},
                    baseline={},
                    window_start=at,
                    window_end=at,
                    article_ids=(1,),
                    story_ids=(11,),
                    confidence=0.9,
                    completeness="complete",
                    explanation={"rule": "rule"},
                ),
            )
            assert emitted is True

        with Session() as session:
            persisted_story_ids = session.execute(text("""
                SELECT se.story_ids
                FROM signal_evidence se
                JOIN signals s ON s.id=se.signal_id
                WHERE s.dedup_key='tier_convergence:ES:postgres-hardening'
            """)).scalar_one()
            detail_story = SqlSignalDetailService._load_story(
                session,
                story_ids=(11,),
                article_ids=(1,),
            )

        assert persisted_story_ids == [10]
        assert detail_story.id == 10
        app_engine.dispose()
    finally:
        connection.close()
