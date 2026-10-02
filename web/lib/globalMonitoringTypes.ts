export interface GlobalCoverageCountry {
  code: string;
  name_ru: string;
  configured_sources: number;
  working_direct_publishers: number;
  sampled_articles_7d: number;
  latest_local_published_at: string | null;
  latest_local_collected_at: string | null;
  coverage_state: string;
  work: Record<string, number>;
}

export interface GlobalCoverageResponse {
  as_of: string | null;
  status: "ok" | "not_started";
  stale?: boolean;
  scope_count: number;
  countries: GlobalCoverageCountry[];
  limits: { per_country: number; window_days: 7; counts_are_bounded: true };
  screening: { status: "ok" | "disabled" | "blocked" | "error" | "budget_exhausted" | "missing_key"; reason?: string };
  writer: { status: string; reason?: string };
  source_research?: {
    status: string;
    countries_attempted?: number;
    leads_saved?: number;
    queue?: { totals?: Record<string, number>; countries?: Record<string, Record<string, number>>; lead_count?: number };
  };
  notice: string;
}
