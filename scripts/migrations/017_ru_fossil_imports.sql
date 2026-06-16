-- Migration 017: ru_fossil_imports (structural layer — CREA Russia Fossil Tracker)
-- Per-country cumulative imports of Russian fossil fuels (€ and tonnes) since the
-- 2022 invasion, with a per-commodity breakdown and a world rank by euros paid to
-- Russia. Mirrored into data/init.sql.
-- Source (free, no key): https://api.russiafossiltracker.com/v0/counter
-- Run: psql $DATABASE_URL -f scripts/migrations/017_ru_fossil_imports.sql

CREATE TABLE IF NOT EXISTS ru_fossil_imports (
    country_code VARCHAR(2) PRIMARY KEY,        -- ISO2 destination (importer)
    total_eur    DOUBLE PRECISION DEFAULT 0,    -- cumulative € paid to Russia
    total_tonne  DOUBLE PRECISION DEFAULT 0,    -- cumulative tonnes imported
    commodities  JSONB DEFAULT '[]',            -- [{group,name,value_eur,value_tonne}]
    world_rank   INTEGER,                       -- rank among importers by total_eur
    period_from  DATE,                          -- cumulation start (invasion window)
    updated_at   TIMESTAMP DEFAULT now()
);
