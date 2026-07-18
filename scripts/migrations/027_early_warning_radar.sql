-- Additive, replayable Early Warning Radar storage. Public UUIDs are assigned
-- by the application; record IDs remain internal BIGSERIAL keys.

CREATE TABLE IF NOT EXISTS radar_observations (
  id BIGSERIAL PRIMARY KEY,
  public_id UUID UNIQUE NOT NULL,
  input_hash CHAR(64) UNIQUE NOT NULL,
  country_code CHAR(2) NOT NULL REFERENCES countries(code) ON DELETE RESTRICT,
  contour VARCHAR(16) NOT NULL CHECK (contour IN ('media','action')),
  subject_key TEXT NOT NULL,
  direction VARCHAR(24) NOT NULL,
  metric VARCHAR(64) NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  window_start TIMESTAMPTZ,
  window_end TIMESTAMPTZ,
  value NUMERIC(16,4),
  publisher_family_count INTEGER NOT NULL DEFAULT 0 CHECK (publisher_family_count >= 0),
  source_count INTEGER NOT NULL DEFAULT 0 CHECK (source_count >= 0),
  coverage_confidence NUMERIC(5,4) NOT NULL DEFAULT 0
    CHECK (coverage_confidence BETWEEN 0 AND 1),
  authority VARCHAR(32),
  article_id INTEGER REFERENCES articles(id) ON DELETE RESTRICT,
  story_id BIGINT REFERENCES stories(id) ON DELETE RESTRICT,
  signal_id INTEGER REFERENCES signals(id) ON DELETE RESTRICT,
  canonical_entity_id UUID REFERENCES canonical_entities(id) ON DELETE RESTRICT,
  baseline JSONB NOT NULL DEFAULT '{}',
  evidence JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (window_start IS NULL OR window_end IS NULL OR window_start <= window_end)
);
CREATE INDEX IF NOT EXISTS idx_radar_observations_country_time
  ON radar_observations(country_code, observed_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_radar_observations_subject_time
  ON radar_observations(subject_key, observed_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS action_events (
  id BIGSERIAL PRIMARY KEY,
  public_id UUID UNIQUE NOT NULL,
  input_hash CHAR(64) UNIQUE NOT NULL,
  country_code CHAR(2) NOT NULL REFERENCES countries(code) ON DELETE RESTRICT,
  subject_key TEXT NOT NULL,
  direction VARCHAR(24) NOT NULL,
  action_type VARCHAR(64) NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'reported'
    CHECK (status IN ('reported','verified','withdrawn','superseded')),
  authority VARCHAR(32) NOT NULL,
  effective_at TIMESTAMPTZ NOT NULL,
  announced_at TIMESTAMPTZ,
  article_id INTEGER REFERENCES articles(id) ON DELETE RESTRICT,
  story_id BIGINT REFERENCES stories(id) ON DELETE RESTRICT,
  signal_id INTEGER REFERENCES signals(id) ON DELETE RESTRICT,
  canonical_entity_id UUID REFERENCES canonical_entities(id) ON DELETE RESTRICT,
  details JSONB NOT NULL DEFAULT '{}',
  evidence JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_action_events_country_time
  ON action_events(country_code, effective_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_action_events_subject_time
  ON action_events(subject_key, effective_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS radar_trends (
  id BIGSERIAL PRIMARY KEY,
  public_id UUID UNIQUE NOT NULL,
  scope VARCHAR(16) NOT NULL CHECK (scope IN ('country','meta')),
  contour VARCHAR(16) CHECK (contour IN ('media','action')),
  country_code CHAR(2) REFERENCES countries(code),
  subject_key TEXT NOT NULL,
  title_ru TEXT NOT NULL,
  direction VARCHAR(24) NOT NULL,
  wave_key TEXT,
  state VARCHAR(16) NOT NULL CHECK (state IN
    ('candidate','emerging','confirmed','cooling','resolved','rejected')),
  confidence NUMERIC(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  coverage_confidence NUMERIC(5,4) NOT NULL CHECK (coverage_confidence BETWEEN 0 AND 1),
  velocity NUMERIC(12,4) NOT NULL DEFAULT 0,
  first_observed_at TIMESTAMPTZ NOT NULL,
  detected_at TIMESTAMPTZ,
  confirmed_at TIMESTAMPTZ,
  t0_auto TIMESTAMPTZ,
  t0_effective TIMESTAMPTZ,
  detector_version VARCHAR(40) NOT NULL,
  baseline JSONB NOT NULL DEFAULT '{}',
  explanation JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT radar_trends_scope_shape_check CHECK (
    (scope = 'country' AND contour IS NOT NULL AND country_code IS NOT NULL
      AND wave_key IS NOT NULL)
    OR (scope = 'meta' AND contour IS NULL AND country_code IS NULL
      AND wave_key IS NULL)
  )
);
ALTER TABLE radar_trends
  ADD COLUMN IF NOT EXISTS wave_key TEXT;
UPDATE radar_trends
SET wave_key = 'legacy:' || id::text
WHERE scope = 'country' AND wave_key IS NULL;
ALTER TABLE radar_trends
  DROP CONSTRAINT IF EXISTS radar_trends_scope_shape_check;
ALTER TABLE radar_trends
  ADD CONSTRAINT radar_trends_scope_shape_check CHECK (
    (scope = 'country' AND contour IS NOT NULL AND country_code IS NOT NULL
      AND wave_key IS NOT NULL)
    OR (scope = 'meta' AND contour IS NULL AND country_code IS NULL
      AND wave_key IS NULL)
  );
DROP INDEX IF EXISTS uq_radar_country_trend_identity;
CREATE UNIQUE INDEX IF NOT EXISTS uq_radar_country_trend_identity
  ON radar_trends(contour, country_code, subject_key, direction, wave_key, detector_version)
  WHERE scope = 'country';
CREATE UNIQUE INDEX IF NOT EXISTS uq_radar_meta_trend_identity
  ON radar_trends(subject_key, direction, detector_version)
  WHERE scope = 'meta';
CREATE INDEX IF NOT EXISTS idx_radar_trends_country_state_time
  ON radar_trends(country_code, state, first_observed_at DESC, id DESC)
  WHERE scope = 'country';
CREATE INDEX IF NOT EXISTS idx_radar_trends_meta_state_time
  ON radar_trends(state, first_observed_at DESC, id DESC)
  WHERE scope = 'meta';

CREATE OR REPLACE FUNCTION public.reject_radar_trend_identity_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF OLD.scope IS DISTINCT FROM NEW.scope
     OR OLD.contour IS DISTINCT FROM NEW.contour
     OR OLD.country_code IS DISTINCT FROM NEW.country_code
     OR OLD.subject_key IS DISTINCT FROM NEW.subject_key
     OR OLD.direction IS DISTINCT FROM NEW.direction
     OR OLD.wave_key IS DISTINCT FROM NEW.wave_key
     OR OLD.detector_version IS DISTINCT FROM NEW.detector_version THEN
    RAISE EXCEPTION 'radar trend identity fields are immutable'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS radar_trend_identity_immutable ON radar_trends;
CREATE TRIGGER radar_trend_identity_immutable
  BEFORE UPDATE ON radar_trends
  FOR EACH ROW EXECUTE FUNCTION public.reject_radar_trend_identity_mutation();

CREATE TABLE IF NOT EXISTS radar_trend_members (
  id BIGSERIAL PRIMARY KEY,
  public_id UUID UNIQUE NOT NULL,
  meta_trend_id BIGINT NOT NULL REFERENCES radar_trends(id) ON DELETE RESTRICT,
  country_trend_id BIGINT NOT NULL REFERENCES radar_trends(id) ON DELETE RESTRICT,
  joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  left_at TIMESTAMPTZ,
  evidence JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(meta_trend_id, country_trend_id),
  CHECK (meta_trend_id <> country_trend_id),
  CHECK (left_at IS NULL OR left_at >= joined_at)
);
CREATE INDEX IF NOT EXISTS idx_radar_trend_members_country
  ON radar_trend_members(country_trend_id, joined_at DESC, id DESC);

CREATE OR REPLACE FUNCTION public.validate_radar_trend_member_topology()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  referenced_meta_scope VARCHAR(16);
  referenced_country_scope VARCHAR(16);
BEGIN
  SELECT scope INTO referenced_meta_scope
  FROM public.radar_trends
  WHERE id = NEW.meta_trend_id;

  SELECT scope INTO referenced_country_scope
  FROM public.radar_trends
  WHERE id = NEW.country_trend_id;

  IF referenced_meta_scope IS DISTINCT FROM 'meta'
     OR referenced_country_scope IS DISTINCT FROM 'country' THEN
    RAISE EXCEPTION
      'radar trend member requires meta_trend_id scope=meta and country_trend_id scope=country'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS radar_trend_members_topology ON radar_trend_members;
CREATE TRIGGER radar_trend_members_topology
  BEFORE INSERT OR UPDATE ON radar_trend_members
  FOR EACH ROW EXECUTE FUNCTION public.validate_radar_trend_member_topology();

CREATE TABLE IF NOT EXISTS radar_trend_evidence (
  id BIGSERIAL PRIMARY KEY,
  public_id UUID UNIQUE NOT NULL,
  trend_id BIGINT NOT NULL REFERENCES radar_trends(id) ON DELETE RESTRICT,
  observation_id BIGINT REFERENCES radar_observations(id) ON DELETE RESTRICT,
  action_event_id BIGINT REFERENCES action_events(id) ON DELETE RESTRICT,
  article_id INTEGER REFERENCES articles(id) ON DELETE RESTRICT,
  story_id BIGINT REFERENCES stories(id) ON DELETE RESTRICT,
  signal_id INTEGER REFERENCES signals(id) ON DELETE RESTRICT,
  canonical_entity_id UUID REFERENCES canonical_entities(id) ON DELETE RESTRICT,
  role VARCHAR(16) NOT NULL CHECK (role IN
    ('trigger','support','context','contradiction')),
  contribution NUMERIC(6,5) NOT NULL DEFAULT 0 CHECK (contribution BETWEEN -1 AND 1),
  evidence JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (
    observation_id IS NOT NULL OR action_event_id IS NOT NULL OR article_id IS NOT NULL
    OR story_id IS NOT NULL OR signal_id IS NOT NULL OR canonical_entity_id IS NOT NULL
  )
);
CREATE INDEX IF NOT EXISTS idx_radar_trend_evidence_trend_role
  ON radar_trend_evidence(trend_id, role, id);

-- These records intentionally model immutable historical decisions.
CREATE TABLE IF NOT EXISTS radar_state_events (
  id BIGSERIAL PRIMARY KEY,
  public_id UUID UNIQUE NOT NULL,
  trend_id BIGINT NOT NULL REFERENCES radar_trends(id) ON DELETE RESTRICT,
  from_state VARCHAR(16) CHECK (from_state IS NULL OR from_state IN
    ('candidate','emerging','confirmed','cooling','resolved','rejected')),
  to_state VARCHAR(16) NOT NULL CHECK (to_state IN
    ('candidate','emerging','confirmed','cooling','resolved','rejected')),
  transition_reason VARCHAR(80) NOT NULL,
  metrics JSONB NOT NULL DEFAULT '{}',
  evidence JSONB NOT NULL DEFAULT '{}',
  occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (from_state IS NULL OR from_state <> to_state)
);
CREATE INDEX IF NOT EXISTS idx_radar_state_events_trend_time
  ON radar_state_events(trend_id, occurred_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS radar_t0_revisions (
  id BIGSERIAL PRIMARY KEY,
  public_id UUID UNIQUE NOT NULL,
  trend_id BIGINT NOT NULL REFERENCES radar_trends(id) ON DELETE RESTRICT,
  previous_t0 TIMESTAMPTZ,
  revised_t0 TIMESTAMPTZ NOT NULL,
  revision_kind VARCHAR(16) NOT NULL CHECK (revision_kind IN ('automatic','analyst')),
  reason TEXT NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}',
  revised_by TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_radar_t0_revisions_trend_time
  ON radar_t0_revisions(trend_id, created_at DESC, id DESC);

CREATE OR REPLACE FUNCTION public.reject_radar_history_mutation()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION '% records are append-only', TG_TABLE_NAME;
END;
$$;

DROP TRIGGER IF EXISTS radar_state_events_append_only ON radar_state_events;
CREATE TRIGGER radar_state_events_append_only
  BEFORE UPDATE OR DELETE ON radar_state_events
  FOR EACH ROW EXECUTE FUNCTION public.reject_radar_history_mutation();

DROP TRIGGER IF EXISTS radar_t0_revisions_append_only ON radar_t0_revisions;
CREATE TRIGGER radar_t0_revisions_append_only
  BEFORE UPDATE OR DELETE ON radar_t0_revisions
  FOR EACH ROW EXECUTE FUNCTION public.reject_radar_history_mutation();

CREATE TABLE IF NOT EXISTS radar_contour_links (
  id BIGSERIAL PRIMARY KEY,
  public_id UUID UNIQUE NOT NULL,
  media_trend_id BIGINT NOT NULL REFERENCES radar_trends(id) ON DELETE RESTRICT,
  action_trend_id BIGINT NOT NULL REFERENCES radar_trends(id) ON DELETE RESTRICT,
  status VARCHAR(16) NOT NULL CHECK (status IN ('aligned','divergent','insufficient')),
  evidence JSONB NOT NULL DEFAULT '{}',
  evaluated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(media_trend_id, action_trend_id),
  CHECK (media_trend_id <> action_trend_id)
);
CREATE INDEX IF NOT EXISTS idx_radar_contour_links_media_time
  ON radar_contour_links(media_trend_id, evaluated_at DESC, id DESC);

CREATE OR REPLACE FUNCTION public.validate_radar_contour_link_topology()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  media_scope VARCHAR(16);
  media_contour VARCHAR(16);
  media_country CHAR(2);
  media_subject TEXT;
  media_direction VARCHAR(24);
  action_scope VARCHAR(16);
  action_contour VARCHAR(16);
  action_country CHAR(2);
  action_subject TEXT;
  action_direction VARCHAR(24);
BEGIN
  SELECT scope, contour, country_code, subject_key, direction
  INTO media_scope, media_contour, media_country, media_subject, media_direction
  FROM public.radar_trends
  WHERE id = NEW.media_trend_id;

  SELECT scope, contour, country_code, subject_key, direction
  INTO action_scope, action_contour, action_country, action_subject, action_direction
  FROM public.radar_trends
  WHERE id = NEW.action_trend_id;

  IF media_scope IS DISTINCT FROM 'country'
     OR media_contour IS DISTINCT FROM 'media'
     OR action_scope IS DISTINCT FROM 'country'
     OR action_contour IS DISTINCT FROM 'action' THEN
    RAISE EXCEPTION
      'radar contour link requires country/media and country/action trends'
      USING ERRCODE = '23514';
  END IF;

  IF media_country IS DISTINCT FROM action_country
     OR media_subject IS DISTINCT FROM action_subject
     OR media_direction IS DISTINCT FROM action_direction THEN
    RAISE EXCEPTION
      'radar contour link trends must share country, subject, and direction'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS radar_contour_links_topology ON radar_contour_links;
CREATE TRIGGER radar_contour_links_topology
  BEFORE INSERT OR UPDATE ON radar_contour_links
  FOR EACH ROW EXECUTE FUNCTION public.validate_radar_contour_link_topology();

CREATE TABLE IF NOT EXISTS analysis_runs (
  id BIGSERIAL PRIMARY KEY,
  public_id UUID UNIQUE NOT NULL,
  run_type VARCHAR(40) NOT NULL,
  input_hash CHAR(64) NOT NULL,
  parameters JSONB NOT NULL DEFAULT '{}',
  detector_version VARCHAR(40) NOT NULL DEFAULT 'not_applicable',
  model_version VARCHAR(120) NOT NULL DEFAULT 'not_applicable',
  status VARCHAR(16) NOT NULL CHECK (status IN ('pending','running','succeeded','failed','cancelled')),
  cost NUMERIC(14,6) NOT NULL DEFAULT 0 CHECK (cost >= 0),
  input_references JSONB NOT NULL DEFAULT '{}',
  output_references JSONB NOT NULL DEFAULT '{}',
  error JSONB NOT NULL DEFAULT '{}',
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(run_type, input_hash, detector_version, model_version)
);
UPDATE analysis_runs
SET detector_version = 'not_applicable'
WHERE detector_version IS NULL;
UPDATE analysis_runs
SET model_version = 'not_applicable'
WHERE model_version IS NULL;
ALTER TABLE analysis_runs
  ALTER COLUMN detector_version SET DEFAULT 'not_applicable',
  ALTER COLUMN detector_version SET NOT NULL,
  ALTER COLUMN model_version SET DEFAULT 'not_applicable',
  ALTER COLUMN model_version SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_analysis_runs_status_time
  ON analysis_runs(status, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS notification_events (
  id BIGSERIAL PRIMARY KEY,
  public_id UUID UNIQUE NOT NULL,
  trend_id BIGINT NOT NULL REFERENCES radar_trends(id) ON DELETE RESTRICT,
  transition_id UUID NOT NULL REFERENCES radar_state_events(public_id) ON DELETE RESTRICT,
  channel VARCHAR(32) NOT NULL,
  audience_key TEXT NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','sent','failed','suppressed')),
  payload JSONB NOT NULL DEFAULT '{}',
  delivered_at TIMESTAMPTZ,
  error JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(trend_id, transition_id, channel, audience_key)
);
CREATE INDEX IF NOT EXISTS idx_notification_events_trend_time
  ON notification_events(trend_id, created_at DESC, id DESC);
