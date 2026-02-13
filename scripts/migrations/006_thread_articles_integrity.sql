-- Migration 006: thread_articles integrity fix
-- Run: psql $DATABASE_URL -f scripts/migrations/006_thread_articles_integrity.sql

-- 1) Remove dangling links to missing rows
DELETE FROM thread_articles ta
WHERE NOT EXISTS (SELECT 1 FROM articles a WHERE a.id = ta.article_id)
   OR NOT EXISTS (SELECT 1 FROM threads t WHERE t.id = ta.thread_id);

-- 2) Recalculate thread article counters from actual links
UPDATE threads t
SET article_count = COALESCE(ta.cnt, 0)
FROM (
    SELECT thread_id, COUNT(*) AS cnt
    FROM thread_articles
    GROUP BY thread_id
) ta
WHERE t.id = ta.thread_id;

-- threads with no links
UPDATE threads t
SET article_count = 0
WHERE NOT EXISTS (SELECT 1 FROM thread_articles ta WHERE ta.thread_id = t.id);

-- 3) Add integrity constraints (idempotent style)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'thread_articles_thread_fk'
    ) THEN
        ALTER TABLE thread_articles
            ADD CONSTRAINT thread_articles_thread_fk
            FOREIGN KEY (thread_id) REFERENCES threads(id) ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'thread_articles_article_fk'
    ) THEN
        ALTER TABLE thread_articles
            ADD CONSTRAINT thread_articles_article_fk
            FOREIGN KEY (article_id) REFERENCES articles(id) ON DELETE CASCADE;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_thread_articles_thread_id ON thread_articles(thread_id);
CREATE INDEX IF NOT EXISTS idx_thread_articles_article_id ON thread_articles(article_id);
