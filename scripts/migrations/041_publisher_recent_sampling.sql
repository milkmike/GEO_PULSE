-- Ordered attributed-publisher probes must not scan the full article archive.
-- Recover an interrupted concurrent build without disturbing a healthy index.
SELECT format('DROP INDEX CONCURRENTLY IF EXISTS %I.%I;',namespace.nspname,cls.relname)
FROM pg_index idx
JOIN pg_class cls ON cls.oid=idx.indexrelid
JOIN pg_namespace namespace ON namespace.oid=cls.relnamespace
WHERE namespace.nspname='public' AND cls.relname='idx_articles_publisher_published_live'
  AND idx.indrelid='public.articles'::regclass
  AND (NOT idx.indisvalid OR NOT idx.indisready)
\gexec

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_articles_publisher_published_live
 ON public.articles(publisher_source_id,published_at DESC,id DESC)
 WHERE publisher_source_id IS NOT NULL AND is_duplicate=FALSE
   AND geo_status IN ('source_verified','publisher_verified','publisher_reassigned');

DO $$ BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM pg_index idx JOIN pg_class cls ON cls.oid=idx.indexrelid
   WHERE idx.indrelid='public.articles'::regclass
     AND cls.relname='idx_articles_publisher_published_live'
     AND idx.indisvalid AND idx.indisready
 ) THEN RAISE EXCEPTION 'publisher sampling index is not valid and ready'; END IF;
END $$;
