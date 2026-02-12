export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

// ── Types ──────────────────────────────────────────────

export interface Country {
  code: string;
  name: string;
  iso3: string;
  article_count: number;
  temperature: number;
  raw_sentiment: number;
  trend: "rising" | "falling" | "stable";
  last_updated: string;
  divergence: number;
}

export interface CountriesResponse {
  countries: Country[];
}

export interface Stats {
  total_articles: number;
  total_analyzed: number;
  total_relevant: number;
  active_sources: number;
  total_duplicates: number;
  period_days: number;
  oldest_article: string;
  newest_article: string;
  last_temperature_update: string;
}

export interface CountryEvent {
  title: string;
  url: string;
  published_at: string;
  sentiment: number | null;
  event_type: string; // "diplomatic", "economic", "security", "cultural"
  confidence: number;
  action_level: number; // 1-6
  source: string;
  reprint_count: number;
  reasoning: string;
}

export interface CountryEventsResponse {
  country: string;
  name: string;
  events: CountryEvent[];
}

export interface CountryDigest {
  digest: string; // markdown with links
  generated_at: string;
  temperature: number;
}

export interface Thread {
  id: number;
  country_code: string;
  country_name: string;
  thread_key: string;
  title: string;
  narrative: string;
  status: "developing" | "resolved" | "dormant";
  arc_phase: "emerging" | "escalating" | "peak" | "cooling" | "resolved";
  first_seen: string;
  last_seen: string;
  article_count: number;
  avg_sentiment: number;
  max_action_level: number;
  importance_score: number;
  generated_at: string;
}

export interface ThreadsResponse {
  threads: Thread[];
}

export interface ThreadTimelineArticle {
  article_id: number;
  title: string;
  url: string;
  published_at: string;
  sentiment: number;
  action_level: number;
  event_type: string;
  source: string;
  tier: string;
}

export interface ThreadDetail extends Thread {
  timeline: ThreadTimelineArticle[];
}

export interface UNVotesSummary {
  votes: Array<{
    resolution: string;
    date: string;
    countries: Record<string, string>;
  }>;
}

export interface TemperaturePoint {
  time: string;
  temperature: number;
  raw_sentiment: number;
  trend: string;
  anomaly_score: number | null;
  article_count: number;
  components: {
    diplomatic: number | null;
    military: number | null;
    economic: number | null;
    cultural: number | null;
    security: number | null;
  };
}

export interface TemperatureResponse {
  data: TemperaturePoint[];
}

export interface UNVoteYear {
  year: number;
  total_votes: number;
  agree_with_russia: number;
  disagree_with_russia: number;
  abstain: number;
  agreement_pct: number;
}

export interface UNVotesResponse {
  country: string;
  name: string;
  data: UNVoteYear[];
}

export interface TradeYear {
  year: number;
  ru_export_usd: number;
  ru_import_usd: number;
  total_trade_usd: number;
  trade_balance_usd: number;
  yoy_change_pct: number | null;
}

export interface TradeResponse {
  country: string;
  name: string;
  data: TradeYear[];
}

// ── Analytics types ────────────────────────────────────

export interface CoverageDay {
  date: string;
  total: number;
  analyzed: number;
  relevant: number;
}

export interface CoverageCountry {
  code: string;
  name: string;
  days: CoverageDay[];
}

export interface CoverageResponse {
  period_days: number;
  countries: CoverageCountry[];
}

export interface TierInfo {
  tier: string;
  label: string;
  sentiment: number;
  article_count: number;
  source_count: number;
}

export interface TierDivergenceCountry {
  code: string;
  name: string;
  tiers: TierInfo[];
  divergence: number;
  overall_sentiment: number;
  total_articles: number;
  most_positive_tier: string | null;
  most_negative_tier: string | null;
}

export interface TierDivergenceResponse {
  period_days: number;
  countries: TierDivergenceCountry[];
}

// ── Admin types ────────────────────────────────────────

export interface AdminSummary {
  total_cost_today: number;
  total_cost_week: number;
  total_cost_month: number;
  calls_today: number;
  top_service: string | null;
  top_model: string | null;
}

export interface AdminUsageRow {
  date: string;
  service: string;
  total_calls: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
}

export interface AdminUsageResponse {
  period: string;
  data: AdminUsageRow[];
}

export interface AdminKeyInfo {
  service: string;
  env_var: string;
  key_masked: string;
  status: "active" | "missing" | "blocked";
}

export interface AdminKeysResponse {
  keys: AdminKeyInfo[];
}

export interface AdminHealthService {
  service: string;
  status: "ok" | "degraded" | "unreachable";
  http_code: number | null;
  latency_ms: number | null;
  error?: string;
}

export interface AdminHealthResponse {
  services: AdminHealthService[];
}

export interface AdminScriptUsage {
  script: string;
  total_calls: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  avg_duration_ms: number;
}

export interface AdminScriptUsageResponse {
  period_days: number;
  scripts: AdminScriptUsage[];
}

// ── Fetch wrapper ──────────────────────────────────────

async function apiFetch<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const base = API_URL.startsWith("http") ? API_URL : `http://127.0.0.1:8100`;
  const url = new URL(`${base}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    });
  }
  // Client-side: use relative path; Server-side: use absolute URL
  const fetchUrl = typeof window !== "undefined" ? `${API_URL}${path}${url.search}` : url.toString();
  const res = await fetch(fetchUrl, { next: { revalidate: 300 } });
  if (!res.ok) throw new Error(`API error: ${res.status} ${res.statusText}`);
  return res.json();
}

// ── API functions ──────────────────────────────────────

export async function getCountries(days = 30) {
  return apiFetch<CountriesResponse>("/api/v1/countries", { days });
}

export async function getStats(days = 30) {
  return apiFetch<Stats>("/api/v1/stats", { days });
}

export async function getCountryEvents(code: string, days = 30) {
  return apiFetch<CountryEventsResponse>(`/api/v1/countries/${code}/events`, { days });
}

export async function getCountryDigest(code: string, days = 30) {
  return apiFetch<CountryDigest>(`/api/v1/countries/${code}/digest`, { days });
}

export async function getThread(id: number) {
  return apiFetch<ThreadDetail>(`/api/v1/threads/${id}`);
}

export async function getCountryThreads(code: string, params?: { status?: string; limit?: number }) {
  return apiFetch<ThreadsResponse>(`/api/v1/countries/${code}/threads`, params as Record<string, string | number>);
}

export async function getCountryTemperature(code: string, days = 30) {
  return apiFetch<TemperatureResponse>(`/api/v1/countries/${code}/temperature`, { days: Math.min(days, 365) });
}

export async function getCountryUNVotes(code: string) {
  return apiFetch<UNVotesResponse>(`/api/v1/countries/${code}/un-votes`);
}

export async function getCountryTrade(code: string) {
  return apiFetch<TradeResponse>(`/api/v1/countries/${code}/trade`);
}

// ── Sources ────────────────────────────────────────────

export interface Source {
  id: number;
  name: string;
  url: string;
  country_code: string;
  source_type: string;
  weight: number;
  language: string;
  config: Record<string, unknown>;
  active: boolean;
  tier: string;
  created_at: string;
  article_count: number;
  last_collected: string | null;
  relevant_count: number;
  avg_sentiment: number | null;
}

export interface SourcesResponse {
  sources: Source[];
}

export async function getSources() {
  return apiFetch<SourcesResponse>("/api/v1/sources");
}

export async function getCoverage(days = 30) {
  return apiFetch<CoverageResponse>("/api/v1/analytics/coverage", { days });
}

export async function getTierDivergence(days = 14) {
  return apiFetch<TierDivergenceResponse>("/api/v1/analytics/tier-divergence", { days });
}

// ── Helpers ────────────────────────────────────────────

export const PERIOD_DAYS: Record<string, number> = {
  "Неделя": 7,
  "Месяц": 30,
  "Квартал": 90,
  "Год": 365,
  "4 года": 1460,
};

export function temperatureColor(temp: number): string {
  if (temp <= -20) return "#3b82f6"; // blue-500
  if (temp <= -10) return "#60a5fa"; // blue-400
  if (temp <= 0) return "#93c5fd";   // blue-300
  if (temp <= 10) return "#fbbf24";  // amber-400
  if (temp <= 20) return "#f97316";  // orange-500
  return "#ef4444";                   // red-500
}

export function temperatureLabel(temp: number): string {
  if (temp <= -20) return "Очень холодно";
  if (temp <= -10) return "Холодно";
  if (temp <= 0) return "Прохладно";
  if (temp <= 10) return "Тепло";
  if (temp <= 20) return "Горячо";
  return "Очень горячо";
}

export function trendIcon(trend: string): string {
  if (trend === "rising") return "↑";
  if (trend === "falling") return "↓";
  return "→";
}

export function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

export function minutesAgo(dateStr: string): number {
  return Math.round((Date.now() - new Date(dateStr).getTime()) / 60000);
}

export const COUNTRY_CODES = ["KZ", "AM", "UZ", "KG", "TJ", "TM", "AZ", "GE", "MD", "BY"] as const;

export const COUNTRY_FLAGS: Record<string, string> = {
  KZ: "🇰🇿", AM: "🇦🇲", UZ: "🇺🇿", KG: "🇰🇬", TJ: "🇹🇯",
  TM: "🇹🇲", AZ: "🇦🇿", GE: "🇬🇪", MD: "🇲🇩", BY: "🇧🇾",
};

export const COUNTRY_NAMES: Record<string, string> = {
  KZ: "Казахстан", AM: "Армения", UZ: "Узбекистан", KG: "Кыргызстан",
  TJ: "Таджикистан", TM: "Туркменистан", AZ: "Азербайджан",
  GE: "Грузия", MD: "Молдова", BY: "Беларусь",
};

// ── High-Impact Events ─────────────────────────────────

export interface HighImpactEvent {
  title: string;
  url: string;
  source: string;
  tier: string;
  country_code: string;
  country_name: string;
  sentiment: number;
  action_level: number;
  event_type: string;
  published_at: string;
}

export interface HighImpactEventsResponse {
  events: HighImpactEvent[];
}

export async function getHighImpactEvents(days = 14, minActionLevel = 3, limit = 10) {
  return apiFetch<HighImpactEventsResponse>("/api/v1/events/high-impact", {
    days,
    min_action_level: minActionLevel,
    limit,
  });
}

// ── Admin API ──────────────────────────────────────────

export async function getAdminSummary() {
  return apiFetch<AdminSummary>("/api/v1/admin/summary");
}

export async function getAdminUsage(period: string = "week", service: string = "all") {
  return apiFetch<AdminUsageResponse>("/api/v1/admin/usage", { period, service });
}

export async function getAdminKeys() {
  return apiFetch<AdminKeysResponse>("/api/v1/admin/keys");
}

export async function getAdminHealth() {
  return apiFetch<AdminHealthResponse>("/api/v1/admin/health");
}

export async function getAdminUsageByScript(days: number = 7) {
  return apiFetch<AdminScriptUsageResponse>("/api/v1/admin/usage-by-script", { days });
}
