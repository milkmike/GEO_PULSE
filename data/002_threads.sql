-- Narrative Threads — migration 002
-- Run after init.sql

-- Ensure pg_trgm is available (should already exist from init.sql)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Таблица threads
CREATE TABLE IF NOT EXISTS threads (
    id SERIAL PRIMARY KEY,
    country_code CHAR(2) NOT NULL,
    thread_key VARCHAR(200) NOT NULL,  -- normalized event_key cluster
    title VARCHAR(500),                 -- human-readable title
    narrative TEXT,                      -- LLM-generated summary
    status VARCHAR(20) DEFAULT 'developing',  -- developing | resolved | dormant
    arc_phase VARCHAR(20) DEFAULT 'emerging', -- emerging | escalating | peak | cooling | resolved
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
    thread_id INTEGER REFERENCES threads(id) ON DELETE CASCADE,
    article_id INTEGER NOT NULL,
    PRIMARY KEY (thread_id, article_id)
);

CREATE INDEX IF NOT EXISTS idx_threads_country ON threads(country_code, importance_score DESC);
CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);
CREATE INDEX IF NOT EXISTS idx_threads_last_seen ON threads(last_seen DESC);
