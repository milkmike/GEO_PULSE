-- Global story clusters and their membership, geography, entities, and events.

CREATE TABLE IF NOT EXISTS stories (
  id BIGSERIAL PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  title_ru TEXT NOT NULL,
  title_en TEXT,
  summary TEXT,
  lifecycle VARCHAR(20) NOT NULL CHECK (lifecycle IN ('emerging','developing','escalating','cooling','resolved')),
  first_seen TIMESTAMPTZ NOT NULL,
  last_seen TIMESTAMPTZ NOT NULL,
  article_count INTEGER NOT NULL DEFAULT 0,
  source_count INTEGER NOT NULL DEFAULT 0,
  country_count INTEGER NOT NULL DEFAULT 0,
  highest_action_level INTEGER NOT NULL DEFAULT 1,
  clustering_confidence NUMERIC(4,3) NOT NULL DEFAULT 0,
  summary_model VARCHAR(120),
  source_hash CHAR(64),
  meta JSONB NOT NULL DEFAULT '{}',
  generated_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_stories_lifecycle_last_seen
  ON stories(lifecycle, last_seen DESC);

CREATE TABLE IF NOT EXISTS story_articles (
  story_id BIGINT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  membership_confidence NUMERIC(4,3) NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}',
  added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(story_id, article_id)
);

CREATE INDEX IF NOT EXISTS idx_story_articles_membership
  ON story_articles(article_id, story_id);

CREATE TABLE IF NOT EXISTS story_countries (
  story_id BIGINT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  country_code CHAR(2) NOT NULL REFERENCES countries(code),
  article_count INTEGER NOT NULL DEFAULT 0,
  source_count INTEGER NOT NULL DEFAULT 0,
  media_tone NUMERIC(6,2),
  first_seen TIMESTAMPTZ,
  last_seen TIMESTAMPTZ,
  PRIMARY KEY(story_id, country_code)
);

CREATE INDEX IF NOT EXISTS idx_story_countries_country
  ON story_countries(country_code, last_seen DESC);

CREATE TABLE IF NOT EXISTS story_entities (
  story_id BIGINT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  entity_id UUID NOT NULL REFERENCES canonical_entities(id) ON DELETE CASCADE,
  mentions INTEGER NOT NULL DEFAULT 0,
  confidence NUMERIC(4,3) NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY(story_id, entity_id)
);

CREATE TABLE IF NOT EXISTS story_events (
  story_id BIGINT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  entity_id UUID NOT NULL REFERENCES canonical_entities(id) ON DELETE CASCADE,
  event_key TEXT NOT NULL,
  event_at TIMESTAMPTZ,
  action_level INTEGER NOT NULL DEFAULT 1,
  evidence JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY(story_id, entity_id)
);
