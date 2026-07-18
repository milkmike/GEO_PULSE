-- An early 028 deployment enforced one row per trend/observation and removed
-- valid audit facts.  Relation rows intentionally share that pair, so repair
-- the deployed shape without deleting or combining any evidence.
DROP INDEX IF EXISTS public.uq_radar_trend_evidence_observation;

UPDATE public.radar_trend_evidence target
SET article_id = COALESCE(target.article_id, observation.article_id),
    story_id = COALESCE(target.story_id, observation.story_id),
    signal_id = COALESCE(target.signal_id, observation.signal_id),
    canonical_entity_id = COALESCE(
      target.canonical_entity_id, observation.canonical_entity_id
    )
FROM public.radar_observations observation
WHERE target.observation_id = observation.id
  AND (
    target.article_id IS NULL OR target.story_id IS NULL
    OR target.signal_id IS NULL OR target.canonical_entity_id IS NULL
  );

CREATE INDEX IF NOT EXISTS idx_radar_trend_evidence_article_id_lookup
  ON public.radar_trend_evidence(article_id, trend_id)
  WHERE article_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_radar_trend_evidence_story_id_lookup
  ON public.radar_trend_evidence(story_id, trend_id)
  WHERE story_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_radar_trend_evidence_signal_id_lookup
  ON public.radar_trend_evidence(signal_id, trend_id)
  WHERE signal_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_radar_trend_evidence_canonical_entity_id_lookup
  ON public.radar_trend_evidence(canonical_entity_id, trend_id)
  WHERE canonical_entity_id IS NOT NULL;
