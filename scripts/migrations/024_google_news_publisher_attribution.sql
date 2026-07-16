-- Publisher attribution and quarantine schema for broad Google News feeds.
-- This migration is additive: article identity and discovery provenance remain
-- unchanged while analytics can resolve a separately verified publisher.

\set ON_ERROR_STOP on

ALTER TABLE public.articles
  ADD COLUMN IF NOT EXISTS publisher_source_id INTEGER,
  ADD COLUMN IF NOT EXISTS publisher_name TEXT,
  ADD COLUMN IF NOT EXISTS publisher_url TEXT,
  ADD COLUMN IF NOT EXISTS publisher_domain TEXT,
  ADD COLUMN IF NOT EXISTS geo_country_code CHAR(2),
  ADD COLUMN IF NOT EXISTS geo_status VARCHAR(24) NOT NULL DEFAULT 'source_verified',
  ADD COLUMN IF NOT EXISTS geo_method VARCHAR(40),
  ADD COLUMN IF NOT EXISTS geo_confidence NUMERIC(4,3),
  ADD COLUMN IF NOT EXISTS geo_verified_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS resolved_url TEXT;

ALTER TABLE public.articles DROP CONSTRAINT IF EXISTS articles_geo_status_check;
ALTER TABLE public.articles ADD CONSTRAINT articles_geo_status_check CHECK (
  geo_status IN (
    'source_verified', 'publisher_verified', 'publisher_reassigned',
    'unverified', 'legacy_unverified'
  )
) NOT VALID;
ALTER TABLE public.articles VALIDATE CONSTRAINT articles_geo_status_check;

CREATE TABLE IF NOT EXISTS public.publisher_domains (
  domain TEXT PRIMARY KEY,
  publisher_source_id INTEGER NOT NULL REFERENCES public.sources(id),
  country_code CHAR(2) NOT NULL,
  status VARCHAR(16) NOT NULL CHECK (status IN ('verified', 'blocked')),
  method VARCHAR(32) NOT NULL,
  confidence NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.article_discoveries (
  id BIGSERIAL PRIMARY KEY,
  discovery_source_id INTEGER NOT NULL REFERENCES public.sources(id),
  external_id TEXT NOT NULL,
  title TEXT,
  body TEXT,
  google_url TEXT,
  published_at TIMESTAMPTZ NOT NULL,
  feed_country_code CHAR(2) NOT NULL,
  publisher_name TEXT,
  publisher_url TEXT,
  publisher_domain TEXT,
  geo_status VARCHAR(24) NOT NULL DEFAULT 'unverified',
  reason TEXT,
  raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  promoted_article_id INTEGER,
  UNIQUE (discovery_source_id, external_id)
);

UPDATE public.sources
SET config = COALESCE(config, '{}'::jsonb) ||
  jsonb_build_object('feed_mode', 'publisher_discovery')
WHERE url ILIKE 'https://news.google.com/rss/search%'
  AND POSITION('site:' IN LOWER(url)) = 0
  AND name LIKE 'Google News (%) — Россия';

-- Interrupted concurrent builds can leave invalid catalog entries. Remove only
-- those remnants, preserve healthy indexes, then verify every new index.
SELECT format(
  'DROP INDEX CONCURRENTLY IF EXISTS %I.%I;',
  namespace.nspname,
  index_class.relname
)
FROM pg_index index_state
JOIN pg_class index_class ON index_class.oid = index_state.indexrelid
JOIN pg_namespace namespace ON namespace.oid = index_class.relnamespace
WHERE namespace.nspname = 'public'
  AND (
    (
      index_class.relname IN (
        'idx_articles_publisher_source_id',
        'uq_articles_publisher_external_id'
      )
      AND index_state.indrelid = 'public.articles'::regclass
    )
    OR (
      index_class.relname = 'idx_article_discoveries_quarantine_keyset'
      AND index_state.indrelid = 'public.article_discoveries'::regclass
    )
  )
  AND (NOT index_state.indisvalid OR NOT index_state.indisready)
\gexec

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_articles_publisher_source_id
  ON public.articles (publisher_source_id)
  WHERE publisher_source_id IS NOT NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_article_discoveries_quarantine_keyset
  ON public.article_discoveries (discovered_at, id)
  WHERE promoted_article_id IS NULL;

CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS uq_articles_publisher_external_id
  ON public.articles (publisher_source_id, external_id)
  WHERE publisher_source_id IS NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_index idx
    JOIN pg_class cls ON cls.oid = idx.indexrelid
    JOIN pg_class target ON target.oid = idx.indrelid
    WHERE idx.indrelid = 'public.articles'::regclass
      AND cls.relnamespace = target.relnamespace
      AND cls.relname = 'idx_articles_publisher_source_id'
      AND idx.indisvalid AND idx.indisready
  ) THEN
    RAISE EXCEPTION 'index idx_articles_publisher_source_id is not valid and ready';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_index idx
    JOIN pg_class cls ON cls.oid = idx.indexrelid
    JOIN pg_class target ON target.oid = idx.indrelid
    WHERE idx.indrelid = 'public.article_discoveries'::regclass
      AND cls.relnamespace = target.relnamespace
      AND cls.relname = 'idx_article_discoveries_quarantine_keyset'
      AND idx.indisvalid AND idx.indisready
  ) THEN
    RAISE EXCEPTION 'index idx_article_discoveries_quarantine_keyset is not valid and ready';
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM pg_index idx
    JOIN pg_class cls ON cls.oid = idx.indexrelid
    JOIN pg_class target ON target.oid = idx.indrelid
    WHERE idx.indrelid = 'public.articles'::regclass
      AND cls.relnamespace = target.relnamespace
      AND cls.relname = 'uq_articles_publisher_external_id'
      AND idx.indisvalid AND idx.indisready
  ) THEN
    RAISE EXCEPTION 'index uq_articles_publisher_external_id is not valid and ready';
  END IF;
END $$;

CREATE OR REPLACE VIEW public.article_country_facts AS
SELECT
  article.id AS article_id,
  publisher.id,
  publisher.name,
  publisher.url,
  publisher.country_code,
  publisher.source_type,
  publisher.weight,
  publisher.language,
  publisher.tier,
  publisher.state_affiliated,
  publisher.propaganda_risk
FROM public.articles article
JOIN public.sources discovery ON discovery.id = article.source_id
JOIN public.sources publisher ON publisher.id = CASE
  WHEN COALESCE(discovery.config->>'feed_mode', 'publisher') = 'publisher_discovery'
    THEN article.publisher_source_id
  ELSE COALESCE(article.publisher_source_id, article.source_id)
END
WHERE article.geo_status IN (
  'source_verified', 'publisher_verified', 'publisher_reassigned'
)
AND (
  COALESCE(discovery.config->>'feed_mode', 'publisher') <> 'publisher_discovery'
  OR article.publisher_source_id IS NOT NULL
);
