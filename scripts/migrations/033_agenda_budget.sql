-- Persistent, fail-closed reservations for the explicitly authorized Jev campaign.
CREATE TABLE IF NOT EXISTS agenda_budget (
    campaign TEXT PRIMARY KEY,
    limit_usd NUMERIC NOT NULL CHECK (limit_usd > 0 AND limit_usd <= 3),
    charged_usd NUMERIC NOT NULL DEFAULT 0 CHECK (charged_usd >= 0),
    halted BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agenda_budget_calls (
    id UUID PRIMARY KEY,
    campaign TEXT NOT NULL REFERENCES agenda_budget(campaign) ON DELETE RESTRICT,
    payload_hash CHAR(64) NOT NULL,
    pair_keys JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(pair_keys)='array'),
    status VARCHAR(40) NOT NULL DEFAULT 'reserved',
    charged_usd NUMERIC NOT NULL DEFAULT 0.10 CHECK (charged_usd >= 0),
    actual_cost_usd NUMERIC CHECK (actual_cost_usd >= 0),
    cost_invalid BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_agenda_budget_calls_campaign
    ON agenda_budget_calls(campaign, created_at);

ALTER TABLE agenda_budget_calls
    ADD COLUMN IF NOT EXISTS pair_keys JSONB NOT NULL DEFAULT '[]'::jsonb
    CHECK (jsonb_typeof(pair_keys)='array');
CREATE UNIQUE INDEX IF NOT EXISTS idx_agenda_budget_calls_payload
    ON agenda_budget_calls(campaign,payload_hash);
CREATE INDEX IF NOT EXISTS idx_agenda_budget_calls_pairs
    ON agenda_budget_calls USING GIN (pair_keys);
