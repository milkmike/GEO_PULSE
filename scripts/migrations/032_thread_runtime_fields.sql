-- Fields used by the v2 worker/API already exist on older live installations,
-- but were missing from the reproducible migration path.
SET LOCAL lock_timeout = '5s';

ALTER TABLE public.threads
    ADD COLUMN IF NOT EXISTS summary_json JSONB,
    ADD COLUMN IF NOT EXISTS velocity DOUBLE PRECISION DEFAULT 0,
    ADD COLUMN IF NOT EXISTS sentiment_shift DOUBLE PRECISION DEFAULT 0,
    ADD COLUMN IF NOT EXISTS related_threads INTEGER[],
    ADD COLUMN IF NOT EXISTS merged_keys TEXT[];
