-- Migration 013: widen briefs.scope to hold topic:* scopes (e.g. "topic:ukraine_war")
-- Idempotent: VARCHAR widening is safe to re-run.

ALTER TABLE briefs ALTER COLUMN scope TYPE VARCHAR(40);
