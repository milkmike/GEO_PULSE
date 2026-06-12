-- Migration 012: source trust flags (state affiliation + propaganda risk)
-- Run: psql $DATABASE_URL -f scripts/migrations/012_source_trust_flags.sql

ALTER TABLE sources ADD COLUMN IF NOT EXISTS state_affiliated BOOLEAN DEFAULT FALSE;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS propaganda_risk VARCHAR(10) DEFAULT 'low';
