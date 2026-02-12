"use client";

import { useEffect, useState } from "react";
import { COUNTRY_NAMES } from "@/lib/api";
import {
  getVoxOverview,
  getVoxChannels,
  getEliteGap,
  getArticlesFeed,
  type VoxOverview,
  type VoxChannel,
  type EliteGapCountry,
  type FeedArticle,
} from "@/lib/vox-api";
import SectionHeader from "@/components/SectionHeader";
import InfoPopover from "@/components/InfoPopover";

/* ── Emoji helpers ──────────────────────────────────── */
const FLAG: Record<string, string> = {
  KZ: "🇰🇿", AM: "🇦🇲", UZ: "🇺🇿", KG: "🇰🇬", TJ: "🇹🇯",
  TM: "🇹🇲", AZ: "🇦🇿", GE: "🇬🇪", MD: "🇲🇩", BY: "🇧🇾",
};

const EMOTION_EMOJI: Record<string, string> = {
  anger: "😡", fear: "😰", joy: "😊", sadness: "😢",
  surprise: "😮", disgust: "🤢", hope: "🌱", irony: "😏",
  neutral: "😐", frustration: "😤",
};

function tempColor(t: number | null): string {
  if (t === null) return "text-zinc-500";
  if (t >= 70) return "text-red-400";
  if (t >= 55) return "text-orange-400";
  if (t >= 45) return "text-yellow-400";
  if (t >= 30) return "text-cyan-400";
  return "text-blue-400";
}

function gapColor(gap: number | null): string {
  if (gap === null) return "text-zinc-500";
  const abs = Math.abs(gap);
  if (abs >= 15) return "text-red-400";
  if (abs >= 8) return "text-orange-400";
  if (abs >= 3) return "text-yellow-400";
  return "text-emerald-400";
}

function GapBar({ gap }: { gap: number | null }) {
  if (gap === null) return <span className="text-zinc-600">—</span>;
  const w = Math.min(Math.abs(gap) * 3, 100);
  const dir = gap > 0 ? "Элиты теплее" : "Народ теплее";
  const barColor = gap > 0 ? "bg-orange-500" : "bg-cyan-500";
  return (
    <div className="flex items-center gap-2 text-xs">
      <div className="w-24 h-2 bg-zinc-800 rounded-full overflow-hidden relative">
        <div
          className={`absolute top-0 h-full rounded-full ${barColor}`}
          style={{
            width: `${w}%`,
            left: gap > 0 ? "50%" : `${50 - w}%`,
          }}
        />
        <div className="absolute top-0 left-1/2 w-px h-full bg-zinc-600" />
      </div>
      <span className="text-zinc-400 w-28 truncate">{dir}</span>
    </div>
  );
}

/* ── Stat Card ──────────────────────────────────────── */
function StatCard({ label, value, sub, icon }: {
  label: string; value: string | number; sub?: string; icon: string;
}) {
  return (
    <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-4">
      <div className="flex items-center gap-2 text-zinc-400 text-xs mb-1">
        <span>{icon}</span> {label}
      </div>
      <div className="text-2xl font-bold text-white">{value}</div>
      {sub && <div className="text-xs text-zinc-500 mt-1">{sub}</div>}
    </div>
  );
}

/* ── Country Row (Elite Gap table) ──────────────────── */
function EliteGapRow({ c }: { c: EliteGapCountry }) {
  return (
    <div className="flex items-center gap-3 py-2.5 px-3 hover:bg-zinc-800/40 rounded-lg transition-colors">
      <span className="text-lg">{FLAG[c.code] || "🏳️"}</span>
      <span className="text-white font-medium w-28 truncate">
        {COUNTRY_NAMES[c.code] || c.code}
      </span>
      <div className="flex items-center gap-4 flex-1">
        <div className="text-center w-16">
          <div className="text-[10px] text-zinc-500">Медиа</div>
          <div className={`text-sm font-mono font-bold ${tempColor(c.media_temperature)}`}>
            {c.media_temperature?.toFixed(1) ?? "—"}°
          </div>
        </div>
        <div className="text-center w-16">
          <div className="text-[10px] text-zinc-500">Народ</div>
          <div className={`text-sm font-mono font-bold ${tempColor(c.vox_temperature)}`}>
            {c.vox_temperature?.toFixed(1) ?? "—"}°
          </div>
        </div>
        <div className="flex-1">
          <GapBar gap={c.elite_gap} />
        </div>
        <div className={`text-right w-14 font-mono font-bold text-sm ${gapColor(c.elite_gap)}`}>
          {c.elite_gap !== null ? `${c.elite_gap > 0 ? "+" : ""}${c.elite_gap.toFixed(1)}` : "—"}
        </div>
      </div>
      <div className="text-xs text-zinc-500 w-16 text-right">
        {c.comment_count.toLocaleString()} 💬
      </div>
    </div>
  );
}

/* ── Channel Card ───────────────────────────────────── */
function ChannelCard({ ch }: { ch: VoxChannel }) {
  return (
    <div className="flex items-center gap-3 py-2 px-3 bg-zinc-900/40 rounded-lg">
      <span className="text-sm">{FLAG[ch.country_code] || "🏳️"}</span>
      <a
        href={`https://t.me/${ch.channel_username}`}
        target="_blank"
        rel="noopener noreferrer"
        className="text-cyan-400 hover:text-cyan-300 text-sm font-medium"
      >
        @{ch.channel_username}
      </a>
      <span className="text-xs text-zinc-500 flex-1 truncate">{ch.name}</span>
      <span className={`w-2 h-2 rounded-full ${ch.has_discussion ? "bg-emerald-400" : "bg-zinc-600"}`} />
      <span className="text-xs text-zinc-500">{ch.total_comments} 💬</span>
    </div>
  );
}

/* ── VoxCountryCard ─────────────────────────────────── */
function VoxCountryCard({ c }: { c: {
  code: string;
  vox_temperature: number | null;
  media_temperature: number | null;
  elite_gap: number | null;
  comment_count: number;
  unique_authors: number;
  bot_ratio: number;
  dominant_emotion: string | null;
} }) {
  return (
    <div className="bg-zinc-900/60 border border-zinc-800 rounded-xl p-4 hover:border-zinc-700 transition-colors">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xl">{FLAG[c.code] || "🏳️"}</span>
        <span className="text-white font-bold">{COUNTRY_NAMES[c.code] || c.code}</span>
        {c.dominant_emotion && (
          <span className="text-sm ml-auto" title={c.dominant_emotion}>
            {EMOTION_EMOJI[c.dominant_emotion] || "❓"}
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 mb-3">
        <div>
          <div className="text-[10px] text-zinc-500 uppercase tracking-wide">Народ</div>
          <div className={`text-xl font-mono font-bold ${tempColor(c.vox_temperature)}`}>
            {c.vox_temperature?.toFixed(1) ?? "—"}°
          </div>
        </div>
        <div>
          <div className="text-[10px] text-zinc-500 uppercase tracking-wide">Медиа</div>
          <div className={`text-xl font-mono font-bold ${tempColor(c.media_temperature)}`}>
            {c.media_temperature?.toFixed(1) ?? "—"}°
          </div>
        </div>
      </div>

      <GapBar gap={c.elite_gap} />

      <div className="flex items-center gap-3 mt-3 text-xs text-zinc-500">
        <span>💬 {c.comment_count.toLocaleString()}</span>
        <span>👤 {c.unique_authors.toLocaleString()}</span>
        <span className={c.bot_ratio > 0.2 ? "text-red-400" : ""}>
          🤖 {(c.bot_ratio * 100).toFixed(0)}%
        </span>
      </div>
    </div>
  );
}

/* ══════════════════════════════════════════════════════ */
/* ══ MAIN PAGE ═══════════════════════════════════════= */
/* ══════════════════════════════════════════════════════ */

export default function VoxPopuliPage() {
  const [overview, setOverview] = useState<VoxOverview | null>(null);
  const [channels, setChannels] = useState<VoxChannel[]>([]);
  const [eliteGap, setEliteGap] = useState<EliteGapCountry[]>([]);
  const [loading, setLoading] = useState(true);
  const [showChannels, setShowChannels] = useState(false);
  const [articles, setArticles] = useState<FeedArticle[]>([]);
  const [articlesTotal, setArticlesTotal] = useState(0);
  const [feedCountry, setFeedCountry] = useState<string>("");
  const [feedType, setFeedType] = useState<string>("");
  const [feedSearch, setFeedSearch] = useState<string>("");
  const [feedLoading, setFeedLoading] = useState(false);
  const [expandedArticle, setExpandedArticle] = useState<number | null>(null);

  const loadArticles = (country?: string, source_type?: string, search?: string) => {
    setFeedLoading(true);
    getArticlesFeed({
      country: country || undefined,
      source_type: source_type || undefined,
      search: search || undefined,
      days: 3,
      limit: 50,
    }).then(res => {
      setArticles(res.articles);
      setArticlesTotal(res.total);
      setFeedLoading(false);
    }).catch(() => setFeedLoading(false));
  };

  useEffect(() => {
    Promise.all([
      getVoxOverview(7).catch(() => null),
      getVoxChannels().catch(() => ({ channels: [] })),
      getEliteGap(7).catch(() => ({ countries: [] })),
    ]).then(([vox, ch, gap]) => {
      if (vox) setOverview(vox);
      setChannels(ch.channels);
      setEliteGap(gap.countries);
      setLoading(false);
    });
    loadArticles();
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-zinc-950 flex items-center justify-center">
        <div className="text-zinc-400 text-lg animate-pulse">🐙 Загрузка VOX POPULI...</div>
      </div>
    );
  }

  const stats = overview?.stats;
  const countries = overview?.countries || [];
  const activeChannels = channels.filter(c => c.active);
  const withDiscussion = channels.filter(c => c.has_discussion);

  return (
    <div className="min-h-screen bg-zinc-950 text-white">
      <div className="max-w-7xl mx-auto px-4 py-8">

        {/* ── Header ── */}
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-2">
            <h1 className="text-3xl font-bold tracking-tight">
              📢 VOX POPULI
            </h1>
            <InfoPopover title="Глас народа">
              Анализ комментариев из Telegram-каналов. Сравнение народного настроения с медийным нарративом. Elite Gap показывает расхождение между тем, что пишут СМИ и что думают люди.
            </InfoPopover>
          </div>
          <p className="text-zinc-400 text-sm">
            Что думает народ? Анализ комментариев из {activeChannels.length} Telegram-каналов
          </p>
        </div>

        {/* ── Stats Row ── */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          <StatCard
            icon="💬" label="Комментариев"
            value={stats?.total_comments?.toLocaleString() || "0"}
            sub={`за ${stats?.period_days || 7} дней`}
          />
          <StatCard
            icon="👤" label="Авторов"
            value={stats?.unique_authors?.toLocaleString() || "0"}
            sub="уникальных"
          />
          <StatCard
            icon="🤖" label="Бот-комментов"
            value={stats?.bot_comments?.toLocaleString() || "0"}
            sub={stats && stats.total_comments > 0
              ? `${((stats.bot_comments / stats.total_comments) * 100).toFixed(1)}% от общего`
              : "отфильтровано"}
          />
          <StatCard
            icon="📡" label="Каналов"
            value={activeChannels.length}
            sub={`${withDiscussion.length} с обсуждениями`}
          />
        </div>

        {/* ── Country Cards ── */}
        {countries.length > 0 && (
          <section className="mb-8">
            <SectionHeader icon="🌡️" title="Народная температура" />
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-4 mt-4">
              {countries.map(c => (
                <VoxCountryCard key={c.code} c={c} />
              ))}
            </div>
          </section>
        )}

        {/* ── Elite Gap Table ── */}
        <section className="mb-8">
          <SectionHeader icon="⚖️" title="Elite Gap" />
          <InfoPopover title="Elite Gap">
            Разница между медийной температурой (что пишут СМИ) и народной (что думают комментаторы). Положительное значение = медиа теплее к России чем народ. Отрицательное = народ теплее.
          </InfoPopover>
          <div className="mt-4 bg-zinc-900/40 border border-zinc-800 rounded-xl p-4">
            {eliteGap.length > 0 ? (
              <div className="space-y-1">
                {eliteGap.map(c => (
                  <EliteGapRow key={c.code} c={c} />
                ))}
              </div>
            ) : (
              <div className="text-center py-12 text-zinc-500">
                <div className="text-4xl mb-3">📊</div>
                <p className="text-lg">Данные собираются...</p>
                <p className="text-sm mt-1">
                  Комментарии уже поступают. Elite Gap появится после первого цикла анализа.
                </p>
              </div>
            )}
          </div>
        </section>

        {/* ── Articles Feed ── */}
        <section className="mb-8">
          <SectionHeader icon="📰" title="Лента статей" />
          <div className="mt-4">
            {/* Filters */}
            <div className="flex flex-wrap items-center gap-2 mb-4">
              <select
                value={feedCountry}
                onChange={e => { setFeedCountry(e.target.value); loadArticles(e.target.value, feedType, feedSearch); }}
                className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-white focus:border-cyan-500 focus:outline-none"
              >
                <option value="">Все страны</option>
                {Object.entries(COUNTRY_NAMES).map(([code, name]) => (
                  <option key={code} value={code}>{FLAG[code]} {name}</option>
                ))}
              </select>
              <select
                value={feedType}
                onChange={e => { setFeedType(e.target.value); loadArticles(feedCountry, e.target.value, feedSearch); }}
                className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-white focus:border-cyan-500 focus:outline-none"
              >
                <option value="">Все типы</option>
                <option value="telegram">📱 Telegram</option>
                <option value="rss">📡 RSS</option>
                <option value="web">🌐 Web</option>
              </select>
              <div className="flex-1 min-w-[200px]">
                <input
                  type="text"
                  placeholder="🔍 Поиск по заголовку..."
                  value={feedSearch}
                  onChange={e => setFeedSearch(e.target.value)}
                  onKeyDown={e => e.key === "Enter" && loadArticles(feedCountry, feedType, feedSearch)}
                  className="w-full bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-white placeholder-zinc-500 focus:border-cyan-500 focus:outline-none"
                />
              </div>
              <span className="text-xs text-zinc-500">
                {articlesTotal.toLocaleString()} статей за 3 дня
              </span>
            </div>

            {/* Articles List */}
            <div className="space-y-2 max-h-[600px] overflow-y-auto pr-1">
              {feedLoading ? (
                <div className="text-center py-8 text-zinc-500 animate-pulse">Загрузка...</div>
              ) : articles.length === 0 ? (
                <div className="text-center py-8 text-zinc-500">Нет статей по фильтрам</div>
              ) : (
                articles.map(a => (
                  <div
                    key={a.id}
                    className="bg-zinc-900/40 border border-zinc-800 rounded-lg p-3 hover:border-zinc-700 transition-colors cursor-pointer"
                    onClick={() => setExpandedArticle(expandedArticle === a.id ? null : a.id)}
                  >
                    <div className="flex items-start gap-2">
                      <span className="text-sm mt-0.5">{FLAG[a.country_code] || "🏳️"}</span>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 mb-1">
                          <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                            a.source_type === "telegram" ? "bg-cyan-900/50 text-cyan-400" :
                            a.source_type === "rss" ? "bg-orange-900/50 text-orange-400" :
                            "bg-zinc-800 text-zinc-400"
                          }`}>
                            {a.source_type === "telegram" ? "TG" : a.source_type.toUpperCase()}
                          </span>
                          <span className="text-xs text-zinc-500 truncate">{a.source_name}</span>
                          <span className={`text-[10px] px-1.5 py-0.5 rounded-full ${
                            a.tier === "opposition" ? "bg-red-900/40 text-red-400" :
                            a.tier === "state" ? "bg-blue-900/40 text-blue-400" :
                            a.tier === "independent" ? "bg-emerald-900/40 text-emerald-400" :
                            "bg-zinc-800 text-zinc-400"
                          }`}>
                            {a.tier}
                          </span>
                          <span className="text-xs text-zinc-600 ml-auto whitespace-nowrap">
                            {a.published_at ? new Date(a.published_at).toLocaleString("ru", {
                              day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"
                            }) : ""}
                          </span>
                        </div>
                        <h4 className="text-sm text-white font-medium leading-tight line-clamp-2">
                          {a.title || "(без заголовка)"}
                        </h4>
                        {expandedArticle === a.id && (
                          <div className="mt-2">
                            <p className="text-xs text-zinc-400 leading-relaxed whitespace-pre-line">
                              {a.body}
                            </p>
                            {a.url && (
                              <a
                                href={a.url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-block mt-2 text-xs text-cyan-400 hover:text-cyan-300"
                                onClick={e => e.stopPropagation()}
                              >
                                Открыть источник →
                              </a>
                            )}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </section>

        {/* ── Channels ── */}
        <section className="mb-8">
          <div className="flex items-center gap-3 mb-4">
            <SectionHeader icon="📡" title="Каналы мониторинга" />
            <button
              onClick={() => setShowChannels(!showChannels)}
              className="text-xs text-cyan-400 hover:text-cyan-300 transition-colors px-3 py-1 border border-zinc-700 rounded-full"
            >
              {showChannels ? "Скрыть" : `Показать все (${activeChannels.length})`}
            </button>
          </div>
          {showChannels && (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2">
              {activeChannels
                .sort((a, b) => a.country_code.localeCompare(b.country_code))
                .map(ch => (
                  <ChannelCard key={ch.id} ch={ch} />
                ))}
            </div>
          )}
        </section>

        {/* ── How it works ── */}
        <section className="mb-8">
          <div className="bg-zinc-900/30 border border-zinc-800 rounded-xl p-6">
            <h3 className="text-lg font-bold mb-4 flex items-center gap-2">
              🔬 Как это работает
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6 text-sm text-zinc-400">
              <div>
                <div className="text-cyan-400 font-bold mb-1">1. Сбор</div>
                <p>
                  Telegram User API читает комментарии из обсуждений каналов.
                  Каждые 30 мин — новый цикл. Боты фильтруются (bot_score ≥ 0.5).
                </p>
              </div>
              <div>
                <div className="text-cyan-400 font-bold mb-1">2. Анализ</div>
                <p>
                  Claude Haiku оценивает каждый комментарий: sentiment (-1..+1),
                  эмоция, позиция (pro/anti/neutral Russia), темы, bot_score.
                </p>
              </div>
              <div>
                <div className="text-cyan-400 font-bold mb-1">3. Температура</div>
                <p>
                  VOX температура = средний sentiment × 50 + 50 (шкала 0–100°).
                  Elite Gap = медийная температура − народная. τ = 7 дней.
                </p>
              </div>
            </div>
          </div>
        </section>

      </div>
    </div>
  );
}
