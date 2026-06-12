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
  index_history: { day: string; score: number; structural: number | null; media: number | null }[];
  temperature_history: { time: string; temperature: number; article_count: number | null }[];
  gdelt: { day: string; volume: number | null; volume_share: number | null; tone: number | null }[];
  signals: Omit<Signal, "country_code" | "country_name" | "active" | "expires_at">[];
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
  countries: { code: string; name: string; iso3: string; flag: string; region: string; tier: number }[];
}

export interface SourceRow {
  id: number; name: string; url: string; country_code: string;
  source_type: string; language: string | null; active: boolean;
  tier: string; article_count: number; last_collected: string | null;
  relevant_count: number; avg_sentiment: number | null;
}
