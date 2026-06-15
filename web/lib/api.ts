import type {
  AgreementGroup, Brief, CountrySummary, Dossier, EntityStat, FxSeries, Headline, Health,
  MapEntry, Meta, Signal, SourceHealthRow, SourceRow, TopicStat, TradeYear,
  UNVoteYear,
} from "./types";

/** API base: build-time env wins; otherwise same host on :8100 (compose default). */
export function apiBase(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8100`;
  }
  return "http://localhost:8100";
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json() as Promise<T>;
}

/** Thrown when the admin key is missing/wrong (401/403). */
export class AdminAuthError extends Error {}

/** GET an admin endpoint with the X-Admin-Key header. */
export async function adminGet<T>(path: string, key: string): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, {
    cache: "no-store",
    headers: { "X-Admin-Key": key },
  });
  if (res.status === 401 || res.status === 403) throw new AdminAuthError("forbidden");
  if (!res.ok) throw new Error(`${res.status} ${path}`);
  return res.json() as Promise<T>;
}

export const api = {
  countries: () =>
    get<{ countries: CountrySummary[]; total: number }>("/api/v2/countries"),
  map: () => get<{ map: MapEntry[] }>("/api/v2/map"),
  dossier: (code: string, days = 90) =>
    get<Dossier>(`/api/v2/countries/${code}?days=${days}`),
  topics: (code: string, days = 30) =>
    get<{ topics: TopicStat[] }>(`/api/v2/countries/${code}/topics?days=${days}`),
  headlines: (code: string, days = 3, limit = 15) =>
    get<{ source: string; headlines: Headline[] }>(
      `/api/v2/countries/${code}/headlines?days=${days}&limit=${limit}`),
  entities: (code: string, days = 30) =>
    get<{ entities: EntityStat[] }>(`/api/v2/countries/${code}/entities?days=${days}`),
  fx: (code: string, days = 90) =>
    get<FxSeries>(`/api/v2/countries/${code}/fx?days=${days}`),
  countryBrief: (code: string) => get<Brief>(`/api/v2/countries/${code}/brief`),
  worldBrief: () => get<Brief>("/api/v2/brief"),
  worldHeadlines: (hours = 24, limit = 20, region?: string | null, topic?: string | null) =>
    get<{ headlines: Headline[]; total: number }>(
      `/api/v2/headlines?hours=${hours}&limit=${limit}${region ? `&region=${region}` : ""}${topic ? `&topic=${topic}` : ""}`),
  topicBrief: (topic: string) =>
    get<Brief & { topic: string; label: string }>(`/api/v2/topics/${topic}/brief`),
  signals: (params = "") =>
    get<{ signals: Signal[]; total: number }>(`/api/v2/signals?days=7&limit=200${params}`),
  health: () => get<Health>("/api/v2/health"),
  meta: () => get<Meta>("/api/v2/meta"),
  sources: () => get<{ sources: SourceRow[] }>("/api/v1/sources"),
  healthSources: () => get<{ sources: SourceHealthRow[]; total: number }>("/api/v2/health/sources"),
  topicCountries: (topic: string, days = 30) =>
    get<{ label: string; countries: { country_code: string; country_name: string; articles: number; avg_sentiment: number | null }[] }>(
      `/api/v2/topics/${topic}/countries?days=${days}`),
  unVotes: (code: string) =>
    get<{ data: UNVoteYear[] }>(`/api/v2/countries/${code}/un-votes`),
  trade: (code: string) =>
    get<{ data: TradeYear[] }>(`/api/v2/countries/${code}/trade`),
  agreements: (code: string, days = 180) =>
    get<{ agreements: AgreementGroup[] }>(`/api/v2/countries/${code}/agreements?days=${days}`),
};
