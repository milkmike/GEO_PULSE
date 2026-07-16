-- Thermometer v1 anomaly z-scores are mathematically unbounded. Preserve the
-- score instead of silently clipping it to the legacy NUMERIC(4,2) range.

ALTER TABLE public.temperature
  ALTER COLUMN anomaly_score TYPE NUMERIC(8,2);
