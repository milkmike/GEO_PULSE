-- Production-safe additive indexes for installations that already recorded 019.
--
-- Each replacement is built under a shadow name. A retry first removes a
-- possibly-invalid shadow left by an interrupted CREATE INDEX CONCURRENTLY.
-- The old index is dropped only after PostgreSQL reports the shadow ready and
-- valid. Shadow names remain as the final names to avoid a risky name swap.

DROP INDEX CONCURRENTLY IF EXISTS idx_articles_search_snapshot_v2;
CREATE INDEX CONCURRENTLY idx_articles_search_snapshot_v2
  ON articles ((COALESCE(collected_at, published_at)) DESC, id DESC);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_index idx
    JOIN pg_class cls ON cls.oid = idx.indexrelid
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    WHERE ns.nspname = current_schema()
      AND cls.relname = 'idx_articles_search_snapshot_v2'
      AND idx.indisvalid AND idx.indisready
  ) THEN
    RAISE EXCEPTION 'shadow index idx_articles_search_snapshot_v2 is not valid and ready';
  END IF;
END $$;

DROP INDEX CONCURRENTLY IF EXISTS idx_articles_search_snapshot;

DROP INDEX CONCURRENTLY IF EXISTS idx_embedding_jobs_pending_v2;
CREATE INDEX CONCURRENTLY idx_embedding_jobs_pending_v2
  ON embedding_jobs(profile_id, available_at, id)
  WHERE status = 'pending';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_index idx
    JOIN pg_class cls ON cls.oid = idx.indexrelid
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    WHERE ns.nspname = current_schema()
      AND cls.relname = 'idx_embedding_jobs_pending_v2'
      AND idx.indisvalid AND idx.indisready
  ) THEN
    RAISE EXCEPTION 'shadow index idx_embedding_jobs_pending_v2 is not valid and ready';
  END IF;
END $$;

DROP INDEX CONCURRENTLY IF EXISTS idx_embedding_jobs_pending;

DROP INDEX CONCURRENTLY IF EXISTS idx_embedding_jobs_processing_lease_v2;
CREATE INDEX CONCURRENTLY idx_embedding_jobs_processing_lease_v2
  ON embedding_jobs(profile_id, updated_at, id)
  WHERE status = 'processing';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_index idx
    JOIN pg_class cls ON cls.oid = idx.indexrelid
    JOIN pg_namespace ns ON ns.oid = cls.relnamespace
    WHERE ns.nspname = current_schema()
      AND cls.relname = 'idx_embedding_jobs_processing_lease_v2'
      AND idx.indisvalid AND idx.indisready
  ) THEN
    RAISE EXCEPTION 'shadow index idx_embedding_jobs_processing_lease_v2 is not valid and ready';
  END IF;
END $$;

DROP INDEX CONCURRENTLY IF EXISTS idx_embedding_jobs_processing_lease;

-- The original data/002_threads.sql used an implicit FK name. Migration 006
-- then added the canonical named FK, leaving two equivalent checks on legacy
-- installations. Drop the implicit duplicate only when both constraints exist.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'thread_articles'::regclass
      AND conname = 'thread_articles_thread_fk'
      AND contype = 'f'
  ) AND EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conrelid = 'thread_articles'::regclass
      AND conname = 'thread_articles_thread_id_fkey'
      AND contype = 'f'
  ) THEN
    ALTER TABLE thread_articles
      DROP CONSTRAINT thread_articles_thread_id_fkey;
  END IF;
END $$;
