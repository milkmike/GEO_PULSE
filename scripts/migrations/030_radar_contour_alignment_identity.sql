-- Country waves keep their local analytical identity (story/event/dataset),
-- while contour links use a separate canonical Russia-relationship identity.
-- This prevents unrelated local waves from being merged merely to make the
-- media and action contours comparable.
ALTER TABLE public.radar_trends
  ADD COLUMN IF NOT EXISTS alignment_subject TEXT,
  ADD COLUMN IF NOT EXISTS alignment_direction VARCHAR(24);

WITH observed_identities AS (
  SELECT evidence.trend_id,
         NULLIF(observation.evidence->>'alignment_subject', '') AS subject,
         NULLIF(observation.evidence->>'alignment_direction', '') AS direction,
         count(DISTINCT observation.id) AS observations
  FROM public.radar_trend_evidence evidence
  JOIN public.radar_observations observation
    ON observation.id = evidence.observation_id
  WHERE observation.evidence->>'alignment_subject' IS NOT NULL
    AND observation.evidence->>'alignment_direction' IS NOT NULL
  GROUP BY evidence.trend_id, 2, 3
), best_identity AS (
  SELECT DISTINCT ON (trend_id) trend_id, subject, direction
  FROM observed_identities
  WHERE subject IS NOT NULL AND direction IS NOT NULL
  ORDER BY trend_id, observations DESC, subject, direction
)
UPDATE public.radar_trends trend
SET alignment_subject = identity.subject,
    alignment_direction = identity.direction
FROM best_identity identity
WHERE trend.id = identity.trend_id
  AND trend.scope = 'country'
  AND (trend.alignment_subject IS NULL OR trend.alignment_direction IS NULL);

UPDATE public.radar_trends
SET alignment_subject = COALESCE(alignment_subject, subject_key),
    alignment_direction = COALESCE(alignment_direction, direction)
WHERE scope = 'country'
  AND (alignment_subject IS NULL OR alignment_direction IS NULL);

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
     OR OLD.alignment_subject IS DISTINCT FROM NEW.alignment_subject
     OR OLD.alignment_direction IS DISTINCT FROM NEW.alignment_direction
     OR OLD.wave_key IS DISTINCT FROM NEW.wave_key
     OR OLD.meta_key IS DISTINCT FROM NEW.meta_key
     OR OLD.detector_version IS DISTINCT FROM NEW.detector_version THEN
    RAISE EXCEPTION 'radar trend identity fields are immutable'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.validate_radar_contour_link_topology()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  media_scope VARCHAR(16);
  media_contour VARCHAR(16);
  media_country CHAR(2);
  media_alignment_subject TEXT;
  media_alignment_direction VARCHAR(24);
  action_scope VARCHAR(16);
  action_contour VARCHAR(16);
  action_country CHAR(2);
  action_alignment_subject TEXT;
  action_alignment_direction VARCHAR(24);
BEGIN
  SELECT scope, contour, country_code, alignment_subject, alignment_direction
  INTO media_scope, media_contour, media_country,
       media_alignment_subject, media_alignment_direction
  FROM public.radar_trends
  WHERE id = NEW.media_trend_id;

  SELECT scope, contour, country_code, alignment_subject, alignment_direction
  INTO action_scope, action_contour, action_country,
       action_alignment_subject, action_alignment_direction
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
     OR media_alignment_subject IS NULL
     OR media_alignment_direction IS NULL
     OR action_alignment_subject IS NULL
     OR action_alignment_direction IS NULL
     OR media_alignment_subject IS DISTINCT FROM action_alignment_subject
     OR media_alignment_direction IS DISTINCT FROM action_alignment_direction THEN
    RAISE EXCEPTION
      'radar contour link trends must share country and canonical alignment identity'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;
