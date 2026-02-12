-- Create article_pairs table for audience split detection
CREATE TABLE IF NOT EXISTS article_pairs (
  id SERIAL PRIMARY KEY,
  article_id_1 INTEGER REFERENCES articles(id),
  article_id_2 INTEGER REFERENCES articles(id),
  source_id INTEGER REFERENCES sources(id),
  similarity NUMERIC(4,3),
  sentiment_delta NUMERIC(3,1),
  lang_1 VARCHAR(5),
  lang_2 VARCHAR(5),
  detected_at TIMESTAMPTZ DEFAULT NOW(),
  UNIQUE(article_id_1, article_id_2)
);
CREATE INDEX IF NOT EXISTS idx_pairs_source ON article_pairs(source_id);
CREATE INDEX IF NOT EXISTS idx_pairs_delta ON article_pairs(sentiment_delta DESC);
