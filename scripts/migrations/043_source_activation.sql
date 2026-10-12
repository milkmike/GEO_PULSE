-- Resumable automatic feed validation of provenanced research leads.
ALTER TABLE source_research_leads
 ADD COLUMN IF NOT EXISTS activation_status TEXT NOT NULL DEFAULT 'queued'
   CHECK (activation_status IN ('queued','validating','active','blocked')),
 ADD COLUMN IF NOT EXISTS activation_attempted_at TIMESTAMPTZ,
 ADD COLUMN IF NOT EXISTS activation_lease_until TIMESTAMPTZ,
 ADD COLUMN IF NOT EXISTS activation_lease_token UUID,
 ADD COLUMN IF NOT EXISTS activation_next_attempt_at TIMESTAMPTZ,
 ADD COLUMN IF NOT EXISTS activation_attempts INTEGER NOT NULL DEFAULT 0,
 ADD COLUMN IF NOT EXISTS activation_last_reason TEXT,
 ADD COLUMN IF NOT EXISTS activated_source_id INTEGER REFERENCES sources(id);
CREATE INDEX IF NOT EXISTS source_research_leads_activation_idx
 ON source_research_leads(activation_attempted_at,country_code,id)
 WHERE activation_status IN ('queued','validating','blocked');
