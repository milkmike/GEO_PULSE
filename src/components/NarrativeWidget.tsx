"use client";

import { useEffect, useState, useMemo } from "react";
import SectionHeader from "@/components/SectionHeader";
import { glossary } from "@/lib/glossary";
import {
  LineChart, Line, XAxis, YAxis, Tooltip as RechartsTooltip, ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import { API_URL } from "@/lib/api";

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
  low_data: boolean;
}

interface TiersData {
  country_code: string;
  country_name: string;
  overall_sentiment: number;
  tiers: TierInfo[];
  divergence: number;
}

interface NarrativeTopic {
  topic: string;
  label: string;
  divergence: number;
  most_positive_tier: string;
  most_negative_tier: string;
  tiers: {
    tier: string;
    label: string;
    sentiment: number;
    article_count: number;
    headline: TierHeadline | null;
  }[];
}

interface NarrativeData {
  country_code: string;
  topics: NarrativeTopic[];
}

interface TimelinePoint {
  date: string;
  divergence: number;
}

interface Props {
  code: string;
  days: number;
}

/* ── Constants ── */
const TIER_COLORS: Record<string, string> = {
  official: "#ef4444",
  state: "#dc2626",
  mainstream: "#3b82f6",
  independent: "#10b981",
  analytics: "#a855f7",
  social: "#06b6d4",
  opposition: "#f59e0b",
  western_proxy: "#6b7280",
  domestic_opposition: "#eab308",
};

/* ── Helpers ── */
function sentimentToX(s: number): number {
  return 5 + ((Math.max(-2, Math.min(2, s)) + 2) / 4) * 90;
}

function sentimentColor(s: number): string {
  if (s > 0.05) return "text-green-400";
  if (s < -0.05) return "text-red-400";
  return "text-white/40";
}

function verdictText(gap: number, tiers: TierInfo[]): string {
  const reliable = tiers.filter(t => !t.low_data);
  if (reliable.length < 2) return "Недостаточно данных для оценки расхождений";
  
  const sorted = [...reliable].sort((a, b) => b.sentiment - a.sentiment);
  const top = sorted[0];
  const bottom = sorted[sorted.length - 1];
  
  const topName = top.label.replace(/^[^\s]+\s/, "");
  const bottomName = bottom.label.replace(/^[^\s]+\s/, "");
  
  if (gap >= 0.8) {
    return `Сильный раскол: ${topName} (${top.sentiment >= 0 ? "+" : ""}${top.sentiment.toFixed(1)}) vs ${bottomName} (${bottom.sentiment >= 0 ? "+" : ""}${bottom.sentiment.toFixed(1)})`;
  }
  if (gap >= 0.4) {
    return `Заметное расхождение между ${topName} и ${bottomName}`;
  }
  return `Относительный консенсус — тиры близки по тональности`;
}

/* ── Component ── */
export default function NarrativeWidget({ code, days }: Props) {
  const [data, setData] = useState<TiersData | null>(null);
  const [narrativeData, setNarrativeData] = useState<NarrativeData | null>(null);
  const [timelineData, setTimelineData] = useState<TimelinePoint[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(false);
  const [expandedTier, setExpandedTier] = useState<string | null>(null);

  // Fetch all data
  useEffect(() => {
    setLoading(true);
    const clamp = Math.min(days, 365);
    
    Promise.all([
      fetch(`${API_URL}/api/v1/countries/${code}/tiers?days=${clamp}`).then(r => r.json()).catch(() => null),
      fetch(`${API_URL}/api/v1/countries/${code}/tiers/narrative?days=${clamp}`).then(r => r.json()).catch(() => null),
      fetch(`${API_URL}/api/v1/countries/${code}/divergence/history?days=${clamp}`).then(r => r.json()).catch(() => null),
    ]).then(([tiers, narrative, timeline]) => {
      setData(tiers);
      setNarrativeData(narrative);
      if (timeline?.data) {
        setTimelineData(timeline.data.map((d: { date: string; divergence: number }) => ({
          date: new Date(d.date).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" }),
          divergence: d.divergence,
        })));
      }
      setLoading(false);
    });
  }, [code, days]);

  const sorted = useMemo(() => {
    if (!data?.tiers) return [];
    return [...data.tiers].sort((a, b) => b.sentiment - a.sentiment);
  }, [data]);

  const topNarratives = useMemo(() => {
    if (!narrativeData?.topics) return [];
    return narrativeData.topics.filter(t => t.divergence >= 0.3).slice(0, 3);
  }, [narrativeData]);

  if (loading) {
    return <div className="h-48 rounded-xl border border-white/[0.06] bg-zinc-950 animate-pulse" />;
  }

  if (!data || !data.tiers || data.tiers.length < 2) return null;

  const gap = data.divergence;
  const reliable = data.tiers.filter(t => !t.low_data);

  return (
    <div id="narratives" className="space-y-4 scroll-mt-20">
      <SectionHeader
        icon="🔬"
        title="Нарративный расклад"
        description="Как разные типы источников освещают события — от официоза до оппозиции"
        infoTitle="Расхождение нарративов"
        infoContent={glossary.divergence?.detail || "Разница в тональности между типами СМИ. Считается только по тирам с ≥3 статей."}
      />

      <div className="rounded-xl border border-white/[0.06] overflow-hidden">
        
        {/* ① VERDICT + DIVERGENCE SCORE */}
        <div className="px-6 py-5"
          style={{
            background: gap >= 0.8
              ? "linear-gradient(135deg, rgba(239,68,68,0.04) 0%, transparent 50%, rgba(34,197,94,0.04) 100%)"
              : "transparent",
          }}
        >
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <p className="text-sm text-white/80 leading-relaxed">
                {verdictText(gap, data.tiers)}
              </p>
              <p className="text-[11px] text-white/30 mt-1">
                {reliable.length} тиров · {data.tiers.reduce((s, t) => s + t.article_count, 0)} статей за {days} дн.
                {data.tiers.some(t => t.low_data) && (
                  <span className="ml-2 text-amber-400/50">⚠ {data.tiers.filter(t => t.low_data).length} тир(а) с малым кол-вом данных</span>
                )}
              </p>
            </div>
            <div className="text-right shrink-0">
              <div className={`text-3xl font-bold tabular-nums ${
                gap >= 0.8 ? "text-red-400" : gap >= 0.4 ? "text-amber-400" : "text-green-400"
              }`}>
                {gap.toFixed(2)}
              </div>
              <div className="text-[10px] text-white/25 mt-0.5">расхождение</div>
            </div>
          </div>
        </div>

        {/* ② SPECTRUM BAR */}
        <div className="px-6 py-5 border-t border-white/[0.04]">
          <div className="relative h-14 mb-2">
            {/* Background gradient bar */}
            <div className="absolute top-6 left-0 right-0 h-2 rounded-full bg-gradient-to-r from-red-500/20 via-white/5 to-green-500/20" />
            {/* Center line */}
            <div className="absolute top-4 left-1/2 w-px h-6 bg-white/10" />

            {/* Tier dots */}
            {sorted.map((tier) => {
              const x = sentimentToX(tier.sentiment);
              const color = TIER_COLORS[tier.tier] || "#888";
              return (
                <div
                  key={tier.tier}
                  className={`absolute transition-all duration-500 cursor-pointer group/m ${tier.low_data ? "opacity-40" : ""}`}
                  style={{ left: `${x}%`, top: 0, transform: "translateX(-50%)" }}
                  onClick={() => setExpandedTier(expandedTier === tier.tier ? null : tier.tier)}
                >
                  <div
                    className="w-4 h-4 rounded-full border-2 border-zinc-900 shadow-lg relative top-5 group-hover/m:scale-125 transition-transform"
                    style={{ backgroundColor: color }}
                  />
                  <div className="absolute top-11 left-1/2 -translate-x-1/2 whitespace-nowrap text-center">
                    <div className="text-[10px] font-medium" style={{ color }}>
                      {tier.label.replace(/^[^\s]+\s/, "")}
                    </div>
                    <div className="text-[10px] text-white/30">
                      {tier.sentiment >= 0 ? "+" : ""}{tier.sentiment.toFixed(2)}
                      {tier.low_data && " ⚠"}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
          <div className="flex justify-between mt-10 text-[10px] text-white/20">
            <span>← Негативный</span>
            <span>0</span>
            <span>Позитивный →</span>
          </div>
        </div>

        {/* Expanded tier detail (on click) */}
        {expandedTier && (() => {
          const tier = data.tiers.find(t => t.tier === expandedTier);
          if (!tier) return null;
          return (
            <div className="border-t border-white/[0.04] px-6 py-4 animate-in fade-in slide-in-from-top-2 duration-200 bg-white/[0.01]">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-3 h-3 rounded-full" style={{ backgroundColor: TIER_COLORS[tier.tier] }} />
                <span className="text-sm font-medium">{tier.label}</span>
                <span className={`text-xs ${sentimentColor(tier.sentiment)}`}>
                  {tier.sentiment >= 0 ? "+" : ""}{tier.sentiment.toFixed(2)}
                </span>
                <span className="text-[10px] text-white/20 ml-auto">{tier.article_count} ст. · {tier.sources.length} ист.</span>
                {tier.low_data && <span className="text-[10px] text-amber-400/60">⚠ мало данных</span>}
              </div>
              {tier.headlines && tier.headlines.length > 0 ? (
                <div className="grid gap-2 sm:grid-cols-2">
                  {tier.headlines.slice(0, 4).map((h, i) => (
                    <a
                      key={i}
                      href={h.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block rounded-lg border border-white/[0.04] p-3 hover:border-white/10 transition-colors"
                    >
                      <div className="text-xs text-white/60 line-clamp-2 leading-relaxed">{h.title}</div>
                      <div className="mt-1.5 flex items-center gap-2 text-[10px] text-white/25">
                        <span>{h.source}</span>
                        <span className={h.sentiment > 0 ? "text-green-400/60" : h.sentiment < 0 ? "text-red-400/60" : ""}>
                          {h.sentiment >= 0 ? "+" : ""}{h.sentiment.toFixed(2)}
                        </span>
                      </div>
                    </a>
                  ))}
                </div>
              ) : (
                <div className="text-xs text-white/25">Нет заголовков</div>
              )}
              <div className="mt-2 text-[10px] text-white/15">
                Источники: {tier.sources.join(", ")}
              </div>
            </div>
          );
        })()}

        {/* ③ KEY DIVERGENCES BY TOPIC */}
        {topNarratives.length > 0 && (
          <div className="border-t border-white/[0.04] px-6 py-5">
            <div className="text-xs font-medium text-white/50 mb-3 flex items-center gap-2">
              <span>📌</span> Ключевые расхождения по темам
            </div>
            <div className="space-y-3">
              {topNarratives.map((topic) => {
                const topTier = topic.tiers[0]; // most positive
                const bottomTier = topic.tiers[topic.tiers.length - 1]; // most negative
                return (
                  <div key={topic.topic} className="rounded-lg border border-white/[0.04] overflow-hidden">
                    {/* Topic header */}
                    <div className="px-4 py-2.5 flex items-center justify-between bg-white/[0.02]">
                      <span className="text-xs font-medium text-white/70">{topic.label}</span>
                      <span className={`text-xs font-bold tabular-nums ${
                        topic.divergence >= 0.8 ? "text-red-400/70" : topic.divergence >= 0.4 ? "text-amber-400/70" : "text-green-400/70"
                      }`}>
                        Δ {topic.divergence.toFixed(2)}
                      </span>
                    </div>
                    {/* Two opposing views */}
                    <div className="grid grid-cols-2 divide-x divide-white/[0.04]">
                      {/* Positive side */}
                      <div className="p-3">
                        <div className="flex items-center gap-1.5 mb-2">
                          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: TIER_COLORS[topTier.tier] }} />
                          <span className="text-[10px] text-white/40">{topTier.label.replace(/^[^\s]+\s/, "")}</span>
                          <span className="text-[10px] text-green-400/60 ml-auto">{topTier.sentiment >= 0 ? "+" : ""}{topTier.sentiment.toFixed(2)}</span>
                        </div>
                        {topTier.headline ? (
                          <a href={topTier.headline.url} target="_blank" rel="noopener noreferrer"
                            className="block text-[11px] text-white/50 hover:text-white/70 transition-colors leading-relaxed line-clamp-2">
                            «{topTier.headline.title}»
                          </a>
                        ) : (
                          <div className="text-[11px] text-white/20 italic">нет данных</div>
                        )}
                        <div className="text-[9px] text-white/15 mt-1">{topTier.article_count} ст.</div>
                      </div>
                      {/* Negative side */}
                      <div className="p-3">
                        <div className="flex items-center gap-1.5 mb-2">
                          <div className="w-2 h-2 rounded-full" style={{ backgroundColor: TIER_COLORS[bottomTier.tier] }} />
                          <span className="text-[10px] text-white/40">{bottomTier.label.replace(/^[^\s]+\s/, "")}</span>
                          <span className="text-[10px] text-red-400/60 ml-auto">{bottomTier.sentiment >= 0 ? "+" : ""}{bottomTier.sentiment.toFixed(2)}</span>
                        </div>
                        {bottomTier.headline ? (
                          <a href={bottomTier.headline.url} target="_blank" rel="noopener noreferrer"
                            className="block text-[11px] text-white/50 hover:text-white/70 transition-colors leading-relaxed line-clamp-2">
                            «{bottomTier.headline.title}»
                          </a>
                        ) : (
                          <div className="text-[11px] text-white/20 italic">нет данных</div>
                        )}
                        <div className="text-[9px] text-white/15 mt-1">{bottomTier.article_count} ст.</div>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ④ SPARKLINE TREND */}
        {timelineData.length >= 3 && (
          <div className="border-t border-white/[0.04] px-6 py-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[10px] text-white/30">Динамика расхождения</span>
              <span className="text-[10px] text-white/20">{timelineData.length} дн.</span>
            </div>
            <div className="h-16">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={timelineData}>
                  <XAxis dataKey="date" hide />
                  <YAxis domain={[0, "auto"]} hide />
                  <RechartsTooltip
                    contentStyle={{ backgroundColor: "rgba(24,24,27,0.95)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 8, fontSize: 11 }}
                    labelStyle={{ color: "rgba(255,255,255,0.4)", fontSize: 10 }}
                    itemStyle={{ color: "#fff" }}
                  />
                  <Line
                    type="monotone"
                    dataKey="divergence"
                    stroke="#f59e0b"
                    strokeWidth={1.5}
                    dot={false}
                    activeDot={{ r: 3, stroke: "#f59e0b", strokeWidth: 1.5, fill: "#18181b" }}
                    name="Δ"
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}

        {/* ⑤ EXPAND: Full tier list + details */}
        <div className="border-t border-white/[0.04]">
          <button
            onClick={() => setExpanded(!expanded)}
            className="w-full px-6 py-3 flex items-center justify-center gap-2 text-xs text-white/30 hover:text-white/50 hover:bg-white/[0.02] transition-all"
          >
            <span>{expanded ? "Свернуть" : "📊 Подробнее — все тиры и источники"}</span>
            <svg className={`w-3.5 h-3.5 transition-transform ${expanded ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {expanded && (
            <div className="animate-in fade-in slide-in-from-top-2 duration-300">
              {sorted.map((tier) => (
                <div key={tier.tier} className="border-t border-white/[0.04]">
                  <div className="px-6 py-3 flex items-center gap-3">
                    <div className="w-3 h-3 rounded-full shrink-0" style={{ backgroundColor: TIER_COLORS[tier.tier] }} />
                    <span className="text-sm font-medium text-white/80">{tier.label}</span>
                    <span className={`text-xs tabular-nums ${sentimentColor(tier.sentiment)}`}>
                      {tier.sentiment >= 0 ? "+" : ""}{tier.sentiment.toFixed(2)}
                    </span>
                    {tier.low_data && <span className="text-[10px] text-amber-400/50">⚠ мало данных</span>}
                    <span className="text-[10px] text-white/20 ml-auto">{tier.article_count} ст. · {tier.sources.length} ист.</span>
                  </div>
                  {/* Headlines */}
                  {tier.headlines && tier.headlines.length > 0 && (
                    <div className="px-6 pb-3">
                      <div className="grid gap-1.5 sm:grid-cols-2">
                        {tier.headlines.slice(0, 4).map((h, i) => (
                          <a
                            key={i}
                            href={h.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="flex items-start gap-2 rounded border border-white/[0.03] px-2.5 py-2 hover:border-white/10 transition-colors"
                          >
                            <span className={`text-[10px] tabular-nums shrink-0 mt-0.5 ${h.sentiment > 0 ? "text-green-400/50" : h.sentiment < 0 ? "text-red-400/50" : "text-white/20"}`}>
                              {h.sentiment >= 0 ? "+" : ""}{h.sentiment.toFixed(1)}
                            </span>
                            <div className="min-w-0">
                              <div className="text-[11px] text-white/50 line-clamp-1 leading-relaxed">{h.title}</div>
                              <div className="text-[9px] text-white/15">{h.source}</div>
                            </div>
                          </a>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
