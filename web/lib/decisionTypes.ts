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
    latest_at: string;
  }>;
  brief: { day: DecisionEvidence[]; week: DecisionEvidence[] };
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
