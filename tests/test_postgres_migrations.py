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
from decimal import Decimal
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


def _downgrade_to_pre_024(cursor) -> None:
    cursor.execute("DROP VIEW IF EXISTS public.article_country_facts")
    cursor.execute("""
        DROP INDEX IF EXISTS public.idx_articles_publisher_source_id;
        DROP INDEX IF EXISTS public.idx_article_discoveries_quarantine_keyset;
        DROP INDEX IF EXISTS public.uq_articles_publisher_external_id;
        DROP TABLE IF EXISTS public.article_discoveries;
        DROP TABLE IF EXISTS public.publisher_domains;
    """)
    cursor.execute("""
        ALTER TABLE public.articles
          DROP CONSTRAINT IF EXISTS articles_geo_status_check,
          DROP COLUMN IF EXISTS publisher_source_id,
          DROP COLUMN IF EXISTS publisher_name,
          DROP COLUMN IF EXISTS publisher_url,
          DROP COLUMN IF EXISTS publisher_domain,
          DROP COLUMN IF EXISTS geo_country_code,
          DROP COLUMN IF EXISTS geo_status,
          DROP COLUMN IF EXISTS geo_method,
          DROP COLUMN IF EXISTS geo_confidence,
          DROP COLUMN IF EXISTS geo_verified_at,
          DROP COLUMN IF EXISTS resolved_url
    """)
    cursor.execute("""
        UPDATE public.sources
        SET config = COALESCE(config, '{}'::jsonb) - 'feed_mode'
        WHERE config ? 'feed_mode'
    """)


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
                   'FI','rss',1,'fi','mainstream'),
                  (903,'Google News (FI site) — Россия',
                   'https://news.google.com/rss/search?q=site:publisher.example+Russia&hl=fi',
                   'FI','rss',1,'fi','mainstream')
            """)
            _downgrade_to_pre_024(cursor)
            cursor.execute("""
                SELECT to_regclass('public.publisher_domains'),
                       to_regclass('public.article_discoveries'),
                       to_regclass('public.article_country_facts'),
                       to_regclass('public.idx_articles_publisher_source_id'),
                       to_regclass(
                         'public.idx_article_discoveries_quarantine_keyset'
                       ),
                       to_regclass('public.uq_articles_publisher_external_id')
            """)
            assert cursor.fetchone() == (None, None, None, None, None, None)
            cursor.execute("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'articles'
                  AND column_name IN (
                    'publisher_source_id', 'publisher_name', 'publisher_url',
                    'publisher_domain', 'geo_country_code', 'geo_status',
                    'geo_method', 'geo_confidence', 'geo_verified_at',
                    'resolved_url'
                  )
            """)
            assert cursor.fetchall() == []
            cursor.execute("""
                SELECT id, config->>'feed_mode'
                FROM sources
                WHERE id IN (901, 902, 903)
                ORDER BY id
            """)
            assert cursor.fetchall() == [(901, None), (902, None), (903, None)]
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
                   'discovery before migration',FALSE,FALSE),
                  (903,903,'site-before-024','Site wrapper before migration',
                   'https://publisher.example/story',NOW(),NOW(),'fi',
                   'site wrapper before migration',FALSE,FALSE)
            """)

        first = _run_migrations(dsn)
        _assert_success(first)
        assert "applying 002_threads.sql" in first.stdout
        assert "applying 022_postgres_hardening.sql" in first.stdout
        assert "applying 024_google_news_publisher_attribution.sql" in first.stdout
        second = _run_migrations(dsn)
        _assert_success(second)
        assert "skip 022_postgres_hardening.sql (already applied)" in second.stdout
        assert "skip 024_google_news_publisher_attribution.sql (already applied)" in second.stdout

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
                SELECT id, config->>'feed_mode'
                FROM sources
                WHERE id IN (902, 903)
                ORDER BY id
            """)
            assert cursor.fetchall() == [
                (902, "publisher_discovery"),
                (903, None),
            ]
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
                WHERE id IN (901, 902, 903)
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
                (903, 903, "site-before-024", "https://publisher.example/story"),
            ]
            cursor.execute("""
                SELECT article_id, id, country_code
                FROM article_country_facts
                WHERE article_id IN (901, 902, 903)
                ORDER BY article_id
            """)
            assert cursor.fetchall() == [
                (901, 901, "FI"),
                (903, 903, "FI"),
            ]

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


def test_actual_runner_rebuilds_invalid_duplicate_family_index_and_retries():
    dsn, psycopg2 = _requirements()
    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            _reset(cursor, initialize=True)
            cursor.execute("""
                SELECT cls.oid
                FROM pg_class cls
                JOIN pg_namespace ns ON ns.oid = cls.relnamespace
                WHERE ns.nspname = 'public'
                  AND cls.relname = 'idx_articles_duplicate_of'
            """)
            invalid_oid = cursor.fetchone()[0]
            cursor.execute("""
                UPDATE pg_index
                SET indisvalid = FALSE,
                    indisready = FALSE
                WHERE indexrelid =
                      'public.idx_articles_duplicate_of'::regclass
            """)
            cursor.execute("CREATE SCHEMA role_schema")
            cursor.execute("""
                CREATE TABLE role_schema.articles(
                    id INTEGER PRIMARY KEY,
                    duplicate_of INTEGER
                )
            """)
            cursor.execute("""
                CREATE INDEX idx_articles_duplicate_of
                ON role_schema.articles(duplicate_of)
            """)
            cursor.execute("""
                CREATE TABLE schema_migrations(
                    filename TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ DEFAULT now()
                )
            """)
            cursor.executemany(
                "INSERT INTO schema_migrations(filename) VALUES (%s)",
                [
                    (path.name,)
                    for path in sorted(MIGRATIONS.glob("*.sql"))
                    if path.name != "025_articles_duplicate_family_index.sql"
                ],
            )
            _runner_search_path(cursor, reset=False)

        first = _run_migrations(dsn)
        _assert_success(first)
        assert "applying 025_articles_duplicate_family_index.sql" in first.stdout
        assert "applying 024_google_news_publisher_attribution.sql" not in first.stdout

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT cls.oid,
                       target.oid = 'public.articles'::regclass AS correct_table,
                       ns.nspname,
                       pg_get_indexdef(cls.oid),
                       idx.indisvalid,
                       idx.indisready
                FROM pg_class cls
                JOIN pg_namespace ns ON ns.oid = cls.relnamespace
                JOIN pg_index idx ON idx.indexrelid = cls.oid
                JOIN pg_class target ON target.oid = idx.indrelid
                WHERE ns.nspname = 'public'
                  AND cls.relname = 'idx_articles_duplicate_of'
            """)
            rebuilt = cursor.fetchone()
            assert rebuilt[0] != invalid_oid
            assert rebuilt[1:3] == (True, "public")
            assert (
                "ON public.articles USING btree (duplicate_of)"
                in rebuilt[3]
            )
            assert "WHERE (duplicate_of IS NOT NULL)" in rebuilt[3]
            assert rebuilt[4:] == (True, True)
            cursor.execute("""
                SELECT pg_get_indexdef(cls.oid),
                       idx.indisvalid,
                       idx.indisready
                FROM pg_class cls
                JOIN pg_namespace ns ON ns.oid = cls.relnamespace
                JOIN pg_index idx ON idx.indexrelid = cls.oid
                WHERE ns.nspname = 'role_schema'
                  AND cls.relname = 'idx_articles_duplicate_of'
            """)
            shadow = cursor.fetchone()
            assert "ON role_schema.articles" in shadow[0]
            assert shadow[1:] == (True, True)
            cursor.execute("""
                SELECT count(*)
                FROM public.schema_migrations
                WHERE filename = '025_articles_duplicate_family_index.sql'
            """)
            assert cursor.fetchone()[0] == 1

        second = _run_migrations(dsn)
        _assert_success(second)
        assert (
            "skip 025_articles_duplicate_family_index.sql (already applied)"
            in second.stdout
        )
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


def test_temperature_anomaly_precision_migration_preserves_rows_and_retries():
    dsn, psycopg2 = _requirements()
    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            _reset(cursor, initialize=True)
            cursor.execute("""
                ALTER TABLE public.temperature
                ALTER COLUMN anomaly_score TYPE NUMERIC(4,2)
            """)
            cursor.execute("""
                INSERT INTO public.temperature(
                    time, country_code, temperature, anomaly_score
                ) VALUES (%s, 'ES', 10.0, 99.99)
            """, (datetime(2026, 7, 15, tzinfo=timezone.utc),))

            migration_sql = (
                MIGRATIONS / "026_temperature_anomaly_precision.sql"
            ).read_text()
            cursor.execute(migration_sql)
            cursor.execute(migration_sql)

            cursor.execute("""
                SELECT numeric_precision, numeric_scale
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'temperature'
                  AND column_name = 'anomaly_score'
            """)
            assert cursor.fetchone() == (8, 2)

            extreme = Decimal("109544.33")
            cursor.execute("""
                INSERT INTO public.temperature(
                    time, country_code, temperature, anomaly_score
                ) VALUES (%s, 'ES', 100.0, %s)
            """, (datetime(2026, 7, 16, tzinfo=timezone.utc), extreme))
            cursor.execute("""
                SELECT anomaly_score
                FROM public.temperature
                ORDER BY time
            """)
            assert [row[0] for row in cursor.fetchall()] == [
                Decimal("99.99"),
                extreme,
            ]
    finally:
        connection.close()


def test_radar_migration_is_idempotent_and_preserves_audit_history():
    dsn, psycopg2 = _requirements()
    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            _reset(cursor, initialize=True)
            cursor.execute("""
                DROP TABLE IF EXISTS notification_events CASCADE;
                DROP TABLE IF EXISTS radar_contour_links CASCADE;
                DROP TABLE IF EXISTS radar_t0_revisions CASCADE;
                DROP TABLE IF EXISTS radar_state_events CASCADE;
                DROP TABLE IF EXISTS radar_trend_evidence CASCADE;
                DROP TABLE IF EXISTS radar_trend_members CASCADE;
                DROP TABLE IF EXISTS radar_trends CASCADE;
                DROP TABLE IF EXISTS action_events CASCADE;
                DROP TABLE IF EXISTS radar_observations CASCADE;
                DROP TABLE IF EXISTS analysis_runs CASCADE;
                DROP FUNCTION IF EXISTS public.reject_radar_history_mutation();
            """)
            migration_sql = (
                MIGRATIONS / "027_early_warning_radar.sql"
            ).read_text()
            cursor.execute(migration_sql)
            cursor.execute(migration_sql)

            cursor.execute("""
                INSERT INTO analysis_runs(
                    public_id, run_type, input_hash, status
                ) VALUES (
                    '00000000-0000-0000-0000-000000000010', 'radar-test',
                    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                    'pending'
                )
                RETURNING detector_version, model_version
            """)
            assert cursor.fetchone() == ('not_applicable', 'not_applicable')
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cursor.execute("""
                    INSERT INTO analysis_runs(
                        public_id, run_type, input_hash, status
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000011', 'radar-test',
                        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                        'pending'
                    )
                """)

            cursor.execute("""
                INSERT INTO countries(code, name_ru, name_en, iso3, region)
                VALUES ('XZ', 'Тестовая страна', 'Test country', 'XZZ', 'test')
            """)
            country_code = 'XZ'
            cursor.execute("""
                INSERT INTO articles(title, url, published_at)
                VALUES ('Radar evidence', 'https://example.test/radar', NOW())
                RETURNING id
            """)
            article_id = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO radar_trends(
                    public_id, scope, contour, country_code, subject_key, title_ru,
                    direction, wave_key, state, confidence, coverage_confidence,
                    first_observed_at, detector_version
                ) VALUES (
                    '00000000-0000-0000-0000-000000000001', 'country', 'media',
                    %s, 'policy:test', 'Тест', 'warming', 'wave:test:1',
                    'candidate', 0.5, 0.5, NOW(), 'test-v1'
                ) RETURNING id
            """, (country_code,))
            trend_id = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO radar_trends(
                    public_id, scope, contour, country_code, subject_key, title_ru,
                    direction, wave_key, state, confidence, coverage_confidence,
                    first_observed_at, detector_version
                ) VALUES (
                    '00000000-0000-0000-0000-000000000015', 'country', 'media',
                    %s, 'policy:test', 'Вторая волна', 'warming', 'wave:test:2',
                    'candidate', 0.5, 0.5, NOW(), 'test-v1'
                )
            """, (country_code,))
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cursor.execute("""
                    INSERT INTO radar_trends(
                        public_id, scope, contour, country_code, subject_key,
                        title_ru, direction, wave_key, state, confidence,
                        coverage_confidence, first_observed_at, detector_version
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000016', 'country',
                        'media', %s, 'policy:test', 'Дубликат волны', 'warming',
                        'wave:test:1', 'candidate', 0.5, 0.5, NOW(), 'test-v1'
                    )
                """, (country_code,))
            cursor.execute("""
                INSERT INTO radar_trends(
                    public_id, scope, subject_key, title_ru, direction, meta_key,
                    state, confidence, coverage_confidence, first_observed_at,
                    detector_version
                ) VALUES (
                    '00000000-0000-0000-0000-000000000005', 'meta',
                    'policy:test', 'Мета-тест', 'warming', 'meta:test:1',
                    'candidate', 0.5, 0.5, NOW(), 'test-v1'
                ) RETURNING id
            """)
            meta_trend_id = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO radar_trends(
                    public_id, scope, subject_key, title_ru, direction, meta_key,
                    state, confidence, coverage_confidence, first_observed_at,
                    detector_version
                ) VALUES (
                    '00000000-0000-0000-0000-000000000020', 'meta',
                    'policy:test', 'Второй мета-тренд', 'warming', 'meta:test:2',
                    'candidate', 0.5, 0.5, NOW(), 'test-v1'
                )
            """)
            with pytest.raises(psycopg2.errors.UniqueViolation):
                cursor.execute("""
                    INSERT INTO radar_trends(
                        public_id, scope, subject_key, title_ru, direction,
                        meta_key, state, confidence, coverage_confidence,
                        first_observed_at, detector_version
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000017', 'meta',
                        'policy:test', 'Мета-дубликат', 'warming', 'meta:test:1',
                        'candidate', 0.5, 0.5, NOW(), 'test-v1'
                    )
                """)
            with pytest.raises(psycopg2.errors.CheckViolation):
                cursor.execute("""
                    INSERT INTO radar_trends(
                        public_id, scope, subject_key, title_ru, direction, state,
                        confidence, coverage_confidence, first_observed_at,
                        detector_version
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000021', 'meta',
                        'policy:missing-meta-key', 'Мета без ключа', 'warming',
                        'candidate', 0.5, 0.5, NOW(), 'test-v1'
                    )
                """)
            with pytest.raises(psycopg2.errors.CheckViolation):
                cursor.execute("""
                    INSERT INTO radar_trends(
                        public_id, scope, subject_key, title_ru, direction,
                        wave_key, meta_key, state, confidence, coverage_confidence,
                        first_observed_at, detector_version
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000018', 'meta',
                        'policy:meta-wave', 'Недопустимая мета-волна', 'warming',
                        'wave:meta', 'meta:invalid', 'candidate', 0.5, 0.5,
                        NOW(), 'test-v1'
                    )
                """)
            with pytest.raises(psycopg2.errors.CheckViolation):
                cursor.execute("""
                    INSERT INTO radar_trends(
                        public_id, scope, contour, country_code, subject_key,
                        title_ru, direction, state, confidence,
                        coverage_confidence, first_observed_at, detector_version
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000019', 'country',
                        'media', %s, 'policy:no-wave', 'Без волны', 'warming',
                        'candidate', 0.5, 0.5, NOW(), 'test-v1'
                    )
                """, (country_code,))
            with pytest.raises(psycopg2.errors.CheckViolation):
                cursor.execute("""
                    INSERT INTO radar_trends(
                        public_id, scope, contour, country_code, subject_key,
                        title_ru, direction, wave_key, meta_key, state,
                        confidence, coverage_confidence, first_observed_at,
                        detector_version
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000022', 'country',
                        'media', %s, 'policy:country-meta-key',
                        'Страна с мета-ключом', 'warming', 'wave:country',
                        'meta:invalid', 'candidate', 0.5, 0.5, NOW(), 'test-v1'
                    )
                """, (country_code,))
            cursor.execute("""
                INSERT INTO radar_trends(
                    public_id, scope, contour, country_code, subject_key, title_ru,
                    direction, wave_key, state, confidence, coverage_confidence,
                    first_observed_at, detector_version
                ) VALUES (
                    '00000000-0000-0000-0000-000000000006', 'country', 'action',
                    %s, 'policy:test', 'Тест действия', 'warming',
                    'wave:action:1', 'candidate', 0.5, 0.5, NOW(), 'test-v1'
                ) RETURNING id
            """, (country_code,))
            action_trend_id = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO radar_trends(
                    public_id, scope, contour, country_code, subject_key, title_ru,
                    direction, wave_key, state, confidence, coverage_confidence,
                    first_observed_at, detector_version
                ) VALUES (
                    '00000000-0000-0000-0000-000000000007', 'country', 'action',
                    %s, 'policy:test', 'Несовместимое действие', 'cooling',
                    'wave:action:2', 'candidate', 0.5, 0.5, NOW(), 'test-v1'
                ) RETURNING id
            """, (country_code,))
            incompatible_action_trend_id = cursor.fetchone()[0]

            with pytest.raises(psycopg2.errors.CheckViolation):
                cursor.execute("""
                    INSERT INTO radar_trend_members(
                        public_id, meta_trend_id, country_trend_id
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000008', %s, %s
                    )
                """, (trend_id, meta_trend_id))
            with pytest.raises(psycopg2.errors.CheckViolation):
                cursor.execute("""
                    INSERT INTO radar_contour_links(
                        public_id, media_trend_id, action_trend_id, status
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000009', %s, %s,
                        'divergent'
                    )
                """, (action_trend_id, trend_id))
            with pytest.raises(psycopg2.errors.CheckViolation):
                cursor.execute("""
                    INSERT INTO radar_contour_links(
                        public_id, media_trend_id, action_trend_id, status
                    ) VALUES (
                        '00000000-0000-0000-0000-000000000012', %s, %s,
                        'divergent'
                    )
                """, (trend_id, incompatible_action_trend_id))
            cursor.execute("""
                INSERT INTO radar_trend_members(
                    public_id, meta_trend_id, country_trend_id
                ) VALUES (
                    '00000000-0000-0000-0000-000000000013', %s, %s
                )
            """, (meta_trend_id, trend_id))
            cursor.execute("""
                INSERT INTO radar_contour_links(
                    public_id, media_trend_id, action_trend_id, status
                ) VALUES (
                    '00000000-0000-0000-0000-000000000014', %s, %s, 'aligned'
                )
            """, (trend_id, action_trend_id))
            with pytest.raises(psycopg2.errors.CheckViolation):
                cursor.execute(
                    "UPDATE radar_trends SET subject_key = 'policy:changed' "
                    "WHERE id = %s",
                    (action_trend_id,),
                )
            with pytest.raises(psycopg2.errors.CheckViolation):
                cursor.execute(
                    "UPDATE radar_trends SET wave_key = 'wave:test:changed' "
                    "WHERE id = %s",
                    (trend_id,),
                )
            with pytest.raises(psycopg2.errors.CheckViolation):
                cursor.execute(
                    "UPDATE radar_trends SET meta_key = 'meta:test:changed' "
                    "WHERE id = %s",
                    (meta_trend_id,),
                )
            cursor.execute("""
                INSERT INTO radar_state_events(
                    public_id, trend_id, to_state, transition_reason
                ) VALUES (
                    '00000000-0000-0000-0000-000000000002', %s, 'candidate',
                    'test'
                ) RETURNING id
            """, (trend_id,))
            state_event_id = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO radar_t0_revisions(
                    public_id, trend_id, revised_t0, revision_kind, reason
                ) VALUES (
                    '00000000-0000-0000-0000-000000000003', %s, NOW(),
                    'automatic', 'test'
                ) RETURNING id
            """, (trend_id,))
            t0_revision_id = cursor.fetchone()[0]
            cursor.execute("""
                INSERT INTO radar_trend_evidence(
                    public_id, trend_id, article_id, role
                ) VALUES (
                    '00000000-0000-0000-0000-000000000004', %s, %s, 'support'
                )
            """, (trend_id, article_id))

            with pytest.raises(psycopg2.errors.RaiseException):
                cursor.execute(
                    "UPDATE radar_state_events SET transition_reason = 'changed' "
                    "WHERE id = %s",
                    (state_event_id,),
                )
            with pytest.raises(psycopg2.errors.RaiseException):
                cursor.execute(
                    "DELETE FROM radar_t0_revisions WHERE id = %s",
                    (t0_revision_id,),
                )
            with pytest.raises(psycopg2.errors.ForeignKeyViolation):
                cursor.execute("DELETE FROM articles WHERE id = %s", (article_id,))
    finally:
        connection.close()
