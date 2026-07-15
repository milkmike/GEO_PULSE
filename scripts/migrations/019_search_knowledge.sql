-- Search, canonical knowledge, and embedding infrastructure.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE analysis ADD COLUMN IF NOT EXISTS embedding vector;

ALTER TABLE articles ADD COLUMN IF NOT EXISTS search_vector tsvector
GENERATED ALWAYS AS (
  setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
  setweight(to_tsvector('simple', coalesce(summary, '')), 'B') ||
  setweight(to_tsvector('simple', coalesce(body, '')), 'C')
) STORED;
CREATE INDEX IF NOT EXISTS idx_articles_search_vector ON articles USING gin(search_vector);
CREATE INDEX IF NOT EXISTS idx_articles_search_snapshot
  ON articles ((COALESCE(collected_at, published_at)) DESC, id DESC);

CREATE TABLE IF NOT EXISTS canonical_entities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind VARCHAR(24) NOT NULL CHECK (kind IN ('person','organization','location','event')),
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  labels JSONB NOT NULL DEFAULT '{}',
  country_codes TEXT[] NOT NULL DEFAULT '{}',
  provenance JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(kind, normalized_name)
);

CREATE TABLE IF NOT EXISTS entity_aliases (
  id BIGSERIAL PRIMARY KEY,
  entity_id UUID NOT NULL REFERENCES canonical_entities(id) ON DELETE CASCADE,
  alias TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  language VARCHAR(8),
  ambiguous BOOLEAN NOT NULL DEFAULT FALSE,
  provenance JSONB NOT NULL DEFAULT '{}',
  UNIQUE(entity_id, normalized_alias)
);

CREATE INDEX IF NOT EXISTS idx_entity_aliases_normalized
  ON entity_aliases(normalized_alias);
CREATE INDEX IF NOT EXISTS idx_entity_aliases_normalized_trgm
  ON entity_aliases USING gin(normalized_alias gin_trgm_ops);

CREATE TABLE IF NOT EXISTS article_entity_mentions (
  article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  entity_id UUID NOT NULL REFERENCES canonical_entities(id) ON DELETE CASCADE,
  mention_text TEXT,
  char_start INTEGER,
  char_end INTEGER,
  extractor VARCHAR(80) NOT NULL,
  extractor_version VARCHAR(40),
  confidence NUMERIC(4,3) NOT NULL DEFAULT 1.0,
  evidence JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(article_id, entity_id, extractor)
);

CREATE INDEX IF NOT EXISTS idx_article_entity_mentions_entity
  ON article_entity_mentions(entity_id, article_id);
CREATE INDEX IF NOT EXISTS idx_article_entity_mentions_article
  ON article_entity_mentions(article_id);

CREATE TABLE IF NOT EXISTS knowledge_edges (
  id BIGSERIAL PRIMARY KEY,
  source_node TEXT NOT NULL,
  target_node TEXT NOT NULL,
  relation VARCHAR(80) NOT NULL,
  confidence NUMERIC(4,3) NOT NULL,
  evidence JSONB NOT NULL DEFAULT '[]',
  valid_from TIMESTAMPTZ,
  valid_to TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(source_node, target_node, relation)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_edges_source
  ON knowledge_edges(source_node);
CREATE INDEX IF NOT EXISTS idx_knowledge_edges_target
  ON knowledge_edges(target_node);

CREATE TABLE IF NOT EXISTS embedding_profiles (
  id SERIAL PRIMARY KEY,
  profile_key VARCHAR(80) UNIQUE NOT NULL,
  provider VARCHAR(40) NOT NULL,
  model VARCHAR(120) NOT NULL,
  dimensions INTEGER NOT NULL CHECK (dimensions > 0),
  task VARCHAR(40) NOT NULL DEFAULT 'text-matching',
  version VARCHAR(40) NOT NULL,
  active BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS content_embeddings (
  id BIGSERIAL PRIMARY KEY,
  profile_id INTEGER NOT NULL REFERENCES embedding_profiles(id),
  object_type VARCHAR(24) NOT NULL CHECK (object_type IN ('article','entity','event','story')),
  object_id TEXT NOT NULL,
  content_hash CHAR(64) NOT NULL,
  embedding vector,
  status VARCHAR(20) NOT NULL DEFAULT 'ready',
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(profile_id, object_type, object_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_content_embeddings_object
  ON content_embeddings(object_type, object_id, profile_id);

CREATE TABLE IF NOT EXISTS embedding_jobs (
  id BIGSERIAL PRIMARY KEY,
  profile_id INTEGER NOT NULL REFERENCES embedding_profiles(id),
  object_type VARCHAR(24) NOT NULL,
  object_id TEXT NOT NULL,
  content_hash CHAR(64) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(profile_id, object_type, object_id, content_hash)
);

CREATE INDEX IF NOT EXISTS idx_embedding_jobs_pending
  ON embedding_jobs(profile_id, available_at, id)
  WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_embedding_jobs_processing_lease
  ON embedding_jobs(profile_id, updated_at, id)
  WHERE status = 'processing';
