-- Fast overlap lookup for story/signal evidence on existing installations.
-- Must run in psql autocommit mode: CREATE/DROP INDEX CONCURRENTLY cannot run
-- inside an explicit transaction. Invalid remnants are removed before retry.

\set ON_ERROR_STOP on

-- Existing memberships predate the immutable action snapshot used by stable
-- story ranking. Backfill in committed batches so retries remain bounded and
-- rows already snapshotted are never rewritten.
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
             LEAST(5, GREATEST(1, COALESCE(an.action_level, 1))) AS action_level
      FROM public.story_articles sa
      LEFT JOIN public.analysis an ON an.article_id = sa.article_id
      WHERE NOT ((
        CASE
          WHEN jsonb_typeof(sa.evidence) = 'object' THEN sa.evidence
          ELSE '{}'::jsonb
        END
      ) ? 'action_level_snapshot')
      ORDER BY sa.story_id, sa.article_id
      LIMIT 5000
    )
    UPDATE public.story_articles sa
    SET evidence = jsonb_set(
      CASE
        WHEN jsonb_typeof(sa.evidence) = 'object' THEN sa.evidence
        ELSE '{}'::jsonb
      END,
      '{action_level_snapshot}',
      to_jsonb(batch.action_level),
      true
    )
    FROM batch
    WHERE sa.story_id = batch.story_id
      AND sa.article_id = batch.article_id
      AND NOT ((
        CASE
          WHEN jsonb_typeof(sa.evidence) = 'object' THEN sa.evidence
          ELSE '{}'::jsonb
        END
      ) ? 'action_level_snapshot');

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
WHERE index_class.relname IN (
  'idx_signal_evidence_story_ids_gin',
  'idx_signal_evidence_article_ids_gin'
)
  AND namespace.nspname = 'public'
  AND index_state.indrelid = 'public.signal_evidence'::regclass
  AND NOT index_state.indisvalid
\gexec

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_signal_evidence_story_ids_gin
  ON public.signal_evidence USING GIN (story_ids);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_signal_evidence_article_ids_gin
  ON public.signal_evidence USING GIN (article_ids);
