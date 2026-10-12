-- Shorten old provider-rate-limit retries without changing other work or leases.
UPDATE source_research_tasks
SET next_attempt_at = COALESCE(attempted_at, researched_at, now()) + interval '6 hours',
    updated_at = now()
WHERE status = 'blocked'
  AND last_reason = 'rate_limited'
  AND lease_until IS NULL
  AND lease_token IS NULL
  AND next_attempt_at IS NOT NULL
  AND next_attempt_at > COALESCE(attempted_at, researched_at, now()) + interval '6 hours';
