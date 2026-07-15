export type Level = "ally" | "partner" | "neutral" | "cooling" | "tension" | "hostile";

export interface CountrySummary {
  code: string;
  name: string;
  name_en: string;
  iso3: string;
  flag: string;
  region: string;
  tier: number;
  score: number;
  structural: number | null;
  media: number | null;
  level: Level;
  delta_24h: number | null;
  delta_7d: number | null;
  article_count: number | null;
  gdelt_volume: number | null;
  gdelt_tone: number | null;
  updated_at: string;
}

export interface MapEntry {
  iso3: string;
  code: string;
  name: string;
  score: number;
  level: Level;
  delta_24h: number | null;
}

export interface MapHistoryFrame {
  day: string;
  iso3: string[];
  scores: number[];
}

export interface Signal {
  id: number;
  type: string;
  country_code: string | null;
  country_name: string | null;
  severity: "info" | "warning" | "critical";
  confidence: number;
  title: string;
  description: string | null;
  payload: Record<string, unknown> | null;
  created_at: string;
  expires_at: string | null;
  active: boolean;
}

export type SignalListItem = Omit<Signal, "country_code" | "country_name" | "active" | "expires_at"> & {
  country_code?: string | null;
  country_name?: string | null;
  active?: boolean | null;
  expires_at?: string | null;
};

export interface RriHistoryPoint {
  day: string;
  time: string;
  score: number;
  structural: number | null;
  media: number | null;
  boost: number | null;
  version: string;
  delta_24h: number | null;
  aggregation: "daily_last";
}

export interface Dossier {
  country: {
    code: string; name: string; name_en: string; iso3: string; flag: string;
    region: string; region_name: string | null; tier: number;
    memberships: string[]; unfriendly: boolean; sanctions_on_russia: boolean;
    war_with_russia: boolean; baseline_note: string;
  };
  index: {
    score: number; level: Level; structural: number | null; media: number | null;
    boost: number | null; delta_24h: number | null; delta_7d: number | null;
    details: Record<string, unknown> | null; updated_at: string; version: string;
  } | null;
  index_history: RriHistoryPoint[];
  temperature_history: { time: string; temperature: number; article_count: number | null }[];
  gdelt: { day: string; volume: number | null; volume_share: number | null; tone: number | null }[];
  signals: SignalListItem[];
}

export interface TopicStat {
  topic: string;
  label: string;
  articles: number;
  avg_sentiment: number;
  max_action_level: number;
}

export interface Headline {
  title: string;
  url: string | null;
  source?: string | null;
  tier?: string | null;
  language?: string | null;
  published_at?: string;
  seendate?: string;
  sentiment?: number | null;
  action_level?: number | null;
  topics?: string[] | null;
  country_code?: string;
  country_name?: string;
  flag?: string;
}

export interface Citation {
  n: number;
  title: string;
  url: string;
  source: string;
  country: string;
  used?: boolean;
}

export interface EntityStat {
  key: string;
  name: string;
  category: string | null;
  mentions: number;
  avg_sentiment: number | null;
}

export interface FxSeries {
  country_code: string;
  currency: string | null;
  series: { day: string; rate_to_rub: number; change_1d_pct: number | null }[];
  note?: string;
}

export interface Brief {
  content: string;
  model: string | null;
  created_at: string;
  cached?: boolean;
  meta?: { citations?: Citation[] } | null;
  citations?: Citation[] | null;
}

export interface Health {
  verdict: "HEALTHY" | "WARNING" | "DEGRADED" | "UNHEALTHY";
  sources_total: number;
  sources_ok: number;
  coverage_pct: number;
  gdelt: { status: string };
}

export interface Meta {
  regions: Record<string, string>;
  topics: Record<string, string>;
  levels: Level[];
  countries: { code: string; name: string; iso3: string; flag: string; region: string; tier: number; langs?: string[] }[];
}

export interface SourceHealthRow {
  source_id: number;
  status: "OK" | "STALE" | "DEAD";
  last_article_at: string | null;
  articles_30d: number;
}

export interface SourceRow {
  id: number; name: string; url: string; country_code: string;
  source_type: string; language: string | null; active: boolean;
  tier: string; article_count: number; last_collected: string | null;
  relevant_count: number; avg_sentiment: number | null;
}

export interface UNVoteYear {
  year: number; total_votes: number | null; agree_with_russia: number | null;
  disagree_with_russia: number | null; abstain: number | null;
  agreement_pct: number | null;
}

export interface TradeYear {
  year: number; ru_export_usd: number | null; ru_import_usd: number | null;
  total_trade_usd: number | null; trade_balance_usd: number | null;
  yoy_change_pct: number | null;
}

export interface AgreementGroup {
  event_key: string; event_type: string; action_level: number;
  first_at: string; last_at: string; articles_total: number;
  articles: { title: string; url: string | null; source: string; published_at: string }[];
}

export interface ThreadArticle {
  title: string | null;
  url: string | null;
  published_at: string | null;
}

export interface ThreadSummary {
  title?: string;
  summary?: string;
  dynamics?: string;
  impact?: string;
  forecast?: string;
  key_actors?: string[];
  tags?: string[];
}

export interface Thread {
  id: number;
  country_code: string;
  country_name: string;
  title: string;
  status: string;
  arc_phase: string;
  first_seen: string | null;
  last_seen: string | null;
  article_count: number;
  avg_sentiment: number | null;
  importance_score: number;
  velocity: number;
  sentiment_shift: number;
  summary: ThreadSummary | null;
  articles: ThreadArticle[];
}

export type SearchSort = "relevance" | "newest";

export interface ArticleSearchRequest {
  q?: string;
  country?: string;
  topic?: string;
  entity_id?: string;
  from?: string;
  to?: string;
  tier?: string;
  language?: string;
  sort?: SearchSort;
  limit?: number;
}

export interface ArticleSearchFilters {
  country: string | null;
  topic: string | null;
  entity_id: string | null;
  from: string | null;
  to: string | null;
  tier: string | null;
  language: string | null;
}

export interface SearchMatchedEntity {
  id: string;
  name: string | null;
  kind: "person" | "organization" | "location" | "event" | string | null;
  mention_text: string | null;
  confidence: number;
}

export interface SearchEvidence {
  type: string;
  article_id: number;
  text?: string | null;
  entity_id?: string;
  topic?: string;
  story_id?: number;
  confidence?: number;
}

export interface SearchScoreComponents {
  lexical: number;
  entity: number;
  topic: number;
  freshness: number;
  trust: number;
  story: number;
  vector: number | null;
}

export interface SearchArticle {
  article_id: number;
  title: string;
  summary: string | null;
  url: string | null;
  published_at: string;
  language: string | null;
  source: {
    name: string | null;
    country: string;
    tier: string | null;
  };
  topics: string[];
  matched_entities: SearchMatchedEntity[];
  sentiment: number | null;
  action_level: number | null;
  story: {
    id: number;
    slug: string | null;
    title: string | null;
  } | null;
  why_included: string;
  relevance_score: number;
  confidence: number;
  evidence: SearchEvidence[];
  scores: SearchScoreComponents;
}

export interface ArticleSearchResponse {
  query: string;
  filters: ArticleSearchFilters;
  sort: SearchSort;
  limit: number;
  semantic_search: "unavailable" | string;
  items: SearchArticle[];
  candidate_count: number;
  next_cursor: string | null;
}

export interface EntitySuggestion {
  id: string;
  node_id: string;
  kind: "person" | "organization" | "location" | "event";
  label: string;
  aliases: string[];
  match_explanation: string;
}

export interface EntitySuggestionsResponse {
  items: EntitySuggestion[];
  limit: number;
  offset: number;
  has_more: boolean;
  next_cursor: string | null;
}

export type StoryLifecycle =
  | "emerging"
  | "developing"
  | "escalating"
  | "cooling"
  | "resolved";

export interface StorySignalLink {
  id: number;
  type: string;
  severity: string;
  title: string;
  created_at: string;
  confidence: number;
  completeness: "complete" | "partial" | string;
  relation: "explicit_story_evidence" | "shared_article_membership";
  evidence: Record<string, unknown>;
}

export interface StoryRriShift {
  country_code: string;
  country_name: string;
  at: string;
  score: number;
  delta_24h: number;
  version: string;
  relation: "temporal_context";
  why_included: "rri_point_within_story_window";
  limitation: string;
}

export interface StoryCountryContext {
  country_code: string;
  country_name: string;
  article_count: number;
  source_count: number;
  media_tone: number | null;
}

export interface StoryListItem {
  id: number;
  slug: string;
  title_ru: string;
  title_en: string | null;
  summary: string | null;
  lifecycle: StoryLifecycle;
  first_seen: string;
  last_seen: string;
  article_count: number;
  source_count: number;
  country_count: number;
  highest_action_level: number;
  clustering_confidence: number;
  generated_at: string | null;
  countries: string[];
  primary_url: string | null;
  why_included: string[];
  relevance_score: number;
  confidence: number;
  evidence: Record<string, unknown>;
  linked_signal_count: number;
  linked_signals: StorySignalLink[];
  latest_rri_shift: StoryRriShift | null;
  country_context?: StoryCountryContext | null;
}

export interface StoriesListResponse {
  stories: StoryListItem[];
  next_cursor: string | null;
  coverage?: StoryCoverage;
  consistency: StoryConsistency;
}

export interface StoryConsistency {
  ranking_at: string;
  membership_generation: number;
  mode: "membership_generation_live_filters";
  frozen_features: string[];
  live_filters: string[];
  limitation: string;
}

export interface StoryCoverage {
  selected_from: string | null;
  selected_to: string | null;
  available_from: string | null;
  available_to: string | null;
}

export interface CountryStoriesResponse extends StoriesListResponse {
  country: string;
  name: string;
}

export interface StoryCountrySlice extends StoryCountryContext {
  first_seen: string | null;
  last_seen: string | null;
  primary_url: string | null;
}

export interface StoryEntityEvidence {
  entity_id: string;
  canonical_name: string;
  kind: string;
  mentions: number;
  confidence: number;
  evidence: Record<string, unknown>;
}

export interface StoryEventEvidence {
  entity_id: string;
  event_key: string;
  event_at: string | null;
  action_level: number;
  confidence: number;
  evidence: Record<string, unknown>;
}

export interface StoryArticleEvidence {
  article_id: number;
  title: string | null;
  url: string | null;
  published_at: string | null;
  source: string;
  country_code: string;
  membership_confidence: number;
  evidence: Record<string, unknown>;
  is_primary: boolean;
  why_included: string[];
  relevance_score: number;
  confidence: number;
}

export interface StoryDetailResponse extends Omit<StoryListItem, "countries"> {
  countries: StoryCountrySlice[];
  entities: StoryEntityEvidence[];
  events: StoryEventEvidence[];
  rri_shifts: StoryRriShift[];
  articles: StoryArticleEvidence[];
  articles_next_cursor: string | null;
  redirected_from_story_id: number | null;
}

export interface StoriesRequest {
  country?: string;
  lifecycle?: StoryLifecycle;
  topic?: string;
  entity_id?: string;
  date_from?: string;
  date_to?: string;
  since?: string;
  min_confidence?: number;
  min_action_level?: number;
  limit?: number;
}

export interface IndexExplanationRequest {
  from: string;
  to: string;
  rriVersion?: string;
}

export interface EstimatedContribution {
  status: "estimated" | "omitted";
  label: "estimated";
  method: string;
  event_key: string | null;
  input_article_ids: number[];
  removed_article_ids: number[];
  why_included: string;
  relevance_score: number | null;
  confidence: number | null;
  evidence: {
    input_article_ids: number[];
    removed_article_ids: number[];
    relevance_basis: string;
  };
  reason?: string;
  estimated_delta?: number;
  actual_media_estimate?: number;
  counterfactual_media_estimate?: number;
}

export interface InvestigationContextItem {
  scope: "article" | "story" | "signal" | string;
  id: number;
  label: string | null;
  url: string | null;
  occurred_at: string | null;
  why_included: string;
  relevance_score: number;
  confidence: number;
  evidence: Record<string, unknown>;
}

export interface IndexExplanation {
  country_code: string;
  from_time: string;
  to_time: string;
  rri_version: string;
  exact_changes: {
    from_value: number;
    to_value: number;
    total_delta: number;
    structural_delta: number;
    media_delta: number;
    boost_delta: number;
    exact_subtotal: number;
    calculation_adjustment_delta: number;
    rounding_residual: number;
    from_time: string;
    to_time: string;
    rri_version: string;
    weights: {
      from: { structural: number; media: number };
      to: { structural: number; media: number };
    };
    calculation_adjustments: {
      from: number;
      to: number;
      from_rule: unknown;
      to_rule: unknown;
    };
    input_counts: {
      from_articles: number | null;
      to_articles: number | null;
      from_gdelt_volume: number | null;
      to_gdelt_volume: number | null;
    };
  };
  estimated_contributions: EstimatedContribution[];
  context: InvestigationContextItem[];
  related_story_ids: number[];
  related_signal_ids: number[];
  evidence_completeness: "complete" | "partial";
  limitations: string[];
  cache: { status: "hit" | "miss"; input_hash: string };
}

export interface SignalDetail {
  id: number;
  type: string;
  severity: "info" | "warning" | "critical";
  summary: {
    headline: string;
    description: string | null;
    what_changed: string | null;
  };
  rule: {
    detector: string;
    version: string;
    description: string | null;
    threshold: Record<string, unknown>;
    current_rule_reference?: Record<string, unknown> | string | null;
  };
  values: {
    observed: Record<string, unknown>;
    baseline: Record<string, unknown>;
    window: {
      start: string | null;
      end: string | null;
      basis?: string | null;
      status?: string | null;
    };
  };
  chart_points: Record<string, unknown>[];
  articles: Array<{
    id: number;
    title: string | null;
    url: string | null;
    published_at: string | null;
    source_name: string | null;
    country_code: string | null;
    sentiment: number | null;
    action_level: number | null;
    event_key: string | null;
  }>;
  articles_page: {
    total: number;
    returned: number;
    limit: number;
    truncated: boolean;
    has_more: boolean;
  };
  related_story: null | {
    id: number;
    slug: string | null;
    title: string | null;
    summary: string | null;
    lifecycle: string;
    last_seen: string | null;
    confidence: number;
  };
  countries: Array<{
    code: string;
    name: string;
    article_count?: number;
    media_tone?: number | null;
  }>;
  state: {
    created_at: string | null;
    expires_at: string | null;
    active: boolean;
    status: "active" | "expired";
  };
  confidence: number;
  evidence_completeness: "complete" | "partial";
  evidence_ids: string[];
  evidence: {
    article_ids: number[];
    story_ids: number[];
    rri_points: Record<string, unknown>[];
  };
  evidence_truncation: Record<
    "evidence_ids" | "article_ids" | "story_ids" | "rri_points" | "countries",
    { total: number; returned: number; truncated: boolean }
  >;
  limitations: string[];
}

export interface TemperatureMethodology {
  methodology_version: string;
  name: string;
  plain_language: Array<{ id: string; title: string; body: string }>;
  technical: {
    input_eligibility: Record<string, boolean>;
    window_days: number;
    time_decay: { kind: string; tau_seconds: number; formula: string };
    source_weights: { field: string; default: number; cluster_order: string };
    event_type_weights: Record<string, number>;
    action_level_weights: Record<string, number>;
    reprint_importance: { base: number; formula: string };
    event_clustering: {
      key: string;
      minimum_raw_key_length: number;
      normalization_after_gate: string;
    };
    cluster_diminishing: { base: number; formula: string; unclustered_weight: number };
    aggregation: { numerator: string; denominator: string; raw_sentiment: string };
    normalization: {
      factor: number;
      formula: string;
      temperature_round_digits: number;
      raw_sentiment_round_digits: number;
      component_round_digits: number;
    };
    trend: Record<string, string | number>;
    anomaly: Record<string, string | number>;
    upstream_analysis: Record<string, string | boolean>;
  };
  worked_example: {
    articles: Array<{
      sentiment: number;
      source_weight: number;
      event_type: string;
      action_level: number;
      age_seconds: number;
      reprint_count: number;
      duplicate_index: number;
    }>;
    weighted_numerator: number;
    weighted_denominator: number;
    temperature: number;
  };
  limitations: string[];
}
