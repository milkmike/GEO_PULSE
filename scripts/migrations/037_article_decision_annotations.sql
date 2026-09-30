-- Source-grounded, invalidatable decision annotations. Original articles are immutable here.
CREATE TABLE IF NOT EXISTS article_decision_annotations (
 article_id BIGINT PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
 source_title TEXT NOT NULL,
 source_excerpt TEXT NOT NULL,
 annotation JSONB NOT NULL,
 model TEXT NOT NULL,
 version TEXT NOT NULL,
 analyzed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
