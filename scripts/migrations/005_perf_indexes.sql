-- Migration 005: performance indexes for countries/threads dashboards
-- Run: psql $DATABASE_URL -f scripts/migrations/005_perf_indexes.sql

CREATE INDEX IF NOT EXISTS idx_sources_country_code
    ON sources(country_code);

CREATE INDEX IF NOT EXISTS idx_threads_country_status_importance
    ON threads(country_code, status, importance_score DESC);

CREATE INDEX IF NOT EXISTS idx_threads_status_importance
    ON threads(status, importance_score DESC);

CREATE INDEX IF NOT EXISTS idx_threads_country_importance
    ON threads(country_code, importance_score DESC);
