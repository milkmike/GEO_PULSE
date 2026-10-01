export interface DecisionCountry {
  code: string;
  name: string;
  region: string;
}

export interface DecisionEvidence {
  article_id: number;
  title_ru: string;
  title_original: string;
  url: string | null;
  publisher_name: string;
  publisher_country_code: string | null;
  published_at: string;
  collected_at: string;
  russia_explanation_ru: string;
  russia_evidence_quote: string;
  country_evidence_quote: string;
  summary_ru: string;
  kind: string;
}

export interface NewsLead {
  article_id: number;
  title_ru: string | null;
  title_original: string;
  url: string | null;
  publisher_name: string;
  publisher_country_code: string | null;
  published_at: string;
  collected_at: string;
  topic: string;
  event_type: string;
  actor_type: string;
  russia_relation: "direct" | "indirect" | "uncertain";
  status: "needs_review";
  countries: string[];
}

export interface DecisionWorkspaceResponse {
  as_of: string;
  country: DecisionCountry;
  countries: DecisionCountry[];
  attention: Array<{
    code: string;
    name: string;
    count_24h: number;
    count_7d: number;
    reason: string;
    status?: "needs_review";
    latest_at: string;
  }>;
  brief: { day: DecisionEvidence[]; week: DecisionEvidence[] };
  discovery?: {
    day: NewsLead[];
    week: NewsLead[];
    unassigned_day?: NewsLead[];
    unassigned_week?: NewsLead[];
  };
  positions: Array<{
    id: string;
    actor: string;
    actor_type: string;
    position_ru: string;
    evidence_quote: string;
    evidence: DecisionEvidence;
  }>;
  changes: Array<{
    id: string;
    category: string;
    change_ru: string;
    evidence_quote: string;
    evidence: DecisionEvidence;
  }>;
  topics: Array<{
    id: string;
    title: string;
    question: string;
    evidence: DecisionEvidence[];
  }>;
  coverage: {
    collected_from_country_7d: number;
    reviewed_from_country_7d: number;
    classified_from_country_7d?: number;
    pending_from_country_7d?: number;
    discovered_to_country_7d?: number;
    last_classified_at?: string | null;
    triage_status?: "not_started" | "partial" | "up_to_date" | "budget_exhausted";
    relevant_to_country_7d: number;
    publisher_families: number;
    local_publisher_families: number;
    last_collected_at: string | null;
    last_published_at: string | null;
    last_analyzed_at: string | null;
    independent_confirmation: "not_assessed";
    truncated: boolean;
    limitations: string[];
  };
}
