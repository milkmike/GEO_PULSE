-- Make Radar observation evidence joins race-safe and efficient.  Preserve the
-- oldest decision row and only backfill representative source roots.

UPDATE radar_trend_evidence target
SET article_id = COALESCE(target.article_id, (
      SELECT candidate.article_id FROM radar_trend_evidence candidate
      WHERE candidate.trend_id = target.trend_id
        AND candidate.observation_id = target.observation_id
        AND candidate.article_id IS NOT NULL
      ORDER BY candidate.id LIMIT 1
    )),
    story_id = COALESCE(target.story_id, (
      SELECT candidate.story_id FROM radar_trend_evidence candidate
      WHERE candidate.trend_id = target.trend_id
        AND candidate.observation_id = target.observation_id
        AND candidate.story_id IS NOT NULL
      ORDER BY candidate.id LIMIT 1
    )),
    signal_id = COALESCE(target.signal_id, (
      SELECT candidate.signal_id FROM radar_trend_evidence candidate
      WHERE candidate.trend_id = target.trend_id
        AND candidate.observation_id = target.observation_id
        AND candidate.signal_id IS NOT NULL
      ORDER BY candidate.id LIMIT 1
    )),
    canonical_entity_id = COALESCE(target.canonical_entity_id, (
      SELECT candidate.canonical_entity_id FROM radar_trend_evidence candidate
      WHERE candidate.trend_id = target.trend_id
        AND candidate.observation_id = target.observation_id
        AND candidate.canonical_entity_id IS NOT NULL
      ORDER BY candidate.id LIMIT 1
    ))
WHERE target.observation_id IS NOT NULL;

DELETE FROM radar_trend_evidence duplicate
USING radar_trend_evidence survivor
WHERE duplicate.trend_id = survivor.trend_id
  AND duplicate.observation_id = survivor.observation_id
  AND duplicate.observation_id IS NOT NULL
  AND duplicate.id > survivor.id;

CREATE UNIQUE INDEX IF NOT EXISTS uq_radar_trend_evidence_observation
  ON radar_trend_evidence(trend_id, observation_id);

CREATE INDEX IF NOT EXISTS idx_radar_trend_evidence_article_id_lookup
  ON radar_trend_evidence(article_id, trend_id)
  WHERE article_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_radar_trend_evidence_story_id_lookup
  ON radar_trend_evidence(story_id, trend_id)
  WHERE story_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_radar_trend_evidence_signal_id_lookup
  ON radar_trend_evidence(signal_id, trend_id)
  WHERE signal_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_radar_trend_evidence_canonical_entity_id_lookup
  ON radar_trend_evidence(canonical_entity_id, trend_id)
  WHERE canonical_entity_id IS NOT NULL;
