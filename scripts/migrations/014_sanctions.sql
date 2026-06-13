-- Migration 014: sanctions_pressure (structural layer — OpenSanctions catalog)
-- Per-jurisdiction sanctions activity, mirrored into data/init.sql.
-- Run: psql $DATABASE_URL -f scripts/migrations/014_sanctions.sql

CREATE TABLE IF NOT EXISTS sanctions_pressure (
    id SERIAL PRIMARY KEY,
    country_code VARCHAR(2) NOT NULL,      -- sanctioning jurisdiction (ISO2; 'EU' kept as-is)
    lists_count INTEGER DEFAULT 0,         -- number of sanctions lists this jurisdiction publishes
    target_count INTEGER DEFAULT 0,        -- sanctioned targets across those lists
    prev_target_count INTEGER DEFAULT 0,   -- previous snapshot (for delta/escalation signal)
    delta INTEGER DEFAULT 0,               -- target_count - prev_target_count
    last_change DATE,                      -- newest list update among this jurisdiction's lists
    programs JSONB DEFAULT '[]',           -- [{name,title,targets,last_change}, ...]
    updated_at TIMESTAMP DEFAULT now(),
    UNIQUE (country_code)
);
