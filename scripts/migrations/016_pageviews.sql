-- Migration 016: pageviews (self-hosted visitor counter, no cookies / no raw IP).
-- visitor_hash = sha256(ip + ua + daily_salt)[:16] — daily-unique, not cross-day
-- linkable. Mirrored into data/init.sql.
-- Run: psql $DATABASE_URL -f scripts/migrations/016_pageviews.sql

CREATE TABLE IF NOT EXISTS pageviews (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    day DATE NOT NULL DEFAULT CURRENT_DATE,
    path TEXT,
    visitor_hash CHAR(16),
    referrer_host TEXT
);

CREATE INDEX IF NOT EXISTS idx_pageviews_day ON pageviews(day);
