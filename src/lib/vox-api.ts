import { API_URL } from "./api";

// ── VOX Types ──────────────────────────────────────────

export interface VoxCountry {
  code: string;
  vox_temperature: number | null;
  media_temperature: number | null;
  elite_gap: number | null;
  comment_count: number;
  unique_authors: number;
  bot_ratio: number;
  dominant_emotion: string | null;
  pro_ratio: number;
  anti_ratio: number;
  updated_at: string | null;
}

export interface VoxStats {
  total_comments: number;
  unique_authors: number;
  bot_comments: number;
  period_days: number;
}

export interface VoxOverview {
  countries: VoxCountry[];
  stats: VoxStats;
}

export interface VoxTimelinePoint {
  time: string;
  vox_temperature: number | null;
  media_temperature: number | null;
  elite_gap: number | null;
  comment_count: number;
  dominant_emotion: string | null;
}

export interface VoxComment {
  id: number;
  text: string;
  published_at: string;
  platform: string;
  likes: number;
  sentiment: number;
  emotion: string;
  stance: string;
  topics: string[];
}

export interface VoxCountryDetail {
  country: string;
  days: number;
  timeline: VoxTimelinePoint[];
  emotions: Record<string, number>;
  top_topics: { topic: string; count: number }[];
  recent_comments: VoxComment[];
}

export interface EliteGapCountry {
  code: string;
  vox_temperature: number | null;
  media_temperature: number | null;
  elite_gap: number | null;
  gap_direction: string;
  comment_count: number;
  unique_authors: number;
}

export interface EliteGapResponse {
  countries: EliteGapCountry[];
}

export interface VoxChannel {
  id: number;
  platform: string;
  channel_username: string;
  country_code: string;
  name: string;
  active: boolean;
  last_collected: string | null;
  has_discussion: boolean;
  total_comments: number;
}

// ── API Functions ──────────────────────────────────────

async function voxFetch<T>(path: string, params?: Record<string, string | number>): Promise<T> {
  const url = new URL(`${API_URL}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v));
    });
  }
  const res = await fetch(url.toString(), { next: { revalidate: 120 } });
  if (!res.ok) throw new Error(`VOX API error: ${res.status}`);
  return res.json();
}

export async function getVoxOverview(days = 7) {
  return voxFetch<VoxOverview>("/api/v1/vox", { days });
}

export async function getVoxCountry(code: string, days = 14) {
  return voxFetch<VoxCountryDetail>(`/api/v1/vox/countries/${code}`, { days });
}

export async function getEliteGap(days = 7) {
  return voxFetch<EliteGapResponse>("/api/v1/vox/elite-gap", { days });
}

export async function getVoxChannels() {
  return voxFetch<{ channels: VoxChannel[] }>("/api/v1/vox/channels");
}
