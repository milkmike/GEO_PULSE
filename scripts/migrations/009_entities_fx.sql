-- 009: Entity matching at analysis time + FX rates layer ("media lead markets")

-- Fast entity lookups over analysis.entities (JSONB array of registry keys)
CREATE INDEX IF NOT EXISTS idx_analysis_entities
    ON analysis USING gin(entities jsonb_path_ops);

-- Daily CBR exchange rates: how much RUB per 1 unit of currency
CREATE TABLE IF NOT EXISTS fx_rates (
    day DATE NOT NULL,
    currency CHAR(3) NOT NULL,
    rate_to_rub DECIMAL(14,6) NOT NULL,
    change_1d_pct DECIMAL(8,4),
    fetched_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (day, currency)
);
CREATE INDEX IF NOT EXISTS idx_fx_rates_currency ON fx_rates(currency, day DESC);
