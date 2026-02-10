const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://YOUR_SERVER_IP:8100";

// ── Types ──────────────────────────────────────────────

export interface PipelineStats {
  redis_available: boolean;
  queue_size?: number;
  processing?: number;
  dead_letters?: number;
  total_processed?: number;
  avg_processing_time_ms?: number;
  error?: string;
}

export interface Alert {
  id: number;
  country: string;
  country_name: string;
  type: string;
  severity: string;
  title: string;
  description: string;
  data: Record<string, unknown>;
  created_at: string;
}

export interface AlertsResponse {
  alerts: Alert[];
}

export interface ResonanceEvent {
  event_key: string;
  resonance_score: number;
  article_count: number;
  source_count: number;
  tier_count: number;
  avg_sentiment: number;
  max_action_level: number;
  first_seen: string;
  last_seen: string;
  spread_hours: number;
  source_names: string[];
  tiers: string[];
}

export interface ResonanceResponse {
  country: string;
  days: number;
  events: ResonanceEvent[];
}

export interface SourceDetail {
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
  articles_last_24h: number;
  first_collected: string | null;
  last_collected: string | null;
  avg_articles_per_day: number;
  relevant_count: number;
  avg_sentiment: number | null;
  relevance_pct: number;
}

export interface SourceCreate {
  name: string;
  url: string;
  country_code: string;
  source_type: string;
  weight: number;
  language: string;
  config: Record<string, unknown>;
  active: boolean;
  tier: string;
}

export interface TestResult {
  success: boolean;
  message: string;
  sample?: {
    title: string;
    url: string;
    body?: string;
  };
}

// ── Fetch helpers ──────────────────────────────────────

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${text}`);
  }
  return res.json();
}

// ── Pipeline ───────────────────────────────────────────

export async function getPipelineStats(): Promise<PipelineStats> {
  return apiFetch<PipelineStats>("/api/v1/pipeline/stats");
}

// ── Alerts ─────────────────────────────────────────────

export async function getAlerts(limit = 50): Promise<AlertsResponse> {
  return apiFetch<AlertsResponse>(`/api/v1/alerts?limit=${limit}`);
}

// ── Resonance ──────────────────────────────────────────

export async function getResonance(code: string, days = 14, limit = 10): Promise<ResonanceResponse> {
  return apiFetch<ResonanceResponse>(`/api/v1/countries/${code}/resonance?days=${days}&limit=${limit}`);
}

// ── Source CRUD ────────────────────────────────────────

export async function getSourceDetail(id: number): Promise<SourceDetail> {
  return apiFetch<SourceDetail>(`/api/v1/sources/${id}`);
}

export async function createSource(data: SourceCreate) {
  return apiFetch<{ message: string; source: SourceDetail }>("/api/v1/sources", {
    method: "POST",
    body: JSON.stringify(data),
  });
}

export async function updateSource(id: number, data: Partial<SourceCreate>) {
  return apiFetch<{ message: string; source: SourceDetail }>(`/api/v1/sources/${id}`, {
    method: "PUT",
    body: JSON.stringify(data),
  });
}

export async function deleteSource(id: number) {
  return apiFetch<{ message: string }>(`/api/v1/sources/${id}`, { method: "DELETE" });
}

export async function toggleSource(id: number) {
  return apiFetch<{ message: string; active: boolean }>(`/api/v1/sources/${id}/toggle`, {
    method: "PATCH",
  });
}

export async function testSource(id: number): Promise<TestResult> {
  return apiFetch<TestResult>(`/api/v1/sources/${id}/test`, { method: "POST" });
}

export async function testSourceUrl(data: SourceCreate): Promise<TestResult> {
  return apiFetch<TestResult>("/api/v1/sources/test-url", {
    method: "POST",
    body: JSON.stringify(data),
  });
}
