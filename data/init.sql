-- Enable fuzzy matching
CREATE EXTENSION IF NOT EXISTS pg_trgm;

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
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE articles (
    id SERIAL PRIMARY KEY,
    source_id INTEGER REFERENCES sources(id),
    external_id VARCHAR(500),
    title TEXT,
    body TEXT,
    summary TEXT,
    url VARCHAR(1000),
    published_at TIMESTAMPTZ NOT NULL,
    collected_at TIMESTAMPTZ DEFAULT NOW(),
    language VARCHAR(5),
    title_normalized TEXT,
    is_duplicate BOOLEAN DEFAULT FALSE,
    duplicate_of INTEGER REFERENCES articles(id),
    reprint_count INTEGER DEFAULT 0,
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
    model_used VARCHAR(50),
    prompt_version VARCHAR(20),
    raw_response JSONB,
    analyzed_at TIMESTAMPTZ DEFAULT NOW()
);

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

CREATE INDEX idx_articles_source_published ON articles(source_id, published_at DESC);
CREATE INDEX idx_articles_published ON articles(published_at DESC);
CREATE INDEX idx_analysis_sentiment ON analysis(sentiment);
CREATE INDEX idx_temperature_country ON temperature(country_code, time DESC);
CREATE INDEX idx_alerts_country ON alerts(country_code, created_at DESC);
CREATE INDEX idx_articles_title_trgm ON articles USING gin (title_normalized gin_trgm_ops);
CREATE INDEX idx_articles_duplicate ON articles(is_duplicate);
