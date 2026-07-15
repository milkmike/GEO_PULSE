-- Additive production indexes for installations that already recorded 019.

CREATE INDEX IF NOT EXISTS idx_articles_search_snapshot
  ON articles ((COALESCE(collected_at, published_at)) DESC, id DESC);

-- Migration 019 originally created this name without profile_id. Rebuild it
-- under the stable name so claims do not scan pending jobs of old profiles.
DROP INDEX IF EXISTS idx_embedding_jobs_pending;
CREATE INDEX idx_embedding_jobs_pending
  ON embedding_jobs(profile_id, available_at, id)
  WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_embedding_jobs_processing_lease
  ON embedding_jobs(profile_id, updated_at, id)
  WHERE status = 'processing';

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
