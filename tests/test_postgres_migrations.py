"""Opt-in PostgreSQL migration and canonical-story integration tests.

Set GEO_PULSE_TEST_DATABASE_URL to a disposable database and explicitly set
GEO_PULSE_TEST_DATABASE_RESET=1. These tests drop and recreate public schema.
The migration files are always applied through scripts/apply_migrations.sh and
psql, including CREATE/DROP INDEX CONCURRENTLY.
"""

import os
import shlex
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.api.routes.signal_detail import SqlSignalDetailService
from src.engine.signals import SignalEvidence, _emit


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "scripts" / "migrations"
RETRY_MIGRATIONS = ROOT / "tests" / "fixtures" / "migrations_retry"


def _requirements():
    dsn = os.getenv("GEO_PULSE_TEST_DATABASE_URL")
    reset = os.getenv("GEO_PULSE_TEST_DATABASE_RESET")
    if not dsn or reset != "1":
        pytest.skip("requires an explicitly disposable PostgreSQL database")
    psycopg2 = pytest.importorskip("psycopg2")
    if not os.getenv("GEO_PULSE_TEST_MIGRATION_RUNNER") and not shutil.which("psql"):
        pytest.skip("requires psql or GEO_PULSE_TEST_MIGRATION_RUNNER")
    return dsn, psycopg2


def _runner_migration_dir(path: Path) -> str:
    container_root = os.getenv("GEO_PULSE_TEST_CONTAINER_ROOT")
    if not container_root:
        return str(path)
    relative = path.resolve().relative_to(ROOT.resolve())
    return str(Path(container_root) / relative)


def _run_migrations(dsn: str, migration_dir: Path = MIGRATIONS):
    psycopg2 = pytest.importorskip("psycopg2")
    template = os.getenv("GEO_PULSE_TEST_MIGRATION_RUNNER")
    runner_dir = _runner_migration_dir(migration_dir)
    if template:
        command = shlex.split(template.format(mig_dir=runner_dir))
    else:
        command = ["bash", str(ROOT / "scripts" / "apply_migrations.sh")]

    parsed = psycopg2.extensions.parse_dsn(dsn)
    env = {**os.environ, "MIG_DIR": str(migration_dir)}
    for dsn_key, pg_key in (
        ("host", "PGHOST"),
        ("port", "PGPORT"),
        ("user", "PGUSER"),
        ("password", "PGPASSWORD"),
        ("dbname", "PGDATABASE"),
        ("sslmode", "PGSSLMODE"),
    ):
        if parsed.get(dsn_key):
            env[pg_key] = parsed[dsn_key]
    return subprocess.run(
        command,
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _assert_success(result) -> None:
    assert result.returncode == 0, result.stdout + result.stderr


def _reset(cursor, *, initialize: bool) -> None:
    cursor.execute("DROP SCHEMA IF EXISTS role_schema CASCADE")
    cursor.execute("DROP SCHEMA public CASCADE")
    cursor.execute("CREATE SCHEMA public")
    if initialize:
        cursor.execute("""
            CREATE FUNCTION create_hypertable(regclass, name)
            RETURNS text LANGUAGE sql
            AS $$ SELECT 'test-shim-no-timescaledb'::text $$
        """)
        cursor.execute((ROOT / "data" / "init.sql").read_text())


def _runner_search_path(cursor, *, reset: bool) -> None:
    from psycopg2 import sql

    cursor.execute("SELECT current_user, current_database()")
    role, database = cursor.fetchone()
    if reset:
        statement = sql.SQL("ALTER ROLE {} IN DATABASE {} RESET search_path")
    else:
        statement = sql.SQL(
            "ALTER ROLE {} IN DATABASE {} SET search_path = role_schema, public"
        )
    cursor.execute(statement.format(sql.Identifier(role), sql.Identifier(database)))


def _index_rows(cursor):
    cursor.execute("""
        SELECT cls.relname, pg_get_indexdef(cls.oid),
               idx.indisvalid, idx.indisready
        FROM pg_class cls
        JOIN pg_namespace ns ON ns.oid=cls.relnamespace
        JOIN pg_index idx ON idx.indexrelid=cls.oid
        WHERE ns.nspname='public'
          AND cls.relname IN (
            'idx_articles_search_snapshot_v2',
            'idx_embedding_jobs_pending_v2',
            'idx_embedding_jobs_processing_lease_v2'
          )
        ORDER BY cls.relname
    """)
    return cursor.fetchall()


def test_actual_runner_bootstraps_twice_and_story_resolution_is_safe():
    dsn, psycopg2 = _requirements()
    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            _reset(cursor, initialize=True)
            cursor.execute("""
                INSERT INTO sources(
                    id,name,url,country_code,source_type,weight,language,tier
                ) VALUES
                  (901,'Direct Publisher','https://direct.example','FI','rss',1,
                   'fi','mainstream'),
                  (902,'Google News (FI) — Россия',
                   'https://news.google.com/rss/search?q=Russia&hl=fi',
                   'FI','rss',1,'fi','mainstream')
            """)
            cursor.execute("""
                INSERT INTO articles(
                    id,source_id,external_id,title,url,published_at,collected_at,
                    language,title_normalized,is_duplicate,is_backfill
                ) VALUES
                  (901,901,'direct-before-024','Direct before migration',
                   'https://direct.example/story',NOW(),NOW(),'fi',
                   'direct before migration',FALSE,FALSE),
                  (902,902,'discovery-before-024','Discovery before migration',
                   'https://news.google.com/articles/discovery',NOW(),NOW(),'fi',
                   'discovery before migration',FALSE,FALSE)
            """)

        first = _run_migrations(dsn)
        _assert_success(first)
        assert "applying 002_threads.sql" in first.stdout
        assert "applying 022_postgres_hardening.sql" in first.stdout
        second = _run_migrations(dsn)
        _assert_success(second)
        assert "skip 022_postgres_hardening.sql (already applied)" in second.stdout

        with connection.cursor() as cursor:
            rows = _index_rows(cursor)
            assert [row[0] for row in rows] == [
                "idx_articles_search_snapshot_v2",
                "idx_embedding_jobs_pending_v2",
                "idx_embedding_jobs_processing_lease_v2",
            ]
            assert all(row[2:] == (True, True) for row in rows)
            definitions = {row[0]: row[1] for row in rows}
            assert "COALESCE(collected_at, published_at) DESC, id DESC" in definitions[
                "idx_articles_search_snapshot_v2"
            ]
            assert "(profile_id, available_at, id)" in definitions[
                "idx_embedding_jobs_pending_v2"
            ]
            assert "(profile_id, updated_at, id)" in definitions[
                "idx_embedding_jobs_processing_lease_v2"
            ]

            cursor.execute("""
                SELECT conname FROM pg_constraint
                WHERE conrelid='thread_articles'::regclass AND contype='f'
                ORDER BY conname
            """)
            assert [row[0] for row in cursor.fetchall()] == [
                "thread_articles_article_fk",
                "thread_articles_thread_fk",
            ]
            cursor.execute("SELECT count(*) FROM schema_migrations")
            assert cursor.fetchone()[0] == len(list(MIGRATIONS.glob("*.sql")))
            cursor.execute("""
                SELECT config->>'feed_mode'
                FROM sources
                WHERE id = 902
            """)
            assert cursor.fetchone()[0] == "publisher_discovery"
            cursor.execute("""
                SELECT cls.relname, idx.indisvalid, idx.indisready
                FROM pg_class cls
                JOIN pg_namespace ns ON ns.oid = cls.relnamespace
                JOIN pg_index idx ON idx.indexrelid = cls.oid
                WHERE ns.nspname = 'public'
                  AND cls.relname IN (
                    'idx_articles_publisher_source_id',
                    'idx_article_discoveries_quarantine_keyset',
                    'uq_articles_publisher_external_id'
                  )
                ORDER BY cls.relname
            """)
            assert cursor.fetchall() == [
                ("idx_article_discoveries_quarantine_keyset", True, True),
                ("idx_articles_publisher_source_id", True, True),
                ("uq_articles_publisher_external_id", True, True),
            ]
            cursor.execute("""
                SELECT id, source_id, external_id, url
                FROM articles
                WHERE id IN (901, 902)
                ORDER BY id
            """)
            assert cursor.fetchall() == [
                (901, 901, "direct-before-024", "https://direct.example/story"),
                (
                    902,
                    902,
                    "discovery-before-024",
                    "https://news.google.com/articles/discovery",
                ),
            ]
            cursor.execute("""
                SELECT article_id, id, country_code
                FROM article_country_facts
                WHERE article_id IN (901, 902)
                ORDER BY article_id
            """)
            assert cursor.fetchall() == [(901, 901, "FI")]

        engine = create_engine(dsn)
        Session = sessionmaker(bind=engine, expire_on_commit=False)
        at = datetime.now(timezone.utc).replace(microsecond=0)
        with Session.begin() as session:
            session.execute(text("""
                INSERT INTO sources(
                    id,name,url,country_code,source_type,weight,language,tier
                ) VALUES (1,'Smoke','https://example.test','ES','rss',1,'es','mainstream')
            """))
            session.execute(text("""
                INSERT INTO articles(
                    id,source_id,external_id,title,published_at,collected_at,
                    language,title_normalized,is_duplicate,is_backfill
                ) VALUES (1,1,'smoke','Story evidence',:at,:at,'es',
                          'story evidence',FALSE,FALSE)
            """), {"at": at})
            session.execute(text("""
                INSERT INTO stories(id,slug,title_ru,lifecycle,first_seen,last_seen,meta)
                VALUES
                  (10,'canonical','Canonical','developing',:at,:at,'{}'),
                  (11,'superseded','Superseded','resolved',:at,:at,CAST(:m11 AS jsonb)),
                  (12,'overflow','Overflow','resolved',:at,:at,CAST(:m12 AS jsonb)),
                  (13,'nonnumeric','Nonnumeric','resolved',:at,:at,CAST(:m13 AS jsonb)),
                  (14,'missing','Missing','resolved',:at,:at,CAST(:m14 AS jsonb)),
                  (15,'cycle-a','Cycle A','resolved',:at,:at,CAST(:m15 AS jsonb)),
                  (16,'cycle-b','Cycle B','resolved',:at,:at,CAST(:m16 AS jsonb)),
                  (17,'chain','Chain','resolved',:at,:at,CAST(:m17 AS jsonb))
            """), {
                "at": at,
                "m11": '{"merged_into_story_id":10}',
                "m12": '{"merged_into_story_id":"999999999999999999999999999999999999"}',
                "m13": '{"merged_into_story_id":"not-a-number"}',
                "m14": '{"merged_into_story_id":999}',
                "m15": '{"merged_into_story_id":16}',
                "m16": '{"merged_into_story_id":15}',
                "m17": '{"merged_into_story_id":11}',
            })
            session.execute(text("""
                INSERT INTO story_articles(
                    story_id,article_id,membership_confidence,evidence
                ) VALUES (10,1,0.9,'{}'),(11,1,0.7,'{}')
            """))
            assert _emit(
                session,
                "tier_convergence",
                "ES",
                "tier_convergence:ES:postgres-hardening-v2",
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
                    story_ids=(11, 12, 13, 14, 15, 17),
                    confidence=0.9,
                    completeness="complete",
                    explanation={"rule": "rule"},
                ),
            ) is True

        with Session() as session:
            persisted = session.execute(text("""
                SELECT se.story_ids
                FROM signal_evidence se
                JOIN signals s ON s.id=se.signal_id
                WHERE s.dedup_key='tier_convergence:ES:postgres-hardening-v2'
            """)).scalar_one()
            details = {
                story_id: SqlSignalDetailService._load_story(
                    session, story_ids=(story_id,), article_ids=()
                )
                for story_id in (11, 12, 13, 14, 15, 17)
            }

        assert persisted == [10, 12, 13, 14]
        assert details[11].id == 10
        assert details[12].id == 12
        assert details[13].id == 13
        assert details[14].id == 14
        assert details[15] is None
        assert details[17].id == 10
        engine.dispose()
    finally:
        connection.close()


def test_actual_runner_upgrades_old_019_and_recovers_invalid_shadows():
    dsn, psycopg2 = _requirements()
    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            _reset(cursor, initialize=True)
            cursor.execute("DROP INDEX idx_articles_search_snapshot")
            cursor.execute("DROP INDEX idx_embedding_jobs_pending")
            cursor.execute("DROP INDEX idx_embedding_jobs_processing_lease")
            cursor.execute("""
                CREATE INDEX idx_embedding_jobs_pending
                ON embedding_jobs(available_at, id) WHERE status='pending'
            """)
            cursor.execute("""
                CREATE INDEX idx_articles_search_snapshot_v2
                ON articles ((COALESCE(collected_at,published_at)) DESC, id DESC)
            """)
            cursor.execute("""
                CREATE INDEX idx_embedding_jobs_pending_v2
                ON embedding_jobs(profile_id,available_at,id) WHERE status='pending'
            """)
            cursor.execute("""
                CREATE INDEX idx_embedding_jobs_processing_lease_v2
                ON embedding_jobs(profile_id,updated_at,id) WHERE status='processing'
            """)
            cursor.execute("""
                UPDATE pg_index SET indisvalid=FALSE
                WHERE indexrelid IN (
                    'idx_articles_search_snapshot_v2'::regclass,
                    'idx_embedding_jobs_pending_v2'::regclass,
                    'idx_embedding_jobs_processing_lease_v2'::regclass
                )
            """)
            cursor.execute("""
                CREATE TABLE schema_migrations(
                    filename TEXT PRIMARY KEY, applied_at TIMESTAMPTZ DEFAULT now()
                )
            """)
            cursor.executemany(
                "INSERT INTO schema_migrations(filename) VALUES (%s)",
                [(path.name,) for path in sorted(MIGRATIONS.glob("*.sql"))
                 if path.name != "022_postgres_hardening.sql"],
            )
            cursor.execute("CREATE SCHEMA role_schema")
            _runner_search_path(cursor, reset=False)

        probe = psycopg2.connect(dsn)
        try:
            with probe.cursor() as cursor:
                cursor.execute("SELECT current_schema()")
                assert cursor.fetchone()[0] == "role_schema"
        finally:
            probe.close()

        first = _run_migrations(dsn)
        _assert_success(first)
        assert "applying 022_postgres_hardening.sql" in first.stdout
        assert "applying 001_api_usage.sql" not in first.stdout
        second = _run_migrations(dsn)
        _assert_success(second)
        assert "skip 022_postgres_hardening.sql (already applied)" in second.stdout

        with connection.cursor() as cursor:
            rows = _index_rows(cursor)
            assert len(rows) == 3
            assert all(row[2:] == (True, True) for row in rows)
            cursor.execute("""
                SELECT to_regclass('idx_articles_search_snapshot'),
                       to_regclass('idx_embedding_jobs_pending'),
                       to_regclass('idx_embedding_jobs_processing_lease')
            """)
            assert cursor.fetchone() == (None, None, None)
            cursor.execute("""
                SELECT count(*) FROM schema_migrations
                WHERE filename='022_postgres_hardening.sql'
            """)
            assert cursor.fetchone()[0] == 1
    finally:
        with connection.cursor() as cursor:
            _runner_search_path(cursor, reset=True)
        connection.close()


def test_actual_runner_does_not_record_failure_and_retries_same_file():
    dsn, psycopg2 = _requirements()
    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            _reset(cursor, initialize=False)

        failed = _run_migrations(dsn, RETRY_MIGRATIONS)
        assert failed.returncode != 0
        assert "applying 002_retry_gate.sql" in failed.stdout
        assert "003_after.sql" not in failed.stdout
        with connection.cursor() as cursor:
            cursor.execute("SELECT filename FROM schema_migrations ORDER BY filename")
            assert [row[0] for row in cursor.fetchall()] == ["001_ok.sql"]
            cursor.execute("CREATE TABLE migration_retry_gate(id integer)")

        retried = _run_migrations(dsn, RETRY_MIGRATIONS)
        _assert_success(retried)
        assert "skip 001_ok.sql (already applied)" in retried.stdout
        assert "applying 002_retry_gate.sql" in retried.stdout
        assert "applying 003_after.sql" in retried.stdout
        with connection.cursor() as cursor:
            cursor.execute("SELECT step FROM migration_retry_log ORDER BY step")
            assert [row[0] for row in cursor.fetchall()] == [1, 2, 3]
            cursor.execute("SELECT count(*) FROM schema_migrations")
            assert cursor.fetchone()[0] == 3
    finally:
        connection.close()
