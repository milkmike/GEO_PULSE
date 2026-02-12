"use client";

import { useEffect, useState } from "react";
import { COUNTRY_NAMES } from "@/lib/api";
import {
  getVoxOverview,
  getVoxChannels,
  getEliteGap,
  getVoxInsights,
  type VoxOverview,
  type VoxChannel,
  type EliteGapCountry,
  type VoxInsights,
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
  const [insights, setInsights] = useState<VoxInsights | null>(null);
  const [insightsCountry, setInsightsCountry] = useState<string>("");
  const [insightsLoading, setInsightsLoading] = useState(false);

  const loadInsights = (country?: string) => {
    setInsightsLoading(true);
    getVoxInsights({ country: country || undefined, days: 999 })
      .then(res => { setInsights(res); setInsightsLoading(false); })
      .catch(() => setInsightsLoading(false));
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
    loadInsights();
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

        {/* ── VOX Insights ── */}
        <section className="mb-8">
          <SectionHeader icon="🧠" title="Анализ комментариев" />
          <div className="mt-4">
            <div className="flex items-center gap-2 mb-4">
              <select
                value={insightsCountry}
                onChange={e => { setInsightsCountry(e.target.value); loadInsights(e.target.value); }}
                className="bg-zinc-900 border border-zinc-700 rounded-lg px-3 py-1.5 text-sm text-white focus:border-cyan-500 focus:outline-none"
              >
                <option value="">Все страны</option>
                {Object.entries(COUNTRY_NAMES).map(([code, name]) => (
                  <option key={code} value={code}>{FLAG[code]} {name}</option>
                ))}
              </select>
              {insights && (
                <span className="text-xs text-zinc-500">
                  {insights.total_analyzed} / {insights.total_comments} проанализировано
                </span>
              )}
            </div>

            {insightsLoading ? (
              <div className="text-center py-8 text-zinc-500 animate-pulse">Загрузка...</div>
            ) : !insights || insights.total_analyzed === 0 ? (
              <div className="text-center py-12 text-zinc-500">
                <div className="text-4xl mb-3">🧠</div>
                <p className="text-lg">Анализ запущен...</p>
                <p className="text-sm mt-1">Комментарии обрабатываются ИИ. Данные появятся после первого цикла.</p>
              </div>
            ) : (
              <div className="space-y-6">
                {/* ── Emotion Clusters ── */}
                <div>
                  <h4 className="text-sm font-medium text-zinc-400 mb-3">Эмоции</h4>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                    {insights.emotions.map(e => {
                      const total = insights.emotions.reduce((s, x) => s + x.count, 0);
                      const pct = total > 0 ? (e.count / total * 100) : 0;
                      return (
                        <div key={e.emotion} className="bg-zinc-900/60 border border-zinc-800 rounded-lg p-3 relative overflow-hidden">
                          <div
                            className="absolute inset-y-0 left-0 bg-gradient-to-r from-cyan-500/10 to-transparent"
                            style={{ width: `${pct}%` }}
                          />
                          <div className="relative">
                            <div className="flex items-center gap-2 mb-1">
                              <span className="text-lg">{EMOTION_EMOJI[e.emotion] || "❓"}</span>
                              <span className="text-sm text-white capitalize">{e.emotion}</span>
                            </div>
                            <div className="flex items-baseline gap-1">
                              <span className="text-lg font-bold text-white">{e.count}</span>
                              <span className="text-xs text-zinc-500">{pct.toFixed(0)}%</span>
                            </div>
                          </div>
                          {/* Sample quotes */}
                          {insights.emotion_samples[e.emotion]?.slice(0, 1).map((s, i) => (
                            <p key={i} className="relative text-[10px] text-zinc-500 mt-2 line-clamp-2 italic">
                              &ldquo;{s.text}&rdquo;
                            </p>
                          ))}
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* ── Sentiment Spectrum ── */}
                <div>
                  <h4 className="text-sm font-medium text-zinc-400 mb-3">Спектр настроений</h4>
                  {(() => {
                    const bucketOrder = ["very_negative", "negative", "neutral", "positive", "very_positive"];
                    const bucketLabels: Record<string, string> = {
                      very_negative: "🔴 Резко негатив",
                      negative: "🟠 Негатив",
                      neutral: "⚪ Нейтрально",
                      positive: "🟢 Позитив",
                      very_positive: "🔵 Резко позитив",
                    };
                    const bucketColors: Record<string, string> = {
                      very_negative: "bg-red-500",
                      negative: "bg-orange-500",
                      neutral: "bg-zinc-500",
                      positive: "bg-emerald-500",
                      very_positive: "bg-cyan-500",
                    };
                    const total = insights.sentiment_buckets.reduce((s, b) => s + b.count, 0);
                    return (
                      <div>
                        {/* Bar */}
                        <div className="flex h-8 rounded-lg overflow-hidden mb-2">
                          {bucketOrder.map(key => {
                            const b = insights.sentiment_buckets.find(x => x.bucket === key);
                            const pct = b && total > 0 ? (b.count / total * 100) : 0;
                            if (pct === 0) return null;
                            return (
                              <div
                                key={key}
                                className={`${bucketColors[key]} flex items-center justify-center text-[10px] font-bold text-white transition-all`}
                                style={{ width: `${pct}%` }}
                                title={`${bucketLabels[key]}: ${b?.count} (${pct.toFixed(0)}%)`}
                              >
                                {pct >= 10 ? `${pct.toFixed(0)}%` : ""}
                              </div>
                            );
                          })}
                        </div>
                        {/* Legend */}
                        <div className="flex flex-wrap gap-3 justify-center">
                          {bucketOrder.map(key => {
                            const b = insights.sentiment_buckets.find(x => x.bucket === key);
                            if (!b) return null;
                            return (
                              <span key={key} className="text-xs text-zinc-500">
                                {bucketLabels[key]}: {b.count}
                              </span>
                            );
                          })}
                        </div>
                      </div>
                    );
                  })()}
                </div>

                {/* ── Stance toward Russia ── */}
                <div>
                  <h4 className="text-sm font-medium text-zinc-400 mb-3">Позиция к России</h4>
                  <div className="flex gap-3">
                    {insights.stances.map(s => {
                      const total = insights.stances.reduce((sum, x) => sum + x.count, 0);
                      const pct = total > 0 ? (s.count / total * 100) : 0;
                      const icon = s.stance === "pro" ? "🇷🇺👍" : s.stance === "anti" ? "🇷🇺👎" : "🤷";
                      const color = s.stance === "pro" ? "border-blue-500/30 bg-blue-900/20" :
                                    s.stance === "anti" ? "border-orange-500/30 bg-orange-900/20" :
                                    "border-zinc-700 bg-zinc-900/40";
                      return (
                        <div key={s.stance} className={`flex-1 border rounded-lg p-3 ${color}`}>
                          <div className="text-center">
                            <span className="text-xl">{icon}</span>
                            <div className="text-lg font-bold text-white mt-1">{pct.toFixed(0)}%</div>
                            <div className="text-xs text-zinc-400 capitalize">{s.stance}</div>
                            <div className="text-xs text-zinc-600">{s.count} комм.</div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>

                {/* ── Language Map ── */}
                <div>
                  <h4 className="text-sm font-medium text-zinc-400 mb-3">🌐 Языковая карта</h4>
                  {(() => {
                    const LANG_NAMES: Record<string, string> = {
                      ru: "🇷🇺 Русский", en: "🇬🇧 English", ro: "🇷🇴 Română",
                      tk: "🇹🇷 Türk", uz: "🇺🇿 O'zbek", kk: "🇰🇿 Қазақ",
                      ky: "🇰🇬 Кыргыз", tg: "🇹🇯 Тоҷикӣ", ka: "🇬🇪 ქართ",
                      hy: "🇦🇲 Հայusage", az: "🇦🇿 Azərbay",
                    };
                    const LANG_COLORS: Record<string, string> = {
                      ru: "bg-blue-500", en: "bg-emerald-500", ro: "bg-yellow-500",
                      tk: "bg-red-500", uz: "bg-cyan-500", kk: "bg-amber-500",
                      tg: "bg-purple-500", ky: "bg-pink-500", ka: "bg-lime-500",
                      hy: "bg-orange-500", az: "bg-teal-500",
                    };
                    const artLangs = insights.article_languages || {};
                    const allCountries = Object.keys(artLangs).sort();
                    if (allCountries.length === 0) return null;
                    return (
                      <div className="space-y-2">
                        {/* Per-country language bars */}
                        {allCountries.map(cc => {
                          const langs = artLangs[cc];
                          const total = Object.values(langs).reduce((s, v) => s + v, 0);
                          if (total === 0) return null;
                          return (
                            <div key={cc} className="flex items-center gap-2">
                              <span className="text-sm w-8">{FLAG[cc] || cc}</span>
                              <div className="flex-1 flex h-5 rounded overflow-hidden">
                                {Object.entries(langs)
                                  .sort((a, b) => b[1] - a[1])
                                  .map(([lang, cnt]) => {
                                    const pct = (cnt / total * 100);
                                    if (pct < 1) return null;
                                    return (
                                      <div
                                        key={lang}
                                        className={`${LANG_COLORS[lang] || "bg-zinc-600"} flex items-center justify-center text-[9px] font-bold text-white`}
                                        style={{ width: `${pct}%` }}
                                        title={`${LANG_NAMES[lang] || lang}: ${cnt} (${pct.toFixed(0)}%)`}
                                      >
                                        {pct >= 15 ? (LANG_NAMES[lang]?.split(" ")[0] || lang) : ""}
                                      </div>
                                    );
                                  })}
                              </div>
                              <span className="text-[10px] text-zinc-600 w-12 text-right">{total.toLocaleString()}</span>
                            </div>
                          );
                        })}
                        {/* Comment languages */}
                        {insights.comment_languages && insights.comment_languages.length > 0 && (
                          <div className="mt-3 pt-3 border-t border-zinc-800">
                            <span className="text-xs text-zinc-500">Языки комментариев: </span>
                            {insights.comment_languages.map(l => (
                              <span key={l.language} className="text-xs text-zinc-400 mr-2">
                                {LANG_NAMES[l.language]?.split(" ")[0] || l.language} {l.count}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    );
                  })()}
                </div>

                {/* ── Topic Cloud ── */}
                <div>
                  <h4 className="text-sm font-medium text-zinc-400 mb-3">Облако тем</h4>
                  <div className="flex flex-wrap gap-2 justify-center">
                    {insights.topics.map(t => {
                      const maxCount = insights.topics[0]?.count || 1;
                      const scale = 0.7 + (t.count / maxCount) * 0.8;
                      const opacity = 0.4 + (t.count / maxCount) * 0.6;
                      return (
                        <span
                          key={t.topic}
                          className="px-2 py-1 rounded-full bg-cyan-900/30 text-cyan-300 border border-cyan-800/30 transition-all hover:bg-cyan-800/40 cursor-default"
                          style={{ fontSize: `${scale}rem`, opacity }}
                          title={`${t.count} упоминаний`}
                        >
                          {t.topic}
                          {t.count > 1 && <sup className="ml-1 text-[9px] text-cyan-500">{t.count}</sup>}
                        </span>
                      );
                    })}
                  </div>
                </div>
              </div>
            )}
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
