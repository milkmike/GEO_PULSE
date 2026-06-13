-- Migration 015: per-source fetch health (why a source went silent).
-- The collector used to swallow fetch errors and return []; a source then
-- drifted to DEAD with no recorded reason. These columns record the outcome of
-- every fetch so /api/v2/health/sources can show WHY, and so transient failures
-- can be retried / chronic ones quarantined. Mirrored into data/init.sql.
-- Run: psql $DATABASE_URL -f scripts/migrations/015_source_fetch_health.sql

ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_fetch_at TIMESTAMPTZ;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_status VARCHAR(24);
ALTER TABLE sources ADD COLUMN IF NOT EXISTS last_error TEXT;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER DEFAULT 0;
