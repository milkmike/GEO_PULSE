-- Auditable signal evidence and cached index-change explanations.

CREATE TABLE IF NOT EXISTS signal_evidence (
  id BIGSERIAL PRIMARY KEY,
  signal_id INTEGER UNIQUE NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
  detector VARCHAR(80) NOT NULL,
  detector_version VARCHAR(40) NOT NULL,
  threshold JSONB NOT NULL,
  observed JSONB NOT NULL,
  baseline JSONB NOT NULL,
  window_start TIMESTAMPTZ,
  window_end TIMESTAMPTZ,
  article_ids INTEGER[] NOT NULL DEFAULT '{}',
  story_ids BIGINT[] NOT NULL DEFAULT '{}',
  rri_points JSONB NOT NULL DEFAULT '[]',
  confidence NUMERIC(4,3) NOT NULL,
  completeness VARCHAR(20) NOT NULL CHECK (completeness IN ('complete','partial')),
  explanation JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_signal_evidence_created
  ON signal_evidence(created_at DESC);

CREATE TABLE IF NOT EXISTS index_change_explanations (
  id BIGSERIAL PRIMARY KEY,
  country_code CHAR(2) NOT NULL REFERENCES countries(code),
  from_time TIMESTAMPTZ NOT NULL,
  to_time TIMESTAMPTZ NOT NULL,
  rri_version VARCHAR(16) NOT NULL,
  input_hash CHAR(64) NOT NULL,
  exact_changes JSONB NOT NULL,
  estimated_contributions JSONB NOT NULL DEFAULT '[]',
  context JSONB NOT NULL DEFAULT '[]',
  evidence_completeness VARCHAR(20) NOT NULL,
  limitations JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(country_code, from_time, to_time, rri_version, input_hash)
);

CREATE INDEX IF NOT EXISTS idx_index_change_explanations_lookup
  ON index_change_explanations(country_code, to_time DESC, rri_version);
