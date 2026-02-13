-- Migration 007: remove stale threads with no linked articles
-- Run: psql $DATABASE_URL -f scripts/migrations/007_cleanup_zero_threads.sql

DELETE FROM threads
WHERE article_count <= 0
   OR id NOT IN (SELECT DISTINCT thread_id FROM thread_articles);
