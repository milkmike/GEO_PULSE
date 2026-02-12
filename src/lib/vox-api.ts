import { api as apiFetchUnified } from "./api-client";

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
  return apiFetchUnified<T>(path, params, { revalidate: 120 });
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

// ── Articles Feed ──────────────────────────────────────

export interface FeedArticle {
  id: number;
  title: string;
  body: string;
  url: string;
  published_at: string | null;
  language: string;
  source_name: string;
  country_code: string;
  source_type: string;
  tier: string;
}

export interface ArticlesFeedResponse {
  articles: FeedArticle[];
  total: number;
  limit: number;
  offset: number;
}

// ── Insights ───────────────────────────────────────────

export interface VoxInsights {
  total_comments: number;
  total_analyzed: number;
  emotions: { emotion: string; count: number }[];
  stances: { stance: string; count: number }[];
  topics: { topic: string; count: number }[];
  sentiment_buckets: { bucket: string; count: number }[];
  emotion_samples: Record<string, { text: string; sentiment: number; country: string }[]>;
  comment_languages: { language: string; count: number }[];
  article_languages: Record<string, Record<string, number>>;
}

export async function getVoxInsights(params: { country?: string; days?: number } = {}) {
  const p: Record<string, string | number> = {};
  if (params.country) p.country = params.country;
  if (params.days) p.days = params.days;
  return voxFetch<VoxInsights>("/api/v1/vox/insights", p);
}

// ── Comments Feed ──────────────────────────────────────

export interface FeedComment {
  id: number;
  text: string;
  published_at: string | null;
  platform: string;
  country_code: string;
  likes: number;
  channel_id: string;
  sentiment: number | null;
  emotion: string | null;
  stance: string | null;
  bot_score: number | null;
  topics: string[];
}

export interface CommentsFeedResponse {
  comments: FeedComment[];
  total: number;
  limit: number;
  offset: number;
}

export async function getCommentsFeed(params: {
  country?: string;
  days?: number;
  limit?: number;
  offset?: number;
} = {}) {
  const p: Record<string, string | number> = {};
  if (params.country) p.country = params.country;
  if (params.days) p.days = params.days;
  if (params.limit) p.limit = params.limit;
  if (params.offset) p.offset = params.offset;
  return voxFetch<CommentsFeedResponse>("/api/v1/vox/comments", p);
}

// ── Articles Feed ──────────────────────────────────────

export async function getArticlesFeed(params: {
  country?: string;
  source_type?: string;
  search?: string;
  days?: number;
  limit?: number;
  offset?: number;
} = {}) {
  const p: Record<string, string | number> = {};
  if (params.country) p.country = params.country;
  if (params.source_type) p.source_type = params.source_type;
  if (params.search) p.search = params.search;
  if (params.days) p.days = params.days;
  if (params.limit) p.limit = params.limit;
  if (params.offset) p.offset = params.offset;
  return voxFetch<ArticlesFeedResponse>("/api/v1/articles/feed", p);
}
