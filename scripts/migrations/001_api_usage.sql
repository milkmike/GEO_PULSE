-- Migration: Create api_usage table for API call tracking
-- Run: psql $DATABASE_URL -f scripts/migrations/001_api_usage.sql

CREATE TABLE IF NOT EXISTS api_usage (
    id SERIAL PRIMARY KEY,
    service VARCHAR(50) NOT NULL,          -- openrouter, jina, comtrade, openai
    endpoint VARCHAR(255),
    model VARCHAR(100),
    script VARCHAR(100),                   -- analyze.py, build_threads.py, etc.
    tokens_in INTEGER DEFAULT 0,
    tokens_out INTEGER DEFAULT 0,
    cost_usd NUMERIC(10,6) DEFAULT 0,
    status VARCHAR(20) DEFAULT 'ok',       -- ok, error, timeout
    error_message TEXT,
    duration_ms INTEGER,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_usage_service ON api_usage(service);
CREATE INDEX IF NOT EXISTS idx_api_usage_created ON api_usage(created_at);
CREATE INDEX IF NOT EXISTS idx_api_usage_script ON api_usage(script);
