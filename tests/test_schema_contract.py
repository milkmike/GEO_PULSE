from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def migration(name: str) -> str:
    return (ROOT / "scripts" / "migrations" / name).read_text()


def test_search_and_knowledge_schema_contract():
    sql = migration("019_search_knowledge.sql")
    for fragment in (
        "ADD COLUMN IF NOT EXISTS search_vector tsvector",
        "CREATE INDEX IF NOT EXISTS idx_articles_search_vector",
        "CREATE TABLE IF NOT EXISTS canonical_entities",
        "CREATE TABLE IF NOT EXISTS article_entity_mentions",
        "CREATE TABLE IF NOT EXISTS knowledge_edges",
        "CREATE TABLE IF NOT EXISTS embedding_profiles",
        "CREATE TABLE IF NOT EXISTS content_embeddings",
        "CREATE TABLE IF NOT EXISTS embedding_jobs",
        "ALTER TABLE analysis ADD COLUMN IF NOT EXISTS embedding vector",
        "CREATE INDEX IF NOT EXISTS idx_articles_search_snapshot",
        "ON articles ((COALESCE(collected_at, published_at)) DESC, id DESC)",
        "ON embedding_jobs(profile_id, available_at, id)",
        "ON embedding_jobs(profile_id, updated_at, id)",
        "WHERE status = 'processing'",
    ):
        assert fragment in sql


def test_postgres_hardening_is_additive_for_databases_that_already_ran_019():
    sql = migration("022_postgres_hardening.sql")
    for fragment in (
        "DROP INDEX CONCURRENTLY IF EXISTS idx_articles_search_snapshot_v2",
        "CREATE INDEX CONCURRENTLY idx_articles_search_snapshot_v2",
        "DROP INDEX CONCURRENTLY IF EXISTS idx_articles_search_snapshot",
        "DROP INDEX CONCURRENTLY IF EXISTS idx_embedding_jobs_pending_v2",
        "CREATE INDEX CONCURRENTLY idx_embedding_jobs_pending_v2",
        "DROP INDEX CONCURRENTLY IF EXISTS idx_embedding_jobs_pending",
        "ON embedding_jobs(profile_id, available_at, id)",
        "DROP INDEX CONCURRENTLY IF EXISTS idx_embedding_jobs_processing_lease_v2",
        "CREATE INDEX CONCURRENTLY idx_embedding_jobs_processing_lease_v2",
        "DROP INDEX CONCURRENTLY IF EXISTS idx_embedding_jobs_processing_lease",
        "ON embedding_jobs(profile_id, updated_at, id)",
        "WHERE status = 'processing'",
        "indisvalid AND idx.indisready",
        "DROP CONSTRAINT thread_articles_thread_id_fkey",
        "idx.indrelid = 'public.articles'::regclass",
        "idx.indrelid = 'public.embedding_jobs'::regclass",
        "cls.relnamespace = target.relnamespace",
    ):
        assert fragment in sql

    assert "current_schema()" not in sql

    assert sql.index("CREATE INDEX CONCURRENTLY idx_articles_search_snapshot_v2") < sql.index(
        "DROP INDEX CONCURRENTLY IF EXISTS idx_articles_search_snapshot;"
    )
    assert sql.index("CREATE INDEX CONCURRENTLY idx_embedding_jobs_pending_v2") < sql.index(
        "DROP INDEX CONCURRENTLY IF EXISTS idx_embedding_jobs_pending;"
    )


def test_threads_schema_precedes_legacy_thread_migrations_and_is_bootstrapped():
    migration_sql = migration("002_threads.sql")
    init = (ROOT / "data" / "init.sql").read_text()
    reference = (ROOT / "data" / "002_threads.sql").read_text()

    for fragment in (
        "CREATE TABLE IF NOT EXISTS threads",
        "CREATE TABLE IF NOT EXISTS thread_articles",
        "UNIQUE(country_code, thread_key)",
        "PRIMARY KEY (thread_id, article_id)",
        "CONSTRAINT thread_articles_thread_fk",
        "CONSTRAINT thread_articles_article_fk",
        "idx_threads_country",
        "idx_threads_status",
        "idx_threads_last_seen",
    ):
        assert fragment in reference
        assert fragment in migration_sql
        assert fragment in init


def test_migration_runner_is_strict_and_records_only_success():
    runner = (ROOT / "scripts" / "apply_migrations.sh").read_text()

    assert "set -euo pipefail" in runner
    assert "ON_ERROR_STOP=1" in runner
    assert "ON_ERROR_STOP=0" not in runner
    assert "some statements errored (tolerated)" not in runner
    assert "Record as applied regardless" not in runner
    assert "public.schema_migrations" in runner


def test_story_schema_contract():
    sql = migration("020_global_stories.sql")
    for table in ("stories", "story_articles", "story_countries", "story_entities", "story_events"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql


def test_story_article_orm_tracks_membership_generation_contract():
    from src.db import StoryArticle

    column = StoryArticle.__table__.c.membership_generation
    assert column.nullable is False
    assert column.default is not None
    assert column.default.arg == 0
    assert column.server_default is not None
    assert str(column.server_default.arg) == "0"


def test_evidence_schema_contract():
    sql = migration("021_signal_evidence_explanations.sql")
    assert "CREATE TABLE IF NOT EXISTS signal_evidence" in sql
    assert "CREATE TABLE IF NOT EXISTS index_change_explanations" in sql


def test_signal_evidence_array_indexes_are_present_for_new_and_existing_installs():
    migration_sql = migration("023_signal_evidence_array_indexes.sql")
    init_sql = (ROOT / "data" / "init.sql").read_text()

    for index_name, column in (
        ("idx_signal_evidence_story_ids_gin", "story_ids"),
        ("idx_signal_evidence_article_ids_gin", "article_ids"),
    ):
        assert index_name in migration_sql
        assert "DROP INDEX CONCURRENTLY" in migration_sql
        assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in migration_sql
        assert f"{index_name}\n  ON signal_evidence USING GIN ({column})" in init_sql
    assert "namespace.nspname = 'public'" in migration_sql
    assert "index_state.indrelid = 'public.signal_evidence'::regclass" in migration_sql
    assert migration_sql.count("ON public.signal_evidence USING GIN") == 2
    assert "CREATE OR REPLACE PROCEDURE public.backfill_story_action_snapshots" in migration_sql
    assert "LIMIT 5000" in migration_sql
    assert "jsonb_typeof(sa.evidence) = 'object'" in migration_sql
    assert "ELSE '{}'::jsonb" in migration_sql
    assert "jsonb_set" in migration_sql
    assert "membership_confidence_snapshot" in migration_sql
    assert "LEAST(1.0, GREATEST(" in migration_sql
    assert "0.0," in migration_sql
    assert "CALL public.backfill_story_action_snapshots()" in migration_sql
    assert "CREATE TABLE IF NOT EXISTS public.story_membership_clock" in migration_sql
    assert "ADD COLUMN IF NOT EXISTS membership_generation BIGINT" in migration_sql
    assert "ADD COLUMN IF NOT EXISTS membership_generation BIGINT;" in migration_sql
    assert "WHERE membership_generation IS NULL OR membership_generation < 0" in migration_sql
    assert "ALTER COLUMN membership_generation SET DEFAULT 0" in migration_sql
    assert "ALTER COLUMN membership_generation SET NOT NULL" in migration_sql
    assert "membership_generation BIGINT NOT NULL DEFAULT 0" in init_sql
    assert "CHECK (membership_generation >= 0)" in init_sql
    assert "story_articles_membership_generation_nonnegative" in migration_sql
    assert "INSERT INTO story_membership_clock" in init_sql
    assert "CHECK (highest_action_level BETWEEN 1 AND 6)" in init_sql
    assert "CHECK (action_level BETWEEN 1 AND 6)" in init_sql
    assert "stories_highest_action_level_range" in migration_sql
    assert "story_events_action_level_range" in migration_sql


def test_google_news_publisher_attribution_schema_contract():
    migration_sql = migration("024_google_news_publisher_attribution.sql")
    init_sql = (ROOT / "data" / "init.sql").read_text()
    for fragment in (
        "CREATE TABLE IF NOT EXISTS public.publisher_domains",
        "CREATE TABLE IF NOT EXISTS public.article_discoveries",
        "ADD COLUMN IF NOT EXISTS publisher_source_id INTEGER",
        "ADD COLUMN IF NOT EXISTS publisher_domain TEXT",
        "ADD COLUMN IF NOT EXISTS geo_country_code CHAR(2)",
        "ADD COLUMN IF NOT EXISTS geo_status VARCHAR(24)",
        "CREATE OR REPLACE VIEW public.article_country_facts",
        "publisher_reassigned",
        "legacy_unverified",
    ):
        assert fragment in migration_sql
        assert fragment.replace("public.", "") in init_sql

    assert "UPDATE articles SET source_id" not in migration_sql
    assert "DELETE FROM articles" not in migration_sql
    assert "POSITION('site:' IN LOWER(url)) = 0" in migration_sql
    assert "jsonb_build_object('feed_mode', 'publisher_discovery')" in migration_sql
    assert "indisvalid AND idx.indisready" in migration_sql
    for index_name in (
        "idx_articles_publisher_source_id",
        "idx_article_discoveries_quarantine_keyset",
        "uq_articles_publisher_external_id",
    ):
        assert index_name in migration_sql
        assert index_name in init_sql


def test_article_duplicate_family_index_is_present_and_retry_safe():
    migration_sql = migration("025_articles_duplicate_family_index.sql")
    init_sql = (ROOT / "data" / "init.sql").read_text()

    for sql in (migration_sql, init_sql):
        assert "idx_articles_duplicate_of" in sql
        assert "ON public.articles (duplicate_of)" in sql or (
            "ON articles(duplicate_of)" in sql
        )
        assert "WHERE duplicate_of IS NOT NULL" in sql
    assert "DROP INDEX CONCURRENTLY IF EXISTS" in migration_sql
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in migration_sql
    assert "NOT index_state.indisvalid OR NOT index_state.indisready" in migration_sql
    assert "index_state.indrelid = 'public.articles'::regclass" in migration_sql
    assert "idx.indisvalid AND idx.indisready" in migration_sql


def test_pipeline_recovery_indexes_are_present_for_new_and_existing_installs():
    migration_sql = migration("031_pipeline_recovery_indexes.sql")
    init_sql = (ROOT / "data" / "init.sql").read_text()

    for sql in (migration_sql, init_sql):
        assert "CREATE EXTENSION IF NOT EXISTS pgcrypto" in sql
        assert "idx_articles_pending_scan" in sql
        assert "(collected_at DESC, id DESC)" in sql
        assert "WHERE is_duplicate = FALSE" in sql
        assert "idx_articles_geo_published_live" in sql
        assert "(geo_country_code, published_at DESC, id DESC)" in sql
        assert "'source_verified','publisher_verified','publisher_reassigned'" in sql
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in migration_sql
    assert "DROP INDEX CONCURRENTLY IF EXISTS" in migration_sql
    assert "NOT index_state.indisvalid OR NOT index_state.indisready" in migration_sql
    assert "idx.indisvalid AND idx.indisready" in migration_sql


def test_temperature_anomaly_storage_preserves_unbounded_v1_z_scores():
    from src.db import Temperature
    from src.engine.index import _anomaly_statistics

    anomaly = _anomaly_statistics(100.0, [-100.0] * 29 + [-99.99])
    assert anomaly is not None
    assert anomaly[0] == 109544.33
    assert anomaly[0] > 99.99

    anomaly_type = Temperature.__table__.c.anomaly_score.type
    assert anomaly_type.precision == 8
    assert anomaly_type.scale == 2

    migration_sql = migration("026_temperature_anomaly_precision.sql")
    init_sql = (ROOT / "data" / "init.sql").read_text()
    assert "ALTER COLUMN anomaly_score TYPE NUMERIC(8,2)" in migration_sql
    assert "anomaly_score DECIMAL(8,2)" in init_sql


def test_radar_schema_is_additive_auditable_and_bootstrapped():
    migration_sql = migration("027_early_warning_radar.sql")
    init_sql = (ROOT / "data" / "init.sql").read_text()
    tables = (
        "radar_observations", "action_events", "radar_trends",
        "radar_trend_members", "radar_trend_evidence", "radar_state_events",
        "radar_t0_revisions", "radar_contour_links", "analysis_runs",
        "notification_events",
    )
    for sql in (migration_sql, init_sql):
        for table in tables:
            assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
        assert "CHECK (contour IN ('media','action'))" in sql
        assert "'candidate','emerging','confirmed','cooling','resolved','rejected'" in sql
        assert "'trigger','support','context','contradiction'" in sql
        assert "uq_radar_country_trend_identity" in sql
        assert "uq_radar_meta_trend_identity" in sql
        assert "wave_key TEXT" in sql
        assert "meta_key TEXT" in sql
        assert "AND wave_key IS NOT NULL" in sql
        assert "AND wave_key IS NULL" in sql
        assert "AND meta_key IS NULL" in sql
        assert "AND meta_key IS NOT NULL" in sql
        assert (
            "ON radar_trends(contour, country_code, subject_key, direction, "
            "wave_key, detector_version)" in sql
        )
        assert (
            "ON radar_trends(subject_key, direction, meta_key, detector_version)"
            in sql
        )
        assert "detector_version VARCHAR(40) NOT NULL DEFAULT 'not_applicable'" in sql
        assert "model_version VARCHAR(120) NOT NULL DEFAULT 'not_applicable'" in sql
        assert "validate_radar_trend_member_topology" in sql
        assert "validate_radar_contour_link_topology" in sql
    assert "UPDATE articles SET" not in migration_sql
    assert "DELETE FROM articles" not in migration_sql


def test_radar_evidence_root_backfill_is_atomic_non_destructive_and_indexed():
    migration_sql = migration("028_radar_evidence_roots.sql")
    init_sql = (ROOT / "data" / "init.sql").read_text()
    assert not migration_sql.lstrip().startswith("BEGIN;")
    assert not migration_sql.rstrip().endswith("COMMIT;")
    assert "DELETE FROM radar_trend_evidence" not in migration_sql
    assert "CREATE UNIQUE INDEX" not in migration_sql
    assert "FROM radar_observations observation" in migration_sql
    assert "COALESCE(target.article_id, observation.article_id)" in migration_sql
    for sql in (migration_sql, init_sql):
        assert "uq_radar_trend_evidence_observation" not in sql
        for root in ("article_id", "story_id", "signal_id", "canonical_entity_id"):
            assert f"idx_radar_trend_evidence_{root}_lookup" in sql
            assert f"WHERE {root} IS NOT NULL" in sql


def test_radar_evidence_relation_upgrade_repairs_the_legacy_unique_index():
    migration_sql = migration("029_radar_evidence_relation_rows.sql")
    init_sql = (ROOT / "data" / "init.sql").read_text()

    assert not migration_sql.lstrip().startswith("BEGIN;")
    assert not migration_sql.rstrip().endswith("COMMIT;")
    assert "DROP INDEX IF EXISTS public.uq_radar_trend_evidence_observation" in migration_sql
    assert "DELETE FROM radar_trend_evidence" not in migration_sql
    assert "FROM public.radar_observations observation" in migration_sql
    assert "COALESCE(target.article_id, observation.article_id)" in migration_sql
    for sql in (migration_sql, init_sql):
        assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_radar_trend_evidence_observation" not in sql
        for root in ("article_id", "story_id", "signal_id", "canonical_entity_id"):
            assert f"idx_radar_trend_evidence_{root}_lookup" in sql


def test_radar_contour_alignment_is_separate_from_local_trend_identity():
    migration_sql = migration("030_radar_contour_alignment_identity.sql")
    init_sql = (ROOT / "data" / "init.sql").read_text()

    assert not migration_sql.lstrip().startswith("BEGIN;")
    assert not migration_sql.rstrip().endswith("COMMIT;")
    assert "DELETE FROM" not in migration_sql
    for sql in (migration_sql, init_sql):
        assert "alignment_subject TEXT" in sql
        assert "alignment_direction VARCHAR(24)" in sql
        assert (
            "media_alignment_subject IS DISTINCT FROM "
            "action_alignment_subject" in sql
        )
        assert (
            "media_alignment_direction IS DISTINCT FROM "
            "action_alignment_direction" in sql
        )
        assert "OLD.alignment_subject IS DISTINCT FROM NEW.alignment_subject" in sql
        assert "OLD.alignment_direction IS DISTINCT FROM NEW.alignment_direction" in sql


def test_radar_migration_runner_owns_transaction_and_marker_atomically():
    runner = (ROOT / "scripts" / "apply_migrations.sh").read_text()
    assert '-1 -f "$f" -c "$marker_sql"' in runner
    for filename in (
        "027_early_warning_radar.sql",
        "028_radar_evidence_roots.sql",
        "029_radar_evidence_relation_rows.sql",
        "030_radar_contour_alignment_identity.sql",
    ):
        migration_sql = migration(filename)
        assert "CONCURRENTLY" not in migration_sql
        assert not migration_sql.lstrip().startswith("BEGIN;")
        assert not migration_sql.rstrip().endswith("COMMIT;")


def test_google_news_publisher_attribution_orm_contract():
    from src.db import Article, ArticleDiscovery, PublisherDomain

    for column in (
        "publisher_source_id",
        "publisher_name",
        "publisher_url",
        "publisher_domain",
        "geo_country_code",
        "geo_status",
        "geo_method",
        "geo_confidence",
        "geo_verified_at",
        "resolved_url",
    ):
        assert column in Article.__table__.c

    assert PublisherDomain.__tablename__ == "publisher_domains"
    assert ArticleDiscovery.__tablename__ == "article_discoveries"


def test_search_candidate_indexes_are_present_for_new_and_existing_installs():
    migration_sql = migration("023_signal_evidence_array_indexes.sql")
    init_sql = (ROOT / "data" / "init.sql").read_text()

    assert "DROP INDEX CONCURRENTLY" in migration_sql
    assert "CREATE INDEX CONCURRENTLY IF NOT EXISTS" in migration_sql
    for index_definition in (
        (
            "idx_articles_language_published_id\n"
            "  ON public.articles (language, published_at DESC, id DESC)\n"
            "  WHERE is_duplicate = FALSE"
        ),
        (
            "idx_articles_source_candidates\n"
            "  ON public.articles (source_id, published_at DESC, id DESC)\n"
            "  WHERE is_duplicate = FALSE"
        ),
    ):
        assert index_definition in migration_sql
        assert index_definition.replace("public.", "") in init_sql


def test_story_snapshot_backfill_replaces_corrupt_json_values_safely():
    sql = migration("023_signal_evidence_array_indexes.sql")

    assert "jsonb_typeof(sa.evidence->'action_level_snapshot') = 'number'" in sql
    assert "sa.evidence->>'action_level_snapshot' ~ '^[1-6]$'" in sql
    assert "LEAST(6, GREATEST(1" in sql
    assert "sa.evidence->'membership_confidence_snapshot'" in sql
    assert ") = 'number'" in sql
    assert "BETWEEN 0.0 AND 1.0" in sql
    assert "ELSE to_jsonb(batch.action_level)" in sql
    assert "ELSE to_jsonb(batch.membership_confidence)" in sql


def test_init_schema_mirrors_new_tables():
    init = (ROOT / "data" / "init.sql").read_text()
    for table in (
        "canonical_entities", "entity_aliases", "article_entity_mentions",
        "knowledge_edges", "embedding_profiles", "content_embeddings",
        "embedding_jobs", "stories", "story_articles", "story_countries",
        "story_entities", "story_events", "signal_evidence",
        "index_change_explanations",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in init

    for fragment in (
        "CREATE INDEX IF NOT EXISTS idx_articles_search_snapshot",
        "ON articles ((COALESCE(collected_at, published_at)) DESC, id DESC)",
        "ON embedding_jobs(profile_id, available_at, id)",
        "ON embedding_jobs(profile_id, updated_at, id)",
        "WHERE status = 'processing'",
    ):
        assert fragment in init
