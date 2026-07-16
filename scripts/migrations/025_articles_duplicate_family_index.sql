-- Index both directions of duplicate-family traversal for existing installs.
-- Must run in psql autocommit mode: concurrent index operations cannot run
-- inside an explicit transaction. Invalid remnants are removed before retry.

\set ON_ERROR_STOP on

SELECT format(
  'DROP INDEX CONCURRENTLY IF EXISTS %I.%I;',
  namespace.nspname,
  index_class.relname
)
FROM pg_index index_state
JOIN pg_class index_class ON index_class.oid = index_state.indexrelid
JOIN pg_namespace namespace ON namespace.oid = index_class.relnamespace
WHERE namespace.nspname = 'public'
  AND index_class.relname = 'idx_articles_duplicate_of'
  AND index_state.indrelid = 'public.articles'::regclass
  AND (NOT index_state.indisvalid OR NOT index_state.indisready)
\gexec

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_articles_duplicate_of
  ON public.articles (duplicate_of)
  WHERE duplicate_of IS NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_index idx
    JOIN pg_class cls ON cls.oid = idx.indexrelid
    JOIN pg_class target ON target.oid = idx.indrelid
    WHERE idx.indrelid = 'public.articles'::regclass
      AND cls.relnamespace = target.relnamespace
      AND cls.relname = 'idx_articles_duplicate_of'
      AND idx.indisvalid AND idx.indisready
  ) THEN
    RAISE EXCEPTION 'index idx_articles_duplicate_of is not valid and ready';
  END IF;
END
$$;
