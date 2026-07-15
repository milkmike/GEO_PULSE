-- Fast evidence lookup and bounded search-candidate indexes for existing installs.
-- Must run in psql autocommit mode: CREATE/DROP INDEX CONCURRENTLY cannot run
-- inside an explicit transaction. Invalid remnants are removed before retry.

\set ON_ERROR_STOP on

-- A single transactional clock publishes story membership generations. The
-- builder increments this row in the same transaction as its memberships, so
-- readers either see the old clock and old memberships or the new clock and
-- new memberships. Sequence high-water marks cannot provide that guarantee.
CREATE TABLE IF NOT EXISTS public.story_membership_clock (
  singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
  generation BIGINT NOT NULL DEFAULT 0 CHECK (generation >= 0)
);

INSERT INTO public.story_membership_clock (singleton, generation)
VALUES (TRUE, 0)
ON CONFLICT (singleton) DO NOTHING;

ALTER TABLE public.story_articles
  ADD COLUMN IF NOT EXISTS membership_generation BIGINT;

-- Normalize only values outside the documented six-level scale. Level 6 is a
-- valid value and must never be reduced to the previous five-level ceiling.
UPDATE public.stories
SET highest_action_level = LEAST(6, GREATEST(1, highest_action_level))
WHERE highest_action_level NOT BETWEEN 1 AND 6;

UPDATE public.story_events
SET action_level = LEAST(6, GREATEST(1, action_level))
WHERE action_level NOT BETWEEN 1 AND 6;

UPDATE public.story_articles
SET membership_generation = 0
WHERE membership_generation IS NULL OR membership_generation < 0;

ALTER TABLE public.story_articles
  ALTER COLUMN membership_generation SET DEFAULT 0;
ALTER TABLE public.story_articles
  ALTER COLUMN membership_generation SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'story_articles_membership_generation_nonnegative'
      AND conrelid = 'public.story_articles'::regclass
  ) THEN
    ALTER TABLE public.story_articles
      ADD CONSTRAINT story_articles_membership_generation_nonnegative
      CHECK (membership_generation >= 0) NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'stories_highest_action_level_range'
      AND conrelid = 'public.stories'::regclass
  ) THEN
    ALTER TABLE public.stories
      ADD CONSTRAINT stories_highest_action_level_range
      CHECK (highest_action_level BETWEEN 1 AND 6) NOT VALID;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'story_events_action_level_range'
      AND conrelid = 'public.story_events'::regclass
  ) THEN
    ALTER TABLE public.story_events
      ADD CONSTRAINT story_events_action_level_range
      CHECK (action_level BETWEEN 1 AND 6) NOT VALID;
  END IF;
END
$$;

ALTER TABLE public.story_articles
  VALIDATE CONSTRAINT story_articles_membership_generation_nonnegative;
ALTER TABLE public.stories
  VALIDATE CONSTRAINT stories_highest_action_level_range;
ALTER TABLE public.story_events
  VALIDATE CONSTRAINT story_events_action_level_range;

-- Existing memberships predate the immutable action and confidence snapshots
-- used by stable story/article ranking. Backfill in committed batches so
-- retries remain bounded and rows already snapshotted are never rewritten.
CREATE OR REPLACE PROCEDURE public.backfill_story_action_snapshots()
LANGUAGE plpgsql
AS $backfill$
DECLARE
  updated_rows INTEGER;
BEGIN
  LOOP
    WITH batch AS MATERIALIZED (
      SELECT sa.story_id,
             sa.article_id,
             LEAST(6, GREATEST(1, COALESCE(an.action_level, 1))) AS action_level,
             LEAST(1.0, GREATEST(
               0.0,
               COALESCE(sa.membership_confidence, 0.0)
             )) AS membership_confidence
      FROM public.story_articles sa
      LEFT JOIN public.analysis an ON an.article_id = sa.article_id
      WHERE NOT CASE
        WHEN jsonb_typeof(sa.evidence) = 'object'
             AND jsonb_typeof(sa.evidence->'action_level_snapshot') = 'number'
             AND sa.evidence->>'action_level_snapshot' ~ '^[1-6]$'
        THEN true
        ELSE false
      END
         OR NOT CASE
           WHEN jsonb_typeof(sa.evidence) = 'object'
                AND CASE
                  WHEN jsonb_typeof(
                    sa.evidence->'membership_confidence_snapshot'
                  ) = 'number'
                  THEN (sa.evidence
                          ->>'membership_confidence_snapshot')::numeric
                       BETWEEN 0.0 AND 1.0
                  ELSE false
                END
           THEN true
           ELSE false
         END
      ORDER BY sa.story_id, sa.article_id
      LIMIT 5000
    )
    UPDATE public.story_articles sa
    SET evidence = jsonb_set(
      jsonb_set(
        CASE
          WHEN jsonb_typeof(sa.evidence) = 'object' THEN sa.evidence
          ELSE '{}'::jsonb
        END,
        '{action_level_snapshot}',
        CASE
          WHEN jsonb_typeof(sa.evidence) = 'object'
               AND jsonb_typeof(sa.evidence->'action_level_snapshot') = 'number'
               AND sa.evidence->>'action_level_snapshot' ~ '^[1-6]$'
          THEN sa.evidence->'action_level_snapshot'
          ELSE to_jsonb(batch.action_level)
        END,
        true
      ),
      '{membership_confidence_snapshot}',
      CASE
        WHEN jsonb_typeof(sa.evidence) = 'object'
             AND CASE
               WHEN jsonb_typeof(
                 sa.evidence->'membership_confidence_snapshot'
               ) = 'number'
               THEN (sa.evidence
                       ->>'membership_confidence_snapshot')::numeric
                    BETWEEN 0.0 AND 1.0
               ELSE false
             END
        THEN sa.evidence->'membership_confidence_snapshot'
        ELSE to_jsonb(batch.membership_confidence)
      END,
      true
    )
    FROM batch
    WHERE sa.story_id = batch.story_id
      AND sa.article_id = batch.article_id
      AND (
        NOT CASE
          WHEN jsonb_typeof(sa.evidence) = 'object'
               AND jsonb_typeof(sa.evidence->'action_level_snapshot') = 'number'
               AND sa.evidence->>'action_level_snapshot' ~ '^[1-6]$'
          THEN true
          ELSE false
        END
        OR NOT CASE
          WHEN jsonb_typeof(sa.evidence) = 'object'
               AND CASE
                 WHEN jsonb_typeof(
                   sa.evidence->'membership_confidence_snapshot'
                 ) = 'number'
                 THEN (sa.evidence
                         ->>'membership_confidence_snapshot')::numeric
                      BETWEEN 0.0 AND 1.0
                 ELSE false
               END
          THEN true
          ELSE false
        END
      );

    GET DIAGNOSTICS updated_rows = ROW_COUNT;
    COMMIT;
    EXIT WHEN updated_rows = 0;
  END LOOP;
END;
$backfill$;

CALL public.backfill_story_action_snapshots();
DROP PROCEDURE public.backfill_story_action_snapshots();

SELECT format(
  'DROP INDEX CONCURRENTLY IF EXISTS %I.%I;',
  namespace.nspname,
  index_class.relname
)
FROM pg_index index_state
JOIN pg_class index_class ON index_class.oid = index_state.indexrelid
JOIN pg_namespace namespace ON namespace.oid = index_class.relnamespace
WHERE namespace.nspname = 'public'
  AND (
    (
      index_class.relname IN (
        'idx_signal_evidence_story_ids_gin',
        'idx_signal_evidence_article_ids_gin'
      )
      AND index_state.indrelid = 'public.signal_evidence'::regclass
    )
    OR (
      index_class.relname = 'idx_story_articles_generation'
      AND index_state.indrelid = 'public.story_articles'::regclass
    )
    OR (
      index_class.relname IN (
        'idx_articles_language_published_id',
        'idx_articles_source_candidates'
      )
      AND index_state.indrelid = 'public.articles'::regclass
    )
  )
  AND NOT index_state.indisvalid
\gexec

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_story_articles_generation
  ON public.story_articles (story_id, membership_generation, article_id);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_signal_evidence_story_ids_gin
  ON public.signal_evidence USING GIN (story_ids);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_signal_evidence_article_ids_gin
  ON public.signal_evidence USING GIN (article_ids);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_articles_language_published_id
  ON public.articles (language, published_at DESC, id DESC)
  WHERE is_duplicate = FALSE;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_articles_source_candidates
  ON public.articles (source_id, published_at DESC, id DESC)
  WHERE is_duplicate = FALSE;
