-- 008: World expansion — global country registry, Russia Relations Index,
--      GDELT observations, signal intelligence, AI briefs, topic taxonomy.

-- Global country registry (seeded from src/countries.py at worker startup)
CREATE TABLE IF NOT EXISTS countries (
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

-- Russia Relations Index (RRI v1): structural baseline + media layer
CREATE TABLE IF NOT EXISTS ru_index (
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
CREATE INDEX IF NOT EXISTS idx_ru_index_country ON ru_index(country_code, time DESC);

-- Daily GDELT observations: media of country X about Russia (volume + tone)
CREATE TABLE IF NOT EXISTS gdelt_daily (
    day DATE NOT NULL,
    country_code CHAR(2) NOT NULL,
    volume DECIMAL(12,2),
    volume_share DECIMAL(10,6),
    tone_avg DECIMAL(6,2),
    article_samples JSONB,
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (day, country_code)
);
CREATE INDEX IF NOT EXISTS idx_gdelt_daily_country ON gdelt_daily(country_code, day DESC);

-- Signal intelligence: convergence, silence, velocity, tone shifts, index moves
CREATE TABLE IF NOT EXISTS signals (
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
CREATE INDEX IF NOT EXISTS idx_signals_active ON signals(expires_at DESC, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_country ON signals(country_code, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_dedup ON signals(dedup_key, created_at DESC);

-- AI briefs: world brief + per-country dossier briefs
CREATE TABLE IF NOT EXISTS briefs (
    id SERIAL PRIMARY KEY,
    scope VARCHAR(10) NOT NULL DEFAULT 'world',
    content TEXT NOT NULL,
    model VARCHAR(80),
    source_hash VARCHAR(64),
    meta JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_briefs_scope ON briefs(scope, created_at DESC);

-- Topic taxonomy breakdown on analysis (multi-label, prompt v2.0+)
ALTER TABLE analysis ADD COLUMN IF NOT EXISTS topics TEXT[];
CREATE INDEX IF NOT EXISTS idx_analysis_topics ON analysis USING gin(topics);
