"use client";

import { useEffect, useState, useMemo } from "react";
import SectionHeader from "@/components/SectionHeader";
import { glossary } from "@/lib/glossary";
import {
  LineChart, Line, XAxis, YAxis, Tooltip as RechartsTooltip, ResponsiveContainer,
  BarChart, Bar, Cell, CartesianGrid,
} from "recharts";

const API = process.env.NEXT_PUBLIC_API_URL || "";

/* ── Types ── */
interface TierHeadline {
  title: string;
  url: string;
  sentiment: number;
  source: string;
}

interface TierInfo {
  tier: string;
  label: string;
  sentiment: number;
  article_count: number;
  sources: string[];
  headlines: TierHeadline[];
}

interface TiersData {
  country_code: string;
  country_name: string;
  overall_sentiment: number;
  tiers: TierInfo[];
  divergence: number;
}

interface NarrativeXrayExpandedProps {
  code: string;
  days: number;
}

/* ── Constants ── */
const TIER_COLORS: Record<string, string> = {
  official: "#ef4444",
  mainstream: "#3b82f6",
  independent: "#22c55e",
  domestic_opposition: "#f59e0b",
  analytics: "#a855f7",
  western_proxy: "#6b7280",
  social: "#06b6d4",
};

const TOPICS = ["economic", "military", "diplomatic", "cultural", "security"] as const;
const TOPIC_LABELS: Record<string, string> = {
  economic: "Экономика",
  military: "Военные",
  diplomatic: "Дипломатия",
  cultural: "Культура",
  security: "Безопасность",
};
const TOPIC_COLORS: Record<string, string> = {
  economic: "#22c55e",
  military: "#ef4444",
  diplomatic: "#3b82f6",
  cultural: "#a855f7",
  security: "#f59e0b",
};

/* ── Helpers ── */
function sentimentToX(sentiment: number): number {
  return 5 + ((sentiment + 1) / 2) * 90;
}

function sentimentLabel(s: number): string {
  if (s >= 0.3) return "позитивный";
  if (s >= 0.05) return "скорее позитивный";
  if (s > -0.05) return "нейтральный";
  if (s > -0.3) return "скорее негативный";
  return "негативный";
}

function sentimentColor(s: number): string {
  if (s > 0.05) return "text-green-400";
  if (s < -0.05) return "text-red-400";
  return "text-white/40";
}

function sentimentBg(s: number): string {
  if (s >= 0.3) return "rgba(34,197,94,0.6)";
  if (s >= 0.1) return "rgba(34,197,94,0.3)";
  if (s > -0.1) return "rgba(255,255,255,0.06)";
  if (s > -0.3) return "rgba(239,68,68,0.3)";
  return "rgba(239,68,68,0.6)";
}

/* ── Mock data generators ── */
function generateDivergenceTimeline(days: number, currentDivergence: number) {
  const points = [];
  const numPoints = Math.min(days, 14);
  for (let i = numPoints; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    const noise = (Math.random() - 0.5) * 0.3;
    const base = currentDivergence + noise * (i / numPoints);
    points.push({
      date: d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" }),
      divergence: Math.max(0, Math.min(2, parseFloat(base.toFixed(2)))),
    });
  }
  return points;
}

function generateTopicBreakdown() {
  return TOPICS.map((topic) => ({
    topic,
    label: TOPIC_LABELS[topic],
    divergence: parseFloat((Math.random() * 0.8 + 0.1).toFixed(2)),
    color: TOPIC_COLORS[topic],
  })).sort((a, b) => b.divergence - a.divergence);
}

function generateHeatmapData(tiers: TierInfo[], days: number) {
  const numDays = Math.min(days, 7);
  const dayLabels: string[] = [];
  for (let i = numDays - 1; i >= 0; i--) {
    const d = new Date();
    d.setDate(d.getDate() - i);
    dayLabels.push(d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" }));
  }
  return tiers.map((tier) => ({
    tier: tier.tier,
    label: tier.label,
    days: dayLabels.map((day) => ({
      day,
      sentiment: parseFloat((tier.sentiment + (Math.random() - 0.5) * 0.4).toFixed(2)),
    })),
  }));
}

/* ── Component ── */
export default function NarrativeXrayExpanded({ code, days }: NarrativeXrayExpandedProps) {
  const [data, setData] = useState<TiersData | null>(null);
  const [loading, setLoading] = useState(true);
  const [expandedTier, setExpandedTier] = useState<string | null>(null);
  const [compareA, setCompareA] = useState<string | null>(null);
  const [compareB, setCompareB] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"spectrum" | "timeline" | "compare" | "topics" | "heatmap">("spectrum");

  useEffect(() => {
    setLoading(true);
    fetch(`${API}/api/v1/countries/${code}/tiers?days=${days}`)
      .then((r) => r.json())
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [code, days]);

  useEffect(() => {
    if (data && data.tiers.length >= 2) {
      const sorted = [...data.tiers].sort((a, b) => b.sentiment - a.sentiment);
      setCompareA(sorted[0].tier);
      setCompareB(sorted[sorted.length - 1].tier);
    }
  }, [data]);

  const timelineData = useMemo(
    () => (data ? generateDivergenceTimeline(days, data.divergence) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data?.divergence, days]
  );
  const topicData = useMemo(
    () => (data ? generateTopicBreakdown() : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data?.country_code]
  );
  const heatmapData = useMemo(
    () => (data ? generateHeatmapData(data.tiers, days) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data?.country_code, days]
  );

  if (loading) {
    return <div className="h-48 rounded-xl border border-white/[0.06] bg-zinc-950 animate-pulse" />;
  }

  if (!data || data.tiers.length < 2) return null;

  const sorted = [...data.tiers].sort((a, b) => b.sentiment - a.sentiment);
  const mostPositive = sorted[0];
  const mostNegative = sorted[sorted.length - 1];
  const gap = data.divergence;

  const tierA = data.tiers.find((t) => t.tier === compareA);
  const tierB = data.tiers.find((t) => t.tier === compareB);

  const tabs = [
    { key: "spectrum" as const, label: "Спектр", icon: "🎯" },
    { key: "timeline" as const, label: "Динамика", icon: "📈" },
    { key: "compare" as const, label: "Сравнение", icon: "⚖️" },
    { key: "topics" as const, label: "Темы", icon: "📊" },
    { key: "heatmap" as const, label: "Heatmap", icon: "🗓️" },
  ];

  return (
    <div id="narratives" className="space-y-4 scroll-mt-20">
      <SectionHeader
        icon="🔬"
        title="Нарративный расклад"
        description="Расширенный анализ: спектр, динамика, попарное сравнение и тематическая карта"
        infoTitle="Расхождение нарративов"
        infoContent={glossary.divergence?.detail || "Разница в тональности между разными типами СМИ."}
      />

      <div className="rounded-xl border border-white/[0.06] overflow-hidden">
        {/* Divergence headline */}
        <div
          className="px-6 py-4 flex items-center justify-between border-b border-white/[0.04]"
          style={{
            background: gap >= 0.8
              ? "linear-gradient(90deg, rgba(239,68,68,0.06) 0%, transparent 50%, rgba(34,197,94,0.06) 100%)"
              : "transparent",
          }}
        >
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium text-white/70">Расхождение</span>
            <span className={`text-2xl font-bold tabular-nums ${gap >= 0.8 ? "text-red-400" : gap >= 0.4 ? "text-amber-400" : "text-green-400"}`}>
              {gap.toFixed(2)}
            </span>
          </div>
          <div className="text-xs text-white/30">
            {gap >= 0.8 ? "🔴 Сильный раскол" : gap >= 0.4 ? "🟡 Заметное расхождение" : "🟢 Консенсус"}
          </div>
        </div>

        {/* Tab navigation */}
        <div className="px-6 pt-4 pb-2 flex gap-1 overflow-x-auto border-b border-white/[0.04]">
          {tabs.map((tab) => (
            <button
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all whitespace-nowrap ${
                activeTab === tab.key
                  ? "bg-white/10 text-white"
                  : "text-white/40 hover:text-white/60 hover:bg-white/[0.04]"
              }`}
            >
              <span>{tab.icon}</span>
              {tab.label}
            </button>
          ))}
        </div>

        {/* TAB: Spectrum */}
        {activeTab === "spectrum" && (
          <div className="animate-in fade-in duration-300">
            <div className="px-6 py-6">
              <div className="relative h-16 mb-2">
                <div className="absolute top-6 left-0 right-0 h-2 rounded-full bg-gradient-to-r from-red-500/20 via-white/5 to-green-500/20" />
                <div className="absolute top-4 left-1/2 w-px h-6 bg-white/10" />
                {sorted.map((tier) => {
                  const x = sentimentToX(tier.sentiment);
                  const color = TIER_COLORS[tier.tier] || "#888";
                  return (
                    <div
                      key={tier.tier}
                      className="absolute transition-all duration-500 cursor-pointer group/marker"
                      style={{ left: `${x}%`, top: 0, transform: "translateX(-50%)" }}
                      onClick={() => setExpandedTier(expandedTier === tier.tier ? null : tier.tier)}
                    >
                      <div
                        className="w-4 h-4 rounded-full border-2 border-zinc-900 shadow-lg relative top-5 group-hover/marker:scale-125 transition-transform"
                        style={{ backgroundColor: color }}
                      />
                      <div className="absolute top-11 left-1/2 -translate-x-1/2 whitespace-nowrap text-center">
                        <div className="text-[10px] font-medium" style={{ color }}>{tier.label.replace(/^[^\s]+\s/, "")}</div>
                        <div className="text-[10px] text-white/30">{tier.sentiment >= 0 ? "+" : ""}{tier.sentiment.toFixed(2)}</div>
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="flex justify-between mt-10 text-[10px] text-white/20">
                <span>← Негативный</span>
                <span>Нейтральный</span>
                <span>Позитивный →</span>
              </div>
            </div>

            {gap >= 0.3 && (
              <div className="grid grid-cols-2 border-t border-white/[0.04]">
                <div className="p-5 border-r border-white/[0.04]">
                  <div className="flex items-center gap-2 mb-3">
                    <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: TIER_COLORS[mostPositive.tier] }} />
                    <span className="text-xs font-medium text-white/60">{mostPositive.label}</span>
                    <span className="text-xs text-green-400/70 ml-auto">{mostPositive.sentiment >= 0 ? "+" : ""}{mostPositive.sentiment.toFixed(2)}</span>
                  </div>
                  <div className="space-y-2">
                    {(mostPositive.headlines.filter((h) => h.sentiment >= 0).length > 0
                      ? mostPositive.headlines.filter((h) => h.sentiment >= 0)
                      : mostPositive.headlines
                    ).slice(0, 2).map((h, i) => (
                      <a key={i} href={h.url} target="_blank" rel="noopener noreferrer" className="block text-xs text-white/40 hover:text-white/70 transition-colors leading-relaxed line-clamp-2">
                        «{h.title}»
                      </a>
                    ))}
                  </div>
                  <div className="mt-2 text-[10px] text-white/20">{mostPositive.article_count} статей · {mostPositive.sources.length} источн.</div>
                </div>
                <div className="p-5">
                  <div className="flex items-center gap-2 mb-3">
                    <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: TIER_COLORS[mostNegative.tier] }} />
                    <span className="text-xs font-medium text-white/60">{mostNegative.label}</span>
                    <span className="text-xs text-red-400/70 ml-auto">{mostNegative.sentiment >= 0 ? "+" : ""}{mostNegative.sentiment.toFixed(2)}</span>
                  </div>
                  <div className="space-y-2">
                    {(mostNegative.headlines.filter((h) => h.sentiment <= 0).length > 0
                      ? mostNegative.headlines.filter((h) => h.sentiment <= 0)
                      : mostNegative.headlines
                    ).slice(0, 2).map((h, i) => (
                      <a key={i} href={h.url} target="_blank" rel="noopener noreferrer" className="block text-xs text-white/40 hover:text-white/70 transition-colors leading-relaxed line-clamp-2">
                        «{h.title}»
                      </a>
                    ))}
                  </div>
                  <div className="mt-2 text-[10px] text-white/20">{mostNegative.article_count} статей · {mostNegative.sources.length} источн.</div>
                </div>
              </div>
            )}

            {/* Full tier list */}
            <div className="border-t border-white/[0.04]">
              {sorted.map((tier) => (
                <div key={tier.tier} className="border-b border-white/[0.04] last:border-b-0">
                  <button
                    onClick={() => setExpandedTier(expandedTier === tier.tier ? null : tier.tier)}
                    className="w-full px-6 py-3 flex items-center gap-3 hover:bg-white/[0.02] transition-colors"
                  >
                    <div className="w-3 h-3 rounded-full shrink-0" style={{ backgroundColor: TIER_COLORS[tier.tier] }} />
                    <span className="text-sm font-medium text-white/80">{tier.label}</span>
                    <span className={`text-xs ml-1 ${sentimentColor(tier.sentiment)}`}>{tier.sentiment >= 0 ? "+" : ""}{tier.sentiment.toFixed(2)}</span>
                    <span className="text-[10px] text-white/25 ml-auto">{tier.article_count} ст. · {tier.sources.length} ист.</span>
                    <svg className={`w-4 h-4 text-white/30 transition-transform ${expandedTier === tier.tier ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                  </button>
                  {expandedTier === tier.tier && (
                    <div className="px-6 pb-4 animate-in fade-in slide-in-from-top-2 duration-200">
                      <div className="text-[10px] text-white/25 mb-3">{sentimentLabel(tier.sentiment)} · Источники: {tier.sources.join(", ")}</div>
                      <div className="grid gap-2 sm:grid-cols-2">
                        {tier.headlines.map((h, i) => (
                          <a key={i} href={h.url} target="_blank" rel="noopener noreferrer" className="block rounded-lg border border-white/[0.04] p-3 hover:border-white/10 transition-colors">
                            <div className="text-xs text-white/60 line-clamp-2 leading-relaxed">{h.title}</div>
                            <div className="mt-1.5 flex items-center gap-2 text-[10px] text-white/25">
                              <span>{h.source}</span>
                              <span className={h.sentiment > 0 ? "text-green-400/60" : h.sentiment < 0 ? "text-red-400/60" : "text-white/25"}>{h.sentiment >= 0 ? "+" : ""}{h.sentiment.toFixed(2)}</span>
                            </div>
                          </a>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* TAB: Timeline */}
        {activeTab === "timeline" && (
          <div className="px-6 py-6 animate-in fade-in duration-300">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-sm font-medium text-white/70">Динамика расхождения</h3>
                <p className="text-[10px] text-white/30 mt-0.5">Как менялось расхождение нарративов по дням</p>
              </div>
              <div className="text-[10px] text-white/20 px-2 py-1 rounded bg-white/[0.04] border border-white/[0.06]">📊 Мок-данные</div>
            </div>
            <div className="h-52">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={timelineData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                  <XAxis dataKey="date" tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 10 }} axisLine={{ stroke: "rgba(255,255,255,0.06)" }} tickLine={false} />
                  <YAxis domain={[0, 2]} tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 10 }} axisLine={{ stroke: "rgba(255,255,255,0.06)" }} tickLine={false} width={30} />
                  <RechartsTooltip
                    contentStyle={{ backgroundColor: "rgba(24,24,27,0.95)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, fontSize: 12 }}
                    labelStyle={{ color: "rgba(255,255,255,0.5)" }}
                    itemStyle={{ color: "#fff" }}
                  />
                  <Line type="monotone" dataKey="divergence" stroke="#f59e0b" strokeWidth={2} dot={{ fill: "#f59e0b", r: 3, strokeWidth: 0 }} activeDot={{ r: 5, stroke: "#f59e0b", strokeWidth: 2, fill: "#18181b" }} name="Расхождение" />
                </LineChart>
              </ResponsiveContainer>
            </div>
            <div className="flex gap-4 mt-3 text-[10px]">
              <div className="flex items-center gap-1.5"><div className="w-6 h-px bg-green-400/40" /><span className="text-white/30">&lt; 0.4 Консенсус</span></div>
              <div className="flex items-center gap-1.5"><div className="w-6 h-px bg-amber-400/40" /><span className="text-white/30">0.4–0.8 Расхождение</span></div>
              <div className="flex items-center gap-1.5"><div className="w-6 h-px bg-red-400/40" /><span className="text-white/30">&gt; 0.8 Раскол</span></div>
            </div>
          </div>
        )}

        {/* TAB: Compare */}
        {activeTab === "compare" && (
          <div className="px-6 py-6 animate-in fade-in duration-300">
            <h3 className="text-sm font-medium text-white/70 mb-4">Попарное сравнение тиров</h3>
            <div className="flex flex-wrap gap-3 mb-6">
              <div>
                <label className="text-[10px] text-white/30 block mb-1">Тир A</label>
                <div className="flex flex-wrap gap-1">
                  {data.tiers.map((t) => (
                    <button key={t.tier} onClick={() => setCompareA(t.tier)} className={`px-2.5 py-1 rounded-full text-[11px] font-medium transition-all border ${compareA === t.tier ? "border-white/20 bg-white/10 text-white" : "border-transparent text-white/40 hover:text-white/60 hover:bg-white/[0.04]"}`}>
                      <span className="inline-block w-2 h-2 rounded-full mr-1" style={{ backgroundColor: TIER_COLORS[t.tier] }} />
                      {t.label.replace(/^[^\s]+\s/, "")}
                    </button>
                  ))}
                </div>
              </div>
              <div>
                <label className="text-[10px] text-white/30 block mb-1">Тир B</label>
                <div className="flex flex-wrap gap-1">
                  {data.tiers.map((t) => (
                    <button key={t.tier} onClick={() => setCompareB(t.tier)} className={`px-2.5 py-1 rounded-full text-[11px] font-medium transition-all border ${compareB === t.tier ? "border-white/20 bg-white/10 text-white" : "border-transparent text-white/40 hover:text-white/60 hover:bg-white/[0.04]"}`}>
                      <span className="inline-block w-2 h-2 rounded-full mr-1" style={{ backgroundColor: TIER_COLORS[t.tier] }} />
                      {t.label.replace(/^[^\s]+\s/, "")}
                    </button>
                  ))}
                </div>
              </div>
            </div>

            {tierA && tierB && (
              <div className="grid grid-cols-2 gap-4">
                {[tierA, tierB].map((tier) => (
                  <div key={tier.tier} className="rounded-lg border border-white/[0.06] p-4">
                    <div className="flex items-center gap-2 mb-3">
                      <div className="w-3 h-3 rounded-full" style={{ backgroundColor: TIER_COLORS[tier.tier] }} />
                      <span className="text-sm font-semibold text-white/80">{tier.label}</span>
                    </div>
                    <div className="grid grid-cols-2 gap-3 mb-4">
                      <div className="rounded-lg bg-white/[0.03] p-3">
                        <div className="text-[10px] text-white/30 mb-1">Sentiment</div>
                        <div className={`text-lg font-bold tabular-nums ${sentimentColor(tier.sentiment)}`}>{tier.sentiment >= 0 ? "+" : ""}{tier.sentiment.toFixed(2)}</div>
                        <div className="text-[10px] text-white/25">{sentimentLabel(tier.sentiment)}</div>
                      </div>
                      <div className="rounded-lg bg-white/[0.03] p-3">
                        <div className="text-[10px] text-white/30 mb-1">Статьи</div>
                        <div className="text-lg font-bold tabular-nums text-white/80">{tier.article_count}</div>
                        <div className="text-[10px] text-white/25">{tier.sources.length} источн.</div>
                      </div>
                    </div>
                    <div className="text-[10px] text-white/30 mb-2">Топ заголовки</div>
                    <div className="space-y-2">
                      {tier.headlines.slice(0, 4).map((h, i) => (
                        <a key={i} href={h.url} target="_blank" rel="noopener noreferrer" className="block rounded border border-white/[0.04] p-2 hover:border-white/10 transition-colors">
                          <div className="text-[11px] text-white/50 line-clamp-2 leading-relaxed">{h.title}</div>
                          <div className="mt-1 flex items-center gap-2 text-[10px] text-white/20">
                            <span>{h.source}</span>
                            <span className={h.sentiment > 0 ? "text-green-400/60" : h.sentiment < 0 ? "text-red-400/60" : ""}>{h.sentiment >= 0 ? "+" : ""}{h.sentiment.toFixed(2)}</span>
                          </div>
                        </a>
                      ))}
                    </div>
                  </div>
                ))}
                <div className="col-span-2 rounded-lg border border-white/[0.06] bg-white/[0.02] p-4 flex items-center justify-center gap-6">
                  <div className="text-center">
                    <div className="text-[10px] text-white/30 mb-1">Δ Sentiment</div>
                    <div className="text-xl font-bold tabular-nums text-amber-400">{Math.abs(tierA.sentiment - tierB.sentiment).toFixed(2)}</div>
                  </div>
                  <div className="w-px h-10 bg-white/[0.06]" />
                  <div className="text-center">
                    <div className="text-[10px] text-white/30 mb-1">Δ Статьи</div>
                    <div className="text-xl font-bold tabular-nums text-white/60">{Math.abs(tierA.article_count - tierB.article_count)}</div>
                  </div>
                </div>
              </div>
            )}
          </div>
        )}

        {/* TAB: Topics */}
        {activeTab === "topics" && (
          <div className="px-6 py-6 animate-in fade-in duration-300">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-sm font-medium text-white/70">Расхождение по темам</h3>
                <p className="text-[10px] text-white/30 mt-0.5">По каким темам тиры расходятся больше всего</p>
              </div>
              <div className="text-[10px] text-white/20 px-2 py-1 rounded bg-white/[0.04] border border-white/[0.06]">📊 Мок-данные</div>
            </div>
            <div className="h-48">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={topicData} layout="vertical" barCategoryGap="20%">
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" horizontal={false} />
                  <XAxis type="number" domain={[0, 1]} tick={{ fill: "rgba(255,255,255,0.3)", fontSize: 10 }} axisLine={{ stroke: "rgba(255,255,255,0.06)" }} tickLine={false} />
                  <YAxis type="category" dataKey="label" tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 11 }} axisLine={false} tickLine={false} width={85} />
                  <RechartsTooltip
                    contentStyle={{ backgroundColor: "rgba(24,24,27,0.95)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, fontSize: 12 }}
                    labelStyle={{ color: "rgba(255,255,255,0.5)" }}
                    itemStyle={{ color: "#fff" }}
                  />
                  <Bar dataKey="divergence" radius={[0, 4, 4, 0]}>
                    {topicData.map((entry, idx) => (
                      <Cell key={idx} fill={entry.color} fillOpacity={0.7} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="grid grid-cols-5 gap-2 mt-4">
              {topicData.map((t) => (
                <div key={t.topic} className="rounded-lg border border-white/[0.04] p-3 text-center" style={{ borderColor: t.color + "20" }}>
                  <div className="text-lg font-bold tabular-nums" style={{ color: t.color }}>{t.divergence.toFixed(2)}</div>
                  <div className="text-[10px] text-white/40 mt-1">{t.label}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* TAB: Heatmap */}
        {activeTab === "heatmap" && (
          <div className="px-6 py-6 animate-in fade-in duration-300">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h3 className="text-sm font-medium text-white/70">Sentiment Heatmap</h3>
                <p className="text-[10px] text-white/30 mt-0.5">Тональность по тирам и дням</p>
              </div>
              <div className="text-[10px] text-white/20 px-2 py-1 rounded bg-white/[0.04] border border-white/[0.06]">📊 Мок-данные</div>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr>
                    <th className="text-left text-[10px] text-white/30 pb-2 pr-3 min-w-[120px]">Тир</th>
                    {heatmapData[0]?.days.map((d) => (
                      <th key={d.day} className="text-center text-[10px] text-white/30 pb-2 px-1 min-w-[48px]">{d.day}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {heatmapData.map((row) => (
                    <tr key={row.tier}>
                      <td className="py-1 pr-3">
                        <div className="flex items-center gap-2">
                          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: TIER_COLORS[row.tier] }} />
                          <span className="text-[11px] text-white/60 whitespace-nowrap">{row.label.replace(/^[^\s]+\s/, "")}</span>
                        </div>
                      </td>
                      {row.days.map((d) => (
                        <td key={d.day} className="py-1 px-1">
                          <div
                            className="rounded h-8 flex items-center justify-center text-[10px] font-medium tabular-nums transition-all hover:scale-110 cursor-default"
                            style={{ backgroundColor: sentimentBg(d.sentiment) }}
                            title={`${row.label} · ${d.day} · ${d.sentiment.toFixed(2)}`}
                          >
                            <span className="text-white/70">{d.sentiment >= 0 ? "+" : ""}{d.sentiment.toFixed(1)}</span>
                          </div>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="flex items-center gap-3 mt-4 text-[10px] text-white/30">
              <span>Шкала:</span>
              <div className="flex items-center gap-1"><div className="w-4 h-3 rounded" style={{ backgroundColor: "rgba(239,68,68,0.6)" }} /><span>Негативный</span></div>
              <div className="flex items-center gap-1"><div className="w-4 h-3 rounded" style={{ backgroundColor: "rgba(239,68,68,0.3)" }} /><span>Скорее −</span></div>
              <div className="flex items-center gap-1"><div className="w-4 h-3 rounded" style={{ backgroundColor: "rgba(255,255,255,0.06)" }} /><span>Нейтральный</span></div>
              <div className="flex items-center gap-1"><div className="w-4 h-3 rounded" style={{ backgroundColor: "rgba(34,197,94,0.3)" }} /><span>Скорее +</span></div>
              <div className="flex items-center gap-1"><div className="w-4 h-3 rounded" style={{ backgroundColor: "rgba(34,197,94,0.6)" }} /><span>Позитивный</span></div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
