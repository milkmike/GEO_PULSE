-- Narrative Threads — migration 002.
-- Kept in sync with data/002_threads.sql for clean and legacy databases.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

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
