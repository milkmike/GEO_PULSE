import type {
  AgreementGroup, ArticleSearchRequest, ArticleSearchResponse, Brief, CountrySummary,
  Dossier, EntityStat, EntitySuggestionsResponse, FxSeries, Headline, Health, MapEntry,
  IndexExplanation, IndexExplanationRequest, Meta, Signal, SignalDetail, SourceHealthRow,
  SourceRow, StoriesListResponse, StoriesRequest, StoryDetailResponse, TemperatureMethodology,
  Thread, TopicBriefResponse, TopicStat, TradeYear, UNVoteYear,
  RadarCoverage, RadarEvidencePage, RadarFilters, RadarMethodology, RadarTimeline,
  RadarTrend, RadarTrendPage,
} from "./types";

/** API base: build-time env wins; otherwise same host on :8100 (compose default). */
export function apiBase(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== "undefined") {
    return `${window.location.protocol}//${window.location.hostname}:8100`;
  }
  return "http://localhost:8100";
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${apiBase()}${path}`, { cache: "no-store", signal });
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

function storyParams(request: StoriesRequest, cursor?: string | null): URLSearchParams {
  const params = new URLSearchParams();
  for (const key of [
    "country", "lifecycle", "topic", "entity_id", "date_from", "date_to", "since",
  ] as const) {
    const value = request[key];
    if (typeof value === "string" && value.trim()) params.set(key, value.trim());
  }
  for (const key of ["min_confidence", "min_action_level", "limit"] as const) {
    const value = request[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      params.set(key, String(value));
    }
  }
  if (cursor) params.set("cursor", cursor);
  return params;
}

function radarParams(request: RadarFilters, cursor?: string | null): URLSearchParams {
  const params = new URLSearchParams();
  if (request.state) params.set("state", request.state);
  if (request.contour) params.set("contour", request.contour);
  if (request.country?.trim()) params.set("country", request.country.trim().toUpperCase());
  if (request.limit && Number.isFinite(request.limit)) params.set("limit", String(request.limit));
  if (cursor) params.set("cursor", cursor);
  return params;
}

export const api = {
  radar: (request: RadarFilters = {}, cursor?: string | null, signal?: AbortSignal) =>
    get<RadarTrendPage>(`/api/v2/radar?${radarParams(request, cursor).toString()}`, signal),
  countryRadar: (code: string, request: Omit<RadarFilters, "country"> = {}, cursor?: string | null, signal?: AbortSignal) =>
    get<RadarTrendPage>(`/api/v2/countries/${encodeURIComponent(code.trim().toUpperCase())}/radar?${radarParams(request, cursor).toString()}`, signal),
  radarTrend: (publicId: string, signal?: AbortSignal) =>
    get<RadarTrend>(`/api/v2/radar/trends/${encodeURIComponent(publicId)}`, signal),
  radarTimeline: (publicId: string, signal?: AbortSignal) =>
    get<RadarTimeline>(`/api/v2/radar/trends/${encodeURIComponent(publicId)}/timeline`, signal),
  radarEvidence: (publicId: string, cursor?: string | null, limit = 25, signal?: AbortSignal) => {
    const params = new URLSearchParams({ limit: String(limit) });
    if (cursor) params.set("cursor", cursor);
    return get<RadarEvidencePage>(`/api/v2/radar/trends/${encodeURIComponent(publicId)}/evidence?${params.toString()}`, signal);
  },
  radarCoverage: (signal?: AbortSignal) => get<RadarCoverage>("/api/v2/radar/coverage", signal),
  radarMethodology: (signal?: AbortSignal) => get<RadarMethodology>("/api/v2/methodology/radar", signal),
  indexExplanation: (
    code: string,
    request: IndexExplanationRequest,
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams({ from: request.from, to: request.to });
    if (request.rriVersion) params.set("rri_version", request.rriVersion);
    return get<IndexExplanation>(
      `/api/v2/countries/${encodeURIComponent(code.trim().toUpperCase())}/index-explanation?${params.toString()}`,
      signal,
    );
  },
  signalDetail: (signalId: number, signal?: AbortSignal) =>
    get<SignalDetail>(`/api/v2/signals/${signalId}`, signal),
  temperatureMethodology: (signal?: AbortSignal) =>
    get<TemperatureMethodology>("/api/v2/methodology/temperature", signal),
  stories: (
    request: StoriesRequest = {},
    cursor?: string | null,
    signal?: AbortSignal,
  ) => get<StoriesListResponse>(
    `/api/v2/stories?${storyParams(request, cursor).toString()}`,
    signal,
  ),
  countryStories: (
    code: string,
    request: Omit<StoriesRequest, "country"> = {},
    cursor?: string | null,
    signal?: AbortSignal,
  ) => get<StoriesListResponse & { country: string; name: string }>(
    `/api/v2/countries/${encodeURIComponent(code.trim().toUpperCase())}/stories?${storyParams(request, cursor).toString()}`,
    signal,
  ),
  story: (
    storyId: number,
    articleCursor?: string | null,
    articleLimit = 25,
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams({ article_limit: String(articleLimit) });
    if (articleCursor) params.set("article_cursor", articleCursor);
    return get<StoryDetailResponse>(
      `/api/v2/stories/${storyId}?${params.toString()}`,
      signal,
    );
  },
  searchArticles: (
    request: ArticleSearchRequest,
    cursor?: string | null,
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams();
    for (const key of [
      "q", "country", "topic", "entity_id", "from", "to", "tier", "language",
    ] as const) {
      const value = request[key];
      if (typeof value === "string" && value.trim()) params.set(key, value.trim());
    }
    if (request.sort) params.set("sort", request.sort);
    if (request.limit) params.set("limit", String(request.limit));
    if (cursor) params.set("cursor", cursor);
    return get<ArticleSearchResponse>(
      `/api/v2/search/articles?${params.toString()}`,
      signal,
    );
  },
  entitySuggestions: (query: string, signal?: AbortSignal) =>
    get<EntitySuggestionsResponse>(
      `/api/v2/entities/suggest?q=${encodeURIComponent(query)}&limit=8`,
      signal,
    ),
  countries: () =>
    get<{ countries: CountrySummary[]; total: number }>("/api/v2/countries"),
  map: () => get<{ map: MapEntry[] }>("/api/v2/map"),
  dossier: (code: string, days = 90, signal?: AbortSignal) =>
    get<Dossier>(`/api/v2/countries/${code}?days=${days}`, signal),
  topics: (code: string, days = 30, signal?: AbortSignal) =>
    get<{ topics: TopicStat[] }>(`/api/v2/countries/${code}/topics?days=${days}`, signal),
  headlines: (code: string, days = 3, limit = 15, signal?: AbortSignal) =>
    get<{ source: string; headlines: Headline[] }>(
      `/api/v2/countries/${code}/headlines?days=${days}&limit=${limit}`, signal),
  entities: (code: string, days = 30, signal?: AbortSignal) =>
    get<{ entities: EntityStat[] }>(`/api/v2/countries/${code}/entities?days=${days}`, signal),
  fx: (code: string, days = 90, signal?: AbortSignal) =>
    get<FxSeries>(`/api/v2/countries/${code}/fx?days=${days}`, signal),
  countryBrief: (code: string, signal?: AbortSignal) =>
    get<Brief>(`/api/v2/countries/${code}/brief`, signal),
  worldBrief: () => get<Brief>("/api/v2/brief"),
  worldHeadlines: (hours = 24, limit = 20, region?: string | null, topic?: string | null) =>
    get<{ headlines: Headline[]; total: number }>(
      `/api/v2/headlines?hours=${hours}&limit=${limit}${region ? `&region=${region}` : ""}${topic ? `&topic=${topic}` : ""}`),
  topicBrief: (topic: string) =>
    get<TopicBriefResponse>(`/api/v2/topics/${topic}/brief`),
  signals: (params = "") =>
    get<{ signals: Signal[]; total: number }>(`/api/v2/signals?days=7&limit=200${params}`),
  health: () => get<Health>("/api/v2/health"),
  meta: () => get<Meta>("/api/v2/meta"),
  sources: () => get<{ sources: SourceRow[] }>("/api/v1/sources"),
  healthSources: () => get<{ sources: SourceHealthRow[]; total: number }>("/api/v2/health/sources"),
  topicCountries: (topic: string, days = 30) =>
    get<{ label: string; countries: { country_code: string; country_name: string; articles: number; avg_sentiment: number | null }[] }>(
      `/api/v2/topics/${topic}/countries?days=${days}`),
  unVotes: (code: string, signal?: AbortSignal) =>
    get<{ data: UNVoteYear[] }>(`/api/v2/countries/${code}/un-votes`, signal),
  trade: (code: string, signal?: AbortSignal) =>
    get<{ data: TradeYear[] }>(`/api/v2/countries/${code}/trade`, signal),
  agreements: (code: string, days = 180, signal?: AbortSignal) =>
    get<{ agreements: AgreementGroup[] }>(`/api/v2/countries/${code}/agreements?days=${days}`, signal),
  // Generate the AI dossier on demand (slow; cached 6h server-side).
  generateCountryBrief: (code: string) =>
    get<Brief>(`/api/v2/countries/${code}/brief?generate=1`),
  tierDivergence: (code: string, days = 30) =>
    get<{ tiers: { tier: string; sentiment: number; articles: number; sources: number }[] }>(
      `/api/v2/countries/${code}/tier-divergence?days=${days}`),
  sanctions: (code: string) =>
    get<{
      has_data: boolean; lists_count?: number; target_count?: number; delta?: number;
      last_change?: string | null; programs?: { name: string; title: string; targets: number }[];
    }>(`/api/v2/countries/${code}/sanctions`),
  energy: (code: string) =>
    get<{
      has_data: boolean; total_eur?: number; total_tonne?: number;
      commodities?: { group: string; name: string; value_eur: number; value_tonne: number }[];
      world_rank?: number | null; period_from?: string | null; updated_at?: string | null;
    }>(`/api/v2/countries/${code}/energy`),
  ruRadar: () =>
    get<{
      has_data: boolean;
      usd_rub?: number | null; usd_rub_chg30?: number | null;
      cny_rub?: number | null; cny_rub_chg30?: number | null;
      moex?: number | null; moex_chg30?: number | null;
      moex_spark?: { d: string; v: number }[];
      pressure?: number; verdict?: string | null; updated_at?: string | null;
    }>("/api/v2/ru-radar"),
  vox: (code: string, days = 14) =>
    get<{
      timeline: { time: string; vox_temperature: number | null; media_temperature: number | null;
                  elite_gap: number | null; comment_count: number; dominant_emotion: string | null }[];
      emotions: Record<string, number>;
      top_topics: { topic: string; count: number }[];
      recent_comments: { id: number; text: string; sentiment: number; emotion: string; stance: string }[];
    }>(`/api/v1/vox/countries/${code}?days=${days}`),
  // Narrative storylines (threads) for a country — arc_phase, dynamics/forecast, article links.
  countryThreads: (code: string, limit = 6) =>
    get<{ country: string; name: string; threads: Thread[] }>(
      `/api/v1/countries/${code}/threads?limit=${limit}`),
};
