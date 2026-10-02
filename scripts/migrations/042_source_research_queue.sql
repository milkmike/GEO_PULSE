-- Unverified source research work; never an active collector source.
CREATE TABLE IF NOT EXISTS source_research_tasks (
 id BIGSERIAL PRIMARY KEY,
 country_code CHAR(2) NOT NULL,
 gap_type TEXT NOT NULL CHECK (gap_type IN
   ('no_active_sources','no_successful_direct_fetch','no_qualifying_sample_in_7d')),
 status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN
   ('queued','researching','needs_review','blocked','resolved')),
 gap_observed_at TIMESTAMPTZ NOT NULL,
 last_observed_at TIMESTAMPTZ NOT NULL,
 attempted_at TIMESTAMPTZ,
 researched_at TIMESTAMPTZ,
 lease_until TIMESTAMPTZ,
 lease_token UUID,
 next_attempt_at TIMESTAMPTZ,
 last_reason TEXT,
 attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(country_code,gap_type)
);
CREATE INDEX IF NOT EXISTS source_research_tasks_discovery_idx
 ON source_research_tasks(attempted_at,country_code,id)
 WHERE gap_type='no_active_sources' AND status IN ('queued','researching');

CREATE TABLE IF NOT EXISTS source_research_leads (
 id BIGSERIAL PRIMARY KEY,
 task_id BIGINT NOT NULL REFERENCES source_research_tasks(id),
 country_code CHAR(2) NOT NULL,
 provider TEXT NOT NULL,
 entity_id TEXT NOT NULL,
 name TEXT NOT NULL,
 website TEXT NOT NULL,
 provenance_url TEXT NOT NULL,
 country_entity TEXT NOT NULL,
 revision TEXT,
 claimed_country CHAR(2) NOT NULL,
 discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(country_code,provider,entity_id,website)
);
CREATE INDEX IF NOT EXISTS source_research_leads_country_idx
 ON source_research_leads(country_code,discovered_at DESC,id DESC);
