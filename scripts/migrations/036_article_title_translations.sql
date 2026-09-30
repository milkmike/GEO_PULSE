-- Cached presentation only: original publisher headlines remain authoritative.
CREATE TABLE IF NOT EXISTS article_title_translations (
 article_id BIGINT PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
 source_title TEXT NOT NULL,
 title_ru TEXT NOT NULL CHECK (char_length(title_ru) BETWEEN 1 AND 500),
 model TEXT NOT NULL,
 translated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
