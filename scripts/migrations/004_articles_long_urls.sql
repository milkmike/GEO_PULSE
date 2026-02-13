-- Migration 004: allow long aggregator URLs/external IDs in articles
-- Run: psql $DATABASE_URL -f scripts/migrations/004_articles_long_urls.sql

ALTER TABLE articles
    ALTER COLUMN external_id TYPE TEXT,
    ALTER COLUMN url TYPE TEXT;
