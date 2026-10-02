export interface EarlySignalObservation {
  id: string;
  article_id: number;
  quote: string;
  text_ru: string;
}

export interface EarlySignalEvidence {
  id: number;
  title: string;
  source_name: string;
  url: string | null;
  published_at: string | null;
  collected_at: string | null;
}

export interface EarlySignalWatch {
  observation_ru: string;
  effect: "strengthens" | "weakens";
  by_date: string | null;
}

export interface EarlySignal {
  id: string;
  status: "needs_review";
  headline_ru: string;
  observations: EarlySignalObservation[];
  interpretation_ru: string;
  hypothesis_ru: string;
  opportunity_ru: string | null;
  counterargument_ru: string | null;
  watch: EarlySignalWatch[];
  country_codes: string[];
  horizon_date: string | null;
  russia_link: "hypothesis" | "unestablished";
  as_of: string;
  evidence: EarlySignalEvidence[];
  review_note: string | null;
}

export interface EarlySignalsResponse {
  as_of: string;
  items: EarlySignal[];
  notice: string | null;
}
