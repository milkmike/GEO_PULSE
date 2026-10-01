-- Versioned Jev screening of article source snapshots. Negative classifications
-- are stored too, so a source does not consume another paid screening call.
CREATE TABLE IF NOT EXISTS article_news_triage (
 article_id BIGINT PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
 source_title TEXT NOT NULL,
 source_excerpt TEXT NOT NULL,
 classification JSONB NOT NULL,
 model TEXT NOT NULL,
 version TEXT NOT NULL,
 classified_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
