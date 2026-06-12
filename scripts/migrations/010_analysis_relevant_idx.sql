-- Migration 010: partial index for /api/v2/headlines (relevant analysis ordered by action_level)
-- Run: psql $DATABASE_URL -f scripts/migrations/010_analysis_relevant_idx.sql

CREATE INDEX IF NOT EXISTS idx_analysis_relevant_al
    ON analysis (is_relevant, action_level DESC NULLS LAST, article_id)
    WHERE is_relevant = TRUE;
