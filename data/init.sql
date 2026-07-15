-- Enable fuzzy matching
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- CIS Thermometer — DB Schema

CREATE TABLE sources (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    url VARCHAR(500) NOT NULL,
    country_code CHAR(2) NOT NULL,
    source_type VARCHAR(20) NOT NULL,
    weight DECIMAL(3,2) DEFAULT 1.0,
    language VARCHAR(5) DEFAULT 'ru',
    config JSONB DEFAULT '{}',
    active BOOLEAN DEFAULT TRUE,
    tier VARCHAR(20) DEFAULT 'mainstream',
    state_affiliated BOOLEAN DEFAULT FALSE,
    propaganda_risk VARCHAR(10) DEFAULT 'low',
    last_fetch_at TIMESTAMPTZ,
    last_status VARCHAR(24),
    last_error TEXT,
    consecutive_failures INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE articles (
    id SERIAL PRIMARY KEY,
    source_id INTEGER REFERENCES sources(id),
    external_id TEXT,
    title TEXT,
    body TEXT,
    summary TEXT,
    url TEXT,
    published_at TIMESTAMPTZ NOT NULL,
    collected_at TIMESTAMPTZ DEFAULT NOW(),
    language VARCHAR(5),
    title_normalized TEXT,
    is_duplicate BOOLEAN DEFAULT FALSE,
    duplicate_of INTEGER REFERENCES articles(id),
    reprint_count INTEGER DEFAULT 0,
    is_backfill BOOLEAN DEFAULT FALSE,
    UNIQUE(source_id, external_id)
);

CREATE TABLE analysis (
    id SERIAL PRIMARY KEY,
    article_id INTEGER REFERENCES articles(id) UNIQUE,
    is_relevant BOOLEAN,
    relevance_score DECIMAL(3,2),
    sentiment DECIMAL(3,1),
    sentiment_confidence DECIMAL(3,2),
    event_type VARCHAR(20),
    event_key VARCHAR(200),
    action_level INTEGER DEFAULT 1,
    entities JSONB,
    model_used VARCHAR(50),
    prompt_version VARCHAR(20),
    raw_response JSONB,
    analyzed_at TIMESTAMPTZ DEFAULT NOW()
);

-- === Narrative threads (see data/002_threads.sql and migration 002) ===

CREATE TABLE IF NOT EXISTS threads (
    id SERIAL PRIMARY KEY,
    country_code CHAR(2) NOT NULL,
    thread_key VARCHAR(200) NOT NULL,
    title VARCHAR(500),
    narrative TEXT,
    status VARCHAR(20) DEFAULT 'developing',
    arc_phase VARCHAR(20) DEFAULT 'emerging',
    first_seen TIMESTAMPTZ,
    last_seen TIMESTAMPTZ,
    article_count INTEGER DEFAULT 0,
    avg_sentiment DECIMAL(4,2),
    max_action_level INTEGER DEFAULT 1,
    importance_score DECIMAL(5,2) DEFAULT 0,
    generated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(country_code, thread_key)
);

CREATE TABLE IF NOT EXISTS thread_articles (
    thread_id INTEGER NOT NULL,
    article_id INTEGER NOT NULL,
    CONSTRAINT thread_articles_thread_fk
        FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE,
    CONSTRAINT thread_articles_article_fk
        FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE,
    PRIMARY KEY (thread_id, article_id)
);

CREATE INDEX IF NOT EXISTS idx_threads_country
    ON threads(country_code, importance_score DESC);
CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);
CREATE INDEX IF NOT EXISTS idx_threads_last_seen ON threads(last_seen DESC);

CREATE TABLE temperature (
    time TIMESTAMPTZ NOT NULL,
    country_code CHAR(2) NOT NULL,
    temperature DECIMAL(5,2),
    raw_sentiment DECIMAL(4,2),
    diplomatic DECIMAL(4,2),
    military DECIMAL(4,2),
    economic DECIMAL(4,2),
    cultural DECIMAL(4,2),
    security DECIMAL(4,2),
    article_count INTEGER,
    source_count INTEGER,
    trend VARCHAR(10),
    anomaly_score DECIMAL(4,2),
    pattern_type VARCHAR(20),
    PRIMARY KEY (time, country_code)
);

SELECT create_hypertable('temperature', 'time');

CREATE TABLE reference_events (
    id SERIAL PRIMARY KEY,
    country_code CHAR(2) NOT NULL,
    event_date DATE NOT NULL,
    title VARCHAR(500),
    description TEXT,
    event_type VARCHAR(20),
    expected_sentiment DECIMAL(3,1),
    actual_sentiment DECIMAL(3,1),
    source_url VARCHAR(1000)
);

CREATE TABLE alerts (
    id SERIAL PRIMARY KEY,
    country_code CHAR(2) NOT NULL,
    alert_type VARCHAR(30),
    severity VARCHAR(10),
    title VARCHAR(500),
    description TEXT,
    data JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    acknowledged BOOLEAN DEFAULT FALSE
);

-- === World expansion (see scripts/migrations/008_world_expansion.sql) ===

CREATE TABLE countries (
    code CHAR(2) PRIMARY KEY,
    name_ru VARCHAR(100) NOT NULL,
    name_en VARCHAR(100) NOT NULL,
    iso3 CHAR(3) NOT NULL,
    fips CHAR(2),
    flag VARCHAR(8),
    region VARCHAR(30) NOT NULL,
    tier SMALLINT NOT NULL DEFAULT 2,
    memberships TEXT[] DEFAULT '{}',
    unfriendly BOOLEAN DEFAULT FALSE,
    sanctions_on_russia BOOLEAN DEFAULT FALSE,
    war_with_russia BOOLEAN DEFAULT FALSE,
    baseline_adj SMALLINT DEFAULT 0,
    baseline_note TEXT,
    active BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE ru_index (
    time TIMESTAMPTZ NOT NULL,
    country_code CHAR(2) NOT NULL,
    score DECIMAL(6,2) NOT NULL,
    structural DECIMAL(6,2),
    media DECIMAL(6,2),
    boost DECIMAL(6,2),
    level VARCHAR(12),
    delta_24h DECIMAL(6,2),
    delta_7d DECIMAL(6,2),
    article_count INTEGER,
    gdelt_volume DECIMAL(12,2),
    gdelt_tone DECIMAL(6,2),
    version VARCHAR(8) DEFAULT 'v1',
    details JSONB,
    PRIMARY KEY (time, country_code)
);

CREATE TABLE gdelt_daily (
    day DATE NOT NULL,
    country_code CHAR(2) NOT NULL,
    volume DECIMAL(12,2),
    volume_share DECIMAL(10,6),
    tone_avg DECIMAL(6,2),
    article_samples JSONB,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (day, country_code)
);

CREATE TABLE signals (
    id SERIAL PRIMARY KEY,
    signal_type VARCHAR(30) NOT NULL,
    country_code CHAR(2),
    severity VARCHAR(10) DEFAULT 'info',
    confidence DECIMAL(3,2) DEFAULT 0.70,
    title VARCHAR(500),
    description TEXT,
    payload JSONB,
    dedup_key VARCHAR(200) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ
);

CREATE TABLE briefs (
    id SERIAL PRIMARY KEY,
    scope VARCHAR(40) NOT NULL DEFAULT 'world',
    content TEXT NOT NULL,
    model VARCHAR(80),
    source_hash VARCHAR(64),
    meta JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE analysis ADD COLUMN topics TEXT[];

CREATE TABLE fx_rates (
    day DATE NOT NULL,
    currency CHAR(3) NOT NULL,
    rate_to_rub DECIMAL(14,6) NOT NULL,
    change_1d_pct DECIMAL(8,4),
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (day, currency)
);
CREATE INDEX idx_fx_rates_currency ON fx_rates(currency, day DESC);
CREATE INDEX idx_analysis_entities ON analysis USING gin(entities jsonb_path_ops);

CREATE INDEX idx_ru_index_country ON ru_index(country_code, time DESC);
CREATE INDEX idx_gdelt_daily_country ON gdelt_daily(country_code, day DESC);
CREATE INDEX idx_signals_active ON signals(expires_at DESC, created_at DESC);
CREATE INDEX idx_signals_country ON signals(country_code, created_at DESC);
CREATE INDEX idx_signals_dedup ON signals(dedup_key, created_at DESC);
CREATE INDEX idx_briefs_scope ON briefs(scope, created_at DESC);
CREATE INDEX idx_analysis_topics ON analysis USING gin(topics);

CREATE INDEX idx_articles_source_published ON articles(source_id, published_at DESC);
CREATE INDEX idx_articles_published ON articles(published_at DESC);
CREATE INDEX idx_analysis_sentiment ON analysis(sentiment);
CREATE INDEX IF NOT EXISTS idx_analysis_relevant_al
    ON analysis (is_relevant, action_level DESC NULLS LAST, article_id)
    WHERE is_relevant = TRUE;
CREATE INDEX idx_temperature_country ON temperature(country_code, time DESC);
CREATE INDEX idx_alerts_country ON alerts(country_code, created_at DESC);
CREATE INDEX idx_articles_title_trgm ON articles USING gin (title_normalized gin_trgm_ops);
CREATE INDEX idx_articles_duplicate ON articles(is_duplicate);

-- === Structural data layer (see scripts/migrations/011_un_votes_trade.sql) ===

CREATE TABLE IF NOT EXISTS un_votes (
    id SERIAL PRIMARY KEY,
    country_code VARCHAR(2) NOT NULL,
    year INTEGER NOT NULL,
    total_votes INTEGER DEFAULT 0,
    agree_with_russia INTEGER DEFAULT 0,
    disagree_with_russia INTEGER DEFAULT 0,
    abstain INTEGER DEFAULT 0,
    agreement_pct DOUBLE PRECISION,
    updated_at TIMESTAMP DEFAULT now(),
    UNIQUE (country_code, year)
);

CREATE TABLE IF NOT EXISTS trade_data (
    id SERIAL PRIMARY KEY,
    country_code VARCHAR(2) NOT NULL,
    year INTEGER NOT NULL,
    ru_export_usd BIGINT DEFAULT 0,
    ru_import_usd BIGINT DEFAULT 0,
    total_trade_usd BIGINT DEFAULT 0,
    trade_balance_usd BIGINT DEFAULT 0,
    yoy_change_pct DOUBLE PRECISION,
    updated_at TIMESTAMP DEFAULT now(),
    UNIQUE (country_code, year)
);

-- Sanctions pressure per jurisdiction (see scripts/migrations/014_sanctions.sql)
CREATE TABLE IF NOT EXISTS sanctions_pressure (
    id SERIAL PRIMARY KEY,
    country_code VARCHAR(2) NOT NULL,
    lists_count INTEGER DEFAULT 0,
    target_count INTEGER DEFAULT 0,
    prev_target_count INTEGER DEFAULT 0,
    delta INTEGER DEFAULT 0,
    last_change DATE,
    programs JSONB DEFAULT '[]',
    updated_at TIMESTAMP DEFAULT now(),
    UNIQUE (country_code)
);

-- Pageviews (self-hosted visitor counter, see scripts/migrations/016_pageviews.sql)
CREATE TABLE IF NOT EXISTS pageviews (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    day DATE NOT NULL DEFAULT CURRENT_DATE,
    path TEXT,
    visitor_hash CHAR(16),
    referrer_host TEXT
);
CREATE INDEX IF NOT EXISTS idx_pageviews_day ON pageviews(day);

-- Russian fossil-fuel imports per country (see scripts/migrations/017_ru_fossil_imports.sql)
CREATE TABLE IF NOT EXISTS ru_fossil_imports (
    country_code VARCHAR(2) PRIMARY KEY,
    total_eur    DOUBLE PRECISION DEFAULT 0,
    total_tonne  DOUBLE PRECISION DEFAULT 0,
    commodities  JSONB DEFAULT '[]',
    world_rank   INTEGER,
    period_from  DATE,
    updated_at   TIMESTAMP DEFAULT now()
);

-- Financial "isolation radar" snapshot (see scripts/migrations/018_ru_market_radar.sql)
CREATE TABLE IF NOT EXISTS ru_market_radar (
    id            INTEGER PRIMARY KEY DEFAULT 1,
    usd_rub       DOUBLE PRECISION,
    usd_rub_chg30 DOUBLE PRECISION,
    cny_rub       DOUBLE PRECISION,
    cny_rub_chg30 DOUBLE PRECISION,
    moex          DOUBLE PRECISION,
    moex_chg30    DOUBLE PRECISION,
    moex_spark    JSONB DEFAULT '[]',
    pressure      INTEGER DEFAULT 0,
    verdict       TEXT,
    updated_at    TIMESTAMP DEFAULT now(),
    CONSTRAINT ru_market_radar_singleton CHECK (id = 1)
);

-- === Search and canonical knowledge (see scripts/migrations/019_search_knowledge.sql) ===

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

-- === Global stories (see scripts/migrations/020_global_stories.sql) ===

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

-- === Evidence and explanations (see scripts/migrations/021_signal_evidence_explanations.sql) ===

CREATE TABLE IF NOT EXISTS signal_evidence (
  id BIGSERIAL PRIMARY KEY,
  signal_id INTEGER UNIQUE NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
  detector VARCHAR(80) NOT NULL,
  detector_version VARCHAR(40) NOT NULL,
  threshold JSONB NOT NULL,
  observed JSONB NOT NULL,
  baseline JSONB NOT NULL,
  window_start TIMESTAMPTZ,
  window_end TIMESTAMPTZ,
  article_ids INTEGER[] NOT NULL DEFAULT '{}',
  story_ids BIGINT[] NOT NULL DEFAULT '{}',
  rri_points JSONB NOT NULL DEFAULT '[]',
  confidence NUMERIC(4,3) NOT NULL,
  completeness VARCHAR(20) NOT NULL CHECK (completeness IN ('complete','partial')),
  explanation JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_signal_evidence_created
  ON signal_evidence(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signal_evidence_story_ids_gin
  ON signal_evidence USING GIN (story_ids);
CREATE INDEX IF NOT EXISTS idx_signal_evidence_article_ids_gin
  ON signal_evidence USING GIN (article_ids);

CREATE TABLE IF NOT EXISTS index_change_explanations (
  id BIGSERIAL PRIMARY KEY,
  country_code CHAR(2) NOT NULL REFERENCES countries(code),
  from_time TIMESTAMPTZ NOT NULL,
  to_time TIMESTAMPTZ NOT NULL,
  rri_version VARCHAR(16) NOT NULL,
  input_hash CHAR(64) NOT NULL,
  exact_changes JSONB NOT NULL,
  estimated_contributions JSONB NOT NULL DEFAULT '[]',
  context JSONB NOT NULL DEFAULT '[]',
  evidence_completeness VARCHAR(20) NOT NULL,
  limitations JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(country_code, from_time, to_time, rri_version, input_hash)
);
CREATE INDEX IF NOT EXISTS idx_index_change_explanations_lookup
  ON index_change_explanations(country_code, to_time DESC, rri_version);
