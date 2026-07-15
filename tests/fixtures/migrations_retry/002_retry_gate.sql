DO $$
BEGIN
  IF to_regclass('public.migration_retry_gate') IS NULL THEN
    RAISE EXCEPTION 'retry gate is closed';
  END IF;
END $$;
INSERT INTO migration_retry_log(step) VALUES (2);
