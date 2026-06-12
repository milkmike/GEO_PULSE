-- Migration 011: un_votes and trade_data tables (full registry structural layer)
-- Run: psql $DATABASE_URL -f scripts/migrations/011_un_votes_trade.sql

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
