-- Editorial release is separate from the hypothesis' permanent needs_review status.
CREATE TABLE IF NOT EXISTS early_signal_dossiers (
 id CHAR(64) PRIMARY KEY,
 as_of TIMESTAMPTZ NOT NULL,
 horizon_date DATE NOT NULL,
 country_codes TEXT[] NOT NULL,
 source_snapshot_sha256 CHAR(64) NOT NULL,
 draft JSONB NOT NULL,
 evidence JSONB NOT NULL,
 release TEXT NOT NULL DEFAULT 'draft' CHECK (release IN ('draft','published')),
 review_note TEXT NOT NULL DEFAULT '',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 published_at TIMESTAMPTZ,
 CHECK (release <> 'published' OR length(btrim(review_note)) > 0)
);
CREATE INDEX IF NOT EXISTS early_signal_dossiers_published_idx
 ON early_signal_dossiers (as_of DESC, id) WHERE release='published';
CREATE INDEX IF NOT EXISTS early_signal_dossiers_country_idx
 ON early_signal_dossiers USING gin(country_codes);

CREATE TABLE IF NOT EXISTS early_signal_screenings (
 source_key CHAR(64) PRIMARY KEY,
 article_id BIGINT NOT NULL,
 snapshot_hash CHAR(64) NOT NULL,
 article JSONB NOT NULL,
 classification JSONB NOT NULL,
 screened_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS early_signal_screenings_article_idx
 ON early_signal_screenings(article_id);
