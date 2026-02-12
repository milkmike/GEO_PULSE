"use client";

import { useEffect, useState } from "react";
import { Card, CardContent } from "@/components/ui/card";
import SectionHeader from "@/components/SectionHeader";
import { glossary } from "@/lib/glossary";
import { API_URL } from "@/lib/api";

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

interface NarrativeXrayProps {
  code: string;
  days: number;
}

const TIER_COLORS: Record<string, string> = {
  official: "#ef4444",
  mainstream: "#3b82f6",
  independent: "#22c55e",
  domestic_opposition: "#f59e0b",
  analytics: "#a855f7",
  western_proxy: "#6b7280",
  social: "#06b6d4",
};

function sentimentToX(sentiment: number): number {
  // Map -1..+1 to 5%..95%
  return 5 + ((sentiment + 1) / 2) * 90;
}

function sentimentLabel(s: number): string {
  if (s >= 0.3) return "позитивный";
  if (s >= 0.05) return "скорее позитивный";
  if (s > -0.05) return "нейтральный";
  if (s > -0.3) return "скорее негативный";
  return "негативный";
}

export default function NarrativeXray({ code, days }: NarrativeXrayProps) {
  const [data, setData] = useState<TiersData | null>(null);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    fetch(`${API_URL}/api/v1/countries/${code}/tiers?days=${days}`)
      .then((r) => r.json())
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [code, days]);

  if (loading) {
    return (
      <div className="h-48 rounded-xl border border-border bg-card animate-pulse" />
    );
  }

  if (!data || data.tiers.length < 2) return null;

  const sorted = [...data.tiers].sort((a, b) => b.sentiment - a.sentiment);
  const mostPositive = sorted[0];
  const mostNegative = sorted[sorted.length - 1];
  const gap = data.divergence;

  return (
    <div id="narratives" className="space-y-4 scroll-mt-20">
      <SectionHeader
        icon="🔬"
        title="Нарративный расклад"
        description="Как разные типы источников видят эту страну — от официоза до оппозиции"
        infoTitle="Расхождение нарративов"
        infoContent={glossary.divergence?.detail || "Разница в тональности между разными типами СМИ."}
      />

      {/* Divergence headline */}
      <div className="rounded-xl border border-white/[0.06] overflow-hidden">
        {/* Top bar with divergence score */}
        <div className="px-6 py-4 flex items-center justify-between border-b border-white/[0.04]"
          style={{
            background: gap >= 0.8
              ? "linear-gradient(90deg, rgba(239,68,68,0.06) 0%, transparent 50%, rgba(34,197,94,0.06) 100%)"
              : "transparent",
          }}
        >
          <div className="flex items-center gap-3">
            <span className="text-sm font-medium text-white/70">Расхождение</span>
            <span
              className={`text-2xl font-bold tabular-nums ${
                gap >= 0.8 ? "text-red-400" : gap >= 0.4 ? "text-amber-400" : "text-green-400"
              }`}
            >
              {gap.toFixed(2)}
            </span>
          </div>
          <div className="text-xs text-white/30">
            {gap >= 0.8
              ? "🔴 Сильный раскол"
              : gap >= 0.4
              ? "🟡 Заметное расхождение"
              : "🟢 Консенсус"}
          </div>
        </div>

        {/* Sentiment spectrum */}
        <div className="px-6 py-6">
          {/* Scale */}
          <div className="relative h-16 mb-2">
            {/* Background bar */}
            <div className="absolute top-6 left-0 right-0 h-2 rounded-full bg-gradient-to-r from-red-500/20 via-white/5 to-green-500/20" />
            
            {/* Center line */}
            <div className="absolute top-4 left-1/2 w-px h-6 bg-white/10" />

            {/* Tier markers */}
            {sorted.map((tier) => {
              const x = sentimentToX(tier.sentiment);
              const color = TIER_COLORS[tier.tier] || "#888";
              return (
                <div
                  key={tier.tier}
                  className="absolute transition-all duration-500 cursor-pointer group/marker"
                  style={{ left: `${x}%`, top: 0, transform: "translateX(-50%)" }}
                  onClick={() => setExpanded(expanded === tier.tier ? null : tier.tier)}
                >
                  {/* Dot */}
                  <div
                    className="w-4 h-4 rounded-full border-2 border-zinc-900 shadow-lg relative top-5 group-hover/marker:scale-125 transition-transform"
                    style={{ backgroundColor: color }}
                  />
                  {/* Label */}
                  <div className="absolute top-11 left-1/2 -translate-x-1/2 whitespace-nowrap text-center">
                    <div className="text-[10px] font-medium" style={{ color }}>{tier.label.replace(/^[^\s]+\s/, "")}</div>
                    <div className="text-[10px] text-white/30">{tier.sentiment >= 0 ? "+" : ""}{tier.sentiment.toFixed(2)}</div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Scale labels */}
          <div className="flex justify-between mt-10 text-[10px] text-white/20">
            <span>← Негативный</span>
            <span>Нейтральный</span>
            <span>Позитивный →</span>
          </div>
        </div>

        {/* Split view: most positive vs most negative */}
        {gap >= 0.3 && (
          <div className="grid grid-cols-2 border-t border-white/[0.04]">
            {/* Positive side */}
            <div className="p-5 border-r border-white/[0.04]">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: TIER_COLORS[mostPositive.tier] }} />
                <span className="text-xs font-medium text-white/60">{mostPositive.label}</span>
                <span className="text-xs text-green-400/70 ml-auto">{mostPositive.sentiment >= 0 ? "+" : ""}{mostPositive.sentiment.toFixed(2)}</span>
              </div>
              <div className="space-y-2">
                {mostPositive.headlines.filter(h => h.sentiment >= 0).slice(0, 2).map((h, i) => (
                  <a
                    key={i}
                    href={h.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block text-xs text-white/40 hover:text-white/70 transition-colors leading-relaxed line-clamp-2"
                  >
                    «{h.title}»
                  </a>
                ))}
                {mostPositive.headlines.filter(h => h.sentiment >= 0).length === 0 && mostPositive.headlines.slice(0, 2).map((h, i) => (
                  <a
                    key={i}
                    href={h.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block text-xs text-white/40 hover:text-white/70 transition-colors leading-relaxed line-clamp-2"
                  >
                    «{h.title}»
                  </a>
                ))}
              </div>
              <div className="mt-2 text-[10px] text-white/20">{mostPositive.article_count} статей · {mostPositive.sources.length} источн.</div>
            </div>

            {/* Negative side */}
            <div className="p-5">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: TIER_COLORS[mostNegative.tier] }} />
                <span className="text-xs font-medium text-white/60">{mostNegative.label}</span>
                <span className="text-xs text-red-400/70 ml-auto">{mostNegative.sentiment >= 0 ? "+" : ""}{mostNegative.sentiment.toFixed(2)}</span>
              </div>
              <div className="space-y-2">
                {mostNegative.headlines.filter(h => h.sentiment <= 0).slice(0, 2).map((h, i) => (
                  <a
                    key={i}
                    href={h.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block text-xs text-white/40 hover:text-white/70 transition-colors leading-relaxed line-clamp-2"
                  >
                    «{h.title}»
                  </a>
                ))}
                {mostNegative.headlines.filter(h => h.sentiment <= 0).length === 0 && mostNegative.headlines.slice(0, 2).map((h, i) => (
                  <a
                    key={i}
                    href={h.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block text-xs text-white/40 hover:text-white/70 transition-colors leading-relaxed line-clamp-2"
                  >
                    «{h.title}»
                  </a>
                ))}
              </div>
              <div className="mt-2 text-[10px] text-white/20">{mostNegative.article_count} статей · {mostNegative.sources.length} источн.</div>
            </div>
          </div>
        )}

        {/* Expanded tier detail */}
        {expanded && (() => {
          const tier = data.tiers.find((t) => t.tier === expanded);
          if (!tier) return null;
          return (
            <div className="border-t border-white/[0.04] px-6 py-4 animate-in fade-in slide-in-from-top-2 duration-200">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-3 h-3 rounded-full" style={{ backgroundColor: TIER_COLORS[tier.tier] }} />
                <span className="text-sm font-medium">{tier.label}</span>
                <span className="text-xs text-white/30 ml-2">{sentimentLabel(tier.sentiment)}</span>
              </div>
              <div className="grid gap-2 sm:grid-cols-2">
                {tier.headlines.map((h, i) => (
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
                      <span
                        className={h.sentiment > 0 ? "text-green-400/60" : h.sentiment < 0 ? "text-red-400/60" : "text-white/25"}
                      >
                        {h.sentiment >= 0 ? "+" : ""}{h.sentiment.toFixed(2)}
                      </span>
                    </div>
                  </a>
                ))}
              </div>
              <div className="mt-3 text-[10px] text-white/20">
                Источники: {tier.sources.join(", ")}
              </div>
            </div>
          );
        })()}
      </div>
    </div>
  );
}
