-- Migration 018: ru_market_radar (financial "isolation radar" — single-row snapshot)
-- Composite of ruble strength (USD/RUB, CNY/RUB from our CBR fx_rates) and the
-- MOEX index (IMOEX via MOEX ISS API), with an indicative pressure verdict.
-- Mirrored into data/init.sql.
-- Run: psql $DATABASE_URL -f scripts/migrations/018_ru_market_radar.sql

CREATE TABLE IF NOT EXISTS ru_market_radar (
    id            INTEGER PRIMARY KEY DEFAULT 1,
    usd_rub       DOUBLE PRECISION,        -- rubles per USD (latest)
    usd_rub_chg30 DOUBLE PRECISION,        -- % change over ~30 days (+ = ruble weaker)
    cny_rub       DOUBLE PRECISION,        -- rubles per CNY (latest)
    cny_rub_chg30 DOUBLE PRECISION,
    moex          DOUBLE PRECISION,        -- IMOEX index level
    moex_chg30    DOUBLE PRECISION,        -- % change over ~30 days (- = market down)
    moex_spark    JSONB DEFAULT '[]',      -- recent closes [{d,v}, ...] for a sparkline
    pressure      INTEGER DEFAULT 0,       -- composite 0..2 (higher = more pressure on RF)
    verdict       TEXT,                    -- human label
    updated_at    TIMESTAMP DEFAULT now(),
    CONSTRAINT ru_market_radar_singleton CHECK (id = 1)
);
