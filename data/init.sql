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
    state_affiliated BOOLEAN DEFAULT FALSE,
    propaganda_risk VARCHAR(10) DEFAULT 'low',
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
