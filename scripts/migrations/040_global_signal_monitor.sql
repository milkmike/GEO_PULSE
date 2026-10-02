-- Durable nomination is separate from public editorial release.
CREATE TABLE IF NOT EXISTS early_signal_work (
 id CHAR(64) PRIMARY KEY,
 source_key CHAR(64) NOT NULL,
 planner_version TEXT NOT NULL,
 article_id BIGINT NOT NULL,
 country_code TEXT,
 status TEXT NOT NULL CHECK (status IN
   ('needs_geography','needs_context','context_ready','drafting','needs_review','blocked','expired')),
 context JSONB,
 retrieval_links JSONB NOT NULL DEFAULT '[]'::jsonb,
 newest_published_at TIMESTAMPTZ NOT NULL,
 draft_id CHAR(64) REFERENCES early_signal_dossiers(id),
 last_error TEXT,
 attempted_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(source_key,planner_version)
);
CREATE INDEX IF NOT EXISTS early_signal_work_pending_idx
 ON early_signal_work(country_code,created_at,id) WHERE status='context_ready';
CREATE TABLE IF NOT EXISTS global_monitor_runs (
 id BIGSERIAL PRIMARY KEY,
 as_of TIMESTAMPTZ NOT NULL,
 payload JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS global_monitor_runs_latest_idx ON global_monitor_runs(as_of DESC,id DESC);
