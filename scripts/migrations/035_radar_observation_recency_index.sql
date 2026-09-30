-- Serve Radar readiness from recent persisted media observations.
-- Autocommit is required for the concurrent index build and retry cleanup.
\set ON_ERROR_STOP on

SELECT format('DROP INDEX CONCURRENTLY IF EXISTS %I.%I;', namespace.nspname, cls.relname)
FROM pg_index idx
JOIN pg_class cls ON cls.oid = idx.indexrelid
JOIN pg_namespace namespace ON namespace.oid = cls.relnamespace
WHERE namespace.nspname = 'public'
  AND cls.relname = 'idx_radar_observations_media_time'
  AND idx.indrelid = 'public.radar_observations'::regclass
  AND (NOT idx.indisvalid OR NOT idx.indisready)
\gexec

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_radar_observations_media_time
  ON public.radar_observations (observed_at DESC) INCLUDE (country_code)
  WHERE contour = 'media';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_index idx JOIN pg_class cls ON cls.oid = idx.indexrelid
    WHERE idx.indrelid = 'public.radar_observations'::regclass
      AND cls.relname = 'idx_radar_observations_media_time'
      AND idx.indisvalid AND idx.indisready
  ) THEN
    RAISE EXCEPTION 'Radar observation recency index is not valid and ready';
  END IF;
END $$;
