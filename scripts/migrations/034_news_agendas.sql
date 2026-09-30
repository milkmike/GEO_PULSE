-- Article-first news agendas. Separate from RRI relevance and confirmed risks.
CREATE TABLE IF NOT EXISTS news_agendas (
 id BIGSERIAL PRIMARY KEY,
 anchor_article_id INTEGER NOT NULL UNIQUE REFERENCES articles(id) ON DELETE CASCADE,
 model TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS news_agenda_articles (
 article_id INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
 agenda_id BIGINT NOT NULL REFERENCES news_agendas(id) ON DELETE CASCADE,
 relation TEXT NOT NULL CHECK (relation IN ('seed','same_event','development')),
 confidence DOUBLE PRECISION CHECK (confidence BETWEEN 0 AND 1),
 probabilities JSONB NOT NULL DEFAULT '{}',
 content_hash TEXT NOT NULL,
 anchor_hash TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS news_agenda_articles_group_idx ON news_agenda_articles(agenda_id);
CREATE TABLE IF NOT EXISTS news_agenda_decisions (
 cache_key TEXT PRIMARY KEY,
 anchor_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
 article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
 decision JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS news_agenda_runs (
 id BIGSERIAL PRIMARY KEY,
 status TEXT NOT NULL,
 stats JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
