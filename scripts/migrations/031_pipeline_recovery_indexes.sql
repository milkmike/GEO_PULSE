-- Bounded analyzer recovery and selective country API reads.
-- Concurrent, additive indexes keep the migration safe on the live corpus.

\set ON_ERROR_STOP on

CREATE EXTENSION IF NOT EXISTS pgcrypto;

SELECT format(
  'DROP INDEX CONCURRENTLY IF EXISTS %I.%I;',
  namespace.nspname,
  index_class.relname
)
FROM pg_index index_state
JOIN pg_class index_class ON index_class.oid = index_state.indexrelid
JOIN pg_namespace namespace ON namespace.oid = index_class.relnamespace
WHERE namespace.nspname = 'public'
  AND index_class.relname IN (
    'idx_articles_pending_scan',
    'idx_articles_geo_published_live'
  )
  AND index_state.indrelid = 'public.articles'::regclass
  AND (NOT index_state.indisvalid OR NOT index_state.indisready)
\gexec

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_articles_pending_scan
  ON public.articles (collected_at DESC, id DESC)
  WHERE is_duplicate = FALSE;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_articles_geo_published_live
  ON public.articles (geo_country_code, published_at DESC, id DESC)
  WHERE is_duplicate = FALSE
    AND geo_status IN (
      'source_verified','publisher_verified','publisher_reassigned'
    );

DO $$
BEGIN
  IF (
    SELECT COUNT(*)
    FROM pg_index idx
    JOIN pg_class cls ON cls.oid = idx.indexrelid
    JOIN pg_class target ON target.oid = idx.indrelid
    WHERE idx.indrelid = 'public.articles'::regclass
      AND cls.relnamespace = target.relnamespace
      AND cls.relname IN (
        'idx_articles_pending_scan',
        'idx_articles_geo_published_live'
      )
      AND idx.indisvalid AND idx.indisready
  ) <> 2 THEN
    RAISE EXCEPTION 'pipeline recovery indexes are not valid and ready';
  END IF;
END
$$;
