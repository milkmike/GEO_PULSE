"use client";

import { useEffect, useState, useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import SectionHeader from "@/components/SectionHeader";
import { glossary } from "@/lib/glossary";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ScatterChart,
  Scatter,
  Cell,
  ReferenceLine,
  ZAxis,
  LabelList,
} from "recharts";
import {
  getCountries,
  getStats,
  getCoverage,
  getTierDivergence,
  getCountryUNVotes,
  getCountryTrade,
  COUNTRY_CODES,
  COUNTRY_FLAGS,
  COUNTRY_NAMES,
  temperatureColor,
  type Country,
  type Stats,
  type UNVoteYear,
  type TradeYear,
  type CoverageCountry,
  type TierDivergenceCountry,
} from "@/lib/api";

// ── Period selector ─────────────────────────────────────

const PERIODS = [
  { label: "7д", days: 7 },
  { label: "14д", days: 14 },
  { label: "30д", days: 30 },
  { label: "90д", days: 90 },
];

// ── Helpers ─────────────────────────────────────────────

interface CountryAnalytics {
  code: string;
  name: string;
  flag: string;
  temperature: number;
  unData: UNVoteYear[];
  tradeData: TradeYear[];
  lastUN: UNVoteYear | null;
  lastTrade: TradeYear | null;
}

function pctColorClass(pct: number) {
  if (pct >= 75) return "text-green-400";
  if (pct >= 60) return "text-yellow-400";
  return "text-red-400";
}

function fmtBln(v: number) {
  const b = v / 1e9;
  if (b >= 1) return `$${b.toFixed(1)}B`;
  const m = v / 1e6;
  return `$${m.toFixed(0)}M`;
}

function sentimentToColor(s: number, opacity = 1): string {
  // -1..+1 → blue..gray..orange
  if (s < -0.5) return `rgba(239, 68, 68, ${opacity})`; // red
  if (s < -0.2) return `rgba(251, 146, 60, ${opacity})`; // orange
  if (s < 0.2) return `rgba(148, 163, 184, ${opacity})`; // slate
  if (s < 0.5) return `rgba(74, 222, 128, ${opacity})`; // green
  return `rgba(34, 197, 94, ${opacity})`; // bright green
}

function coverageIntensity(count: number, max: number): string {
  if (count === 0) return "bg-zinc-900/50";
  const ratio = Math.min(count / max, 1);
  if (ratio < 0.15) return "bg-emerald-950/60";
  if (ratio < 0.3) return "bg-emerald-900/70";
  if (ratio < 0.5) return "bg-emerald-800/80";
  if (ratio < 0.75) return "bg-emerald-700/80";
  return "bg-emerald-600/90";
}

const TIER_COLORS: Record<string, string> = {
  official: "#3b82f6",
  mainstream: "#a78bfa",
  analytics: "#22d3ee",
  independent: "#34d399",
  domestic_opposition: "#f97316",
  western_proxy: "#ef4444",
  social: "#fbbf24",
};

const TIER_LABELS_SHORT: Record<string, string> = {
  official: "Офиц.",
  mainstream: "Мейнстрим",
  analytics: "Аналитика",
  independent: "Независ.",
  domestic_opposition: "Оппозиция",
  western_proxy: "Запад",
  social: "Соцсети",
};

// ── Page ────────────────────────────────────────────────

export default function AnalyticsPage() {
  const [period, setPeriod] = useState(14);
  const [stats, setStats] = useState<Stats | null>(null);
  const [coverage, setCoverage] = useState<CoverageCountry[]>([]);
  const [divergence, setDivergence] = useState<TierDivergenceCountry[]>([]);
  const [unTradeData, setUnTradeData] = useState<CountryAnalytics[]>([]);
  const [loading, setLoading] = useState(true);

  // Fetch system stats + coverage + divergence when period changes
  useEffect(() => {
    async function fetchAnalytics() {
      setLoading(true);
      try {
        const [statsRes, coverageRes, divergenceRes] = await Promise.all([
          getStats(period),
          getCoverage(Math.min(period, 30)),
          getTierDivergence(period),
        ]);
        setStats(statsRes);
        setCoverage(coverageRes.countries);
        setDivergence(divergenceRes.countries);
      } catch (err) {
        console.error("Failed to fetch analytics:", err);
      } finally {
        setLoading(false);
      }
    }
    fetchAnalytics();
  }, [period]);

  // Fetch UN + Trade data (period-independent, loaded once)
  useEffect(() => {
    async function fetchUNTrade() {
      try {
        const [countriesRes, ...rest] = await Promise.all([
          getCountries(365),
          ...COUNTRY_CODES.flatMap((code) => [
            getCountryUNVotes(code),
            getCountryTrade(code),
          ]),
        ]);
        const countries = countriesRes.countries;
        const analytics: CountryAnalytics[] = COUNTRY_CODES.map((code, i) => {
          const unRes = rest[i * 2] as { data: UNVoteYear[] };
          const tradeRes = rest[i * 2 + 1] as { data: TradeYear[] };
          const country = countries.find((c: Country) => c.code === code);
          const unData = unRes?.data || [];
          const tradeData = tradeRes?.data || [];
          return {
            code,
            name: COUNTRY_NAMES[code],
            flag: COUNTRY_FLAGS[code],
            temperature: country?.temperature ?? 0,
            unData,
            tradeData,
            lastUN: unData.length > 0 ? unData[unData.length - 1] : null,
            lastTrade: tradeData.length > 0 ? tradeData[tradeData.length - 1] : null,
          };
        });
        setUnTradeData(analytics);
      } catch (err) {
        console.error("Failed to fetch UN/trade:", err);
      }
    }
    fetchUNTrade();
  }, []);

  // ── Derived data ──────────────────────────────────────

  // Coverage heatmap: find max for scaling
  const coverageMax = useMemo(() => {
    let max = 1;
    for (const c of coverage) {
      for (const d of c.days) {
        if (d.total > max) max = d.total;
      }
    }
    return max;
  }, [coverage]);

  // All unique dates across coverage data
  const coverageDates = useMemo(() => {
    const dateSet = new Set<string>();
    for (const c of coverage) {
      for (const d of c.days) dateSet.add(d.date);
    }
    return [...dateSet].sort();
  }, [coverage]);

  // Sort coverage by total articles descending
  const coverageSorted = useMemo(() => {
    return [...coverage].sort((a, b) => {
      const aTotal = a.days.reduce((s, d) => s + d.total, 0);
      const bTotal = b.days.reduce((s, d) => s + d.total, 0);
      return bTotal - aTotal;
    });
  }, [coverage]);

  // UN/Trade derived
  const unSorted = useMemo(
    () =>
      [...unTradeData]
        .filter((d) => d.lastUN)
        .sort((a, b) => (b.lastUN?.agreement_pct ?? 0) - (a.lastUN?.agreement_pct ?? 0)),
    [unTradeData]
  );
  const tradeSorted = useMemo(
    () =>
      [...unTradeData]
        .filter((d) => d.lastTrade)
        .sort((a, b) => (b.lastTrade?.total_trade_usd ?? 0) - (a.lastTrade?.total_trade_usd ?? 0)),
    [unTradeData]
  );
  const tradeBarData = useMemo(
    () =>
      tradeSorted.map((d) => ({
        name: `${d.flag} ${d.name}`,
        export: (d.lastTrade?.ru_export_usd ?? 0) / 1e9,
        import: (d.lastTrade?.ru_import_usd ?? 0) / 1e9,
      })),
    [tradeSorted]
  );
  const scatterData = useMemo(
    () =>
      unTradeData
        .filter((d) => d.lastUN)
        .map((d) => ({
          name: d.name,
          flag: d.flag,
          x: d.lastUN!.agreement_pct,
          y: d.temperature,
          code: d.code,
        })),
    [unTradeData]
  );

  // ── Skeleton ──────────────────────────────────────────

  if (loading && !stats) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold">📊 Аналитика</h1>
        <p className="text-muted-foreground">Загрузка данных…</p>
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-24 animate-pulse rounded-lg border border-border bg-card" />
          ))}
        </div>
        <div className="h-64 animate-pulse rounded-lg border border-border bg-card" />
      </div>
    );
  }

  return (
    <div className="space-y-10">
      {/* ── Header + Period Selector ─────────────────── */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold">📊 Аналитика</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Качество данных, расхождение нарративов и верификация через объективные метрики
          </p>
        </div>
        <div className="flex gap-1 rounded-lg border border-border bg-card/50 p-1">
          {PERIODS.map((p) => (
            <button
              key={p.days}
              onClick={() => setPeriod(p.days)}
              className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
                period === p.days
                  ? "bg-white/10 text-white"
                  : "text-muted-foreground hover:text-white/70"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>

      {/* ── 1. System Stats Cards ────────────────────── */}
      {stats && (
        <section className="space-y-3">
          <SectionHeader
            icon="📦"
            title="Состояние системы"
            description="Ключевые метрики работы платформы — сбор, анализ, свежесть данных"
            infoTitle="Состояние системы"
            infoContent={
              <>
                <p>Здесь видно, насколько здорова система сбора и анализа данных.</p>
                <p><strong>Статей</strong> — сколько статей собрано за период. <strong>Проанализировано</strong> — сколько прошли AI-анализ.</p>
                <p><strong>Свежесть</strong> — когда последний раз были новые данные. Если {'>'} 1 часа — возможна проблема с парсерами.</p>
                <p><strong>Дубликаты</strong> — статьи, отфильтрованные как повторы (перепечатки).</p>
              </>
            }
          />
          <div className="grid gap-3 grid-cols-2 lg:grid-cols-5">
            <StatCard
              label="Статей"
              value={stats.total_articles.toLocaleString("ru-RU")}
              sub={`${stats.total_relevant.toLocaleString("ru-RU")} релевантных`}
              accent={stats.total_relevant > 0 ? "green" : "red"}
            />
            <StatCard
              label="Проанализировано"
              value={`${Math.round((stats.total_analyzed / Math.max(stats.total_articles, 1)) * 100)}%`}
              sub={`${stats.total_analyzed.toLocaleString("ru-RU")} из ${stats.total_articles.toLocaleString("ru-RU")}`}
              accent={stats.total_analyzed / Math.max(stats.total_articles, 1) > 0.9 ? "green" : "yellow"}
            />
            <StatCard
              label="Активных источников"
              value={String(stats.active_sources)}
              sub={`${stats.total_duplicates.toLocaleString("ru-RU")} дубликатов`}
              accent="blue"
            />
            <StatCard
              label="Свежесть"
              value={stats.newest_article ? formatTimeSince(stats.newest_article) : "—"}
              sub="последняя статья"
              accent={isRecent(stats.newest_article) ? "green" : "red"}
            />
            <StatCard
              label="Температура"
              value={stats.last_temperature_update ? formatTimeSince(stats.last_temperature_update) : "—"}
              sub="последний пересчёт"
              accent={isRecent(stats.last_temperature_update) ? "green" : "yellow"}
            />
          </div>
        </section>
      )}

      {/* ── 2. Coverage Heatmap ──────────────────────── */}
      {coverageSorted.length > 0 && (
        <section className="space-y-3">
          <SectionHeader
            icon="🗺️"
            title="Покрытие по странам"
            description={glossary.coverage.short}
            infoTitle="Покрытие"
            infoContent={glossary.coverage.detail}
          />
          <Card className="border-border bg-card overflow-x-auto">
            <CardContent className="p-4">
              {/* Date labels */}
              <div className="flex items-end mb-1">
                <div className="w-28 shrink-0" />
                <div className="flex flex-1 gap-px">
                  {coverageDates.map((date, i) => (
                    <div
                      key={date}
                      className="flex-1 text-center text-[9px] text-muted-foreground/40"
                      title={date}
                    >
                      {i % 3 === 0 ? date.slice(5) : ""}
                    </div>
                  ))}
                </div>
                <div className="w-16 shrink-0 text-right text-[10px] text-muted-foreground/50 pl-2">
                  Σ
                </div>
              </div>
              {/* Rows */}
              {coverageSorted.map((country) => {
                const dayMap = new Map(country.days.map((d) => [d.date, d]));
                const totalArticles = country.days.reduce((s, d) => s + d.total, 0);
                const totalRelevant = country.days.reduce((s, d) => s + d.relevant, 0);
                const gapDays = coverageDates.filter((dt) => !dayMap.has(dt)).length;

                return (
                  <div key={country.code} className="flex items-center mb-px">
                    <div className="w-28 shrink-0 flex items-center gap-1.5 pr-2">
                      <span className="text-sm">{COUNTRY_FLAGS[country.code]}</span>
                      <span className="text-xs text-muted-foreground truncate">{country.name}</span>
                    </div>
                    <div className="flex flex-1 gap-px">
                      {coverageDates.map((date) => {
                        const day = dayMap.get(date);
                        const count = day?.total ?? 0;
                        return (
                          <div
                            key={date}
                            className={`flex-1 h-5 rounded-[2px] transition-colors ${coverageIntensity(count, coverageMax)}`}
                            title={`${COUNTRY_NAMES[country.code]} ${date}: ${count} статей (${day?.relevant ?? 0} релевантных)`}
                          />
                        );
                      })}
                    </div>
                    <div className="w-16 shrink-0 text-right pl-2">
                      <span className="text-xs font-medium text-muted-foreground">
                        {totalArticles}
                      </span>
                      {gapDays > 0 && (
                        <span className="text-[9px] text-red-400/70 ml-1" title={`${gapDays} дней без данных`}>
                          −{gapDays}
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
              {/* Legend */}
              <div className="flex items-center gap-3 mt-3 pt-2 border-t border-border/30">
                <span className="text-[10px] text-muted-foreground/50">Мало</span>
                <div className="flex gap-px">
                  {["bg-zinc-900/50", "bg-emerald-950/60", "bg-emerald-900/70", "bg-emerald-800/80", "bg-emerald-700/80", "bg-emerald-600/90"].map((cls, i) => (
                    <div key={i} className={`w-5 h-3 rounded-[2px] ${cls}`} />
                  ))}
                </div>
                <span className="text-[10px] text-muted-foreground/50">Много</span>
                <span className="text-[10px] text-red-400/50 ml-2">−N = дни без данных</span>
              </div>
            </CardContent>
          </Card>
        </section>
      )}

      {/* ── 3. Tier Divergence ───────────────────────── */}
      {divergence.length > 0 && (
        <section className="space-y-3">
          <SectionHeader
            icon="🎭"
            title="Расхождение нарративов"
            description={glossary.divergence.short}
            infoTitle="Расхождение нарративов"
            infoContent={glossary.divergence.detail}
          />
          <div className="grid gap-3 md:grid-cols-2">
            {divergence.map((country) => {
              const maxAbsSent = Math.max(
                ...country.tiers.map((t) => Math.abs(t.sentiment)),
                0.1
              );
              const isHigh = country.divergence >= 0.5;
              const isMedium = country.divergence >= 0.2;

              return (
                <Card
                  key={country.code}
                  className={`border-border bg-card ${
                    isHigh
                      ? "ring-1 ring-red-500/20"
                      : isMedium
                      ? "ring-1 ring-yellow-500/10"
                      : ""
                  }`}
                >
                  <CardContent className="p-4">
                    {/* Country header */}
                    <div className="flex items-center justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <span className="text-lg">{COUNTRY_FLAGS[country.code]}</span>
                        <span className="font-medium text-sm">{country.name}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-muted-foreground">
                          {country.total_articles} ст.
                        </span>
                        <Badge
                          variant="outline"
                          className={`text-[10px] font-mono ${
                            isHigh
                              ? "border-red-500/30 text-red-400"
                              : isMedium
                              ? "border-yellow-500/30 text-yellow-400"
                              : "border-green-500/30 text-green-400"
                          }`}
                        >
                          Δ {country.divergence.toFixed(2)}
                        </Badge>
                      </div>
                    </div>

                    {/* Tier bars */}
                    <div className="space-y-1.5">
                      {country.tiers
                        .sort((a, b) => b.article_count - a.article_count)
                        .map((tier) => {
                          const barWidth = Math.abs(tier.sentiment) / maxAbsSent;
                          const isPositive = tier.sentiment >= 0;
                          const color = TIER_COLORS[tier.tier] || "#94a3b8";

                          return (
                            <div key={tier.tier} className="flex items-center gap-2">
                              <div className="w-20 shrink-0 text-right">
                                <span className="text-[10px] text-muted-foreground">
                                  {TIER_LABELS_SHORT[tier.tier] || tier.tier}
                                </span>
                              </div>
                              {/* Bidirectional bar */}
                              <div className="flex-1 flex items-center">
                                <div className="w-1/2 flex justify-end">
                                  {!isPositive && (
                                    <div
                                      className="h-4 rounded-l-sm"
                                      style={{
                                        width: `${barWidth * 100}%`,
                                        backgroundColor: color,
                                        opacity: 0.7,
                                      }}
                                    />
                                  )}
                                </div>
                                <div className="w-px h-5 bg-white/10 shrink-0" />
                                <div className="w-1/2">
                                  {isPositive && (
                                    <div
                                      className="h-4 rounded-r-sm"
                                      style={{
                                        width: `${barWidth * 100}%`,
                                        backgroundColor: color,
                                        opacity: 0.7,
                                      }}
                                    />
                                  )}
                                </div>
                              </div>
                              <div className="w-20 shrink-0 flex items-center gap-1">
                                <span
                                  className="text-[10px] font-mono"
                                  style={{ color }}
                                >
                                  {tier.sentiment >= 0 ? "+" : ""}
                                  {tier.sentiment.toFixed(2)}
                                </span>
                                <span className="text-[9px] text-muted-foreground/50">
                                  ({tier.article_count})
                                </span>
                              </div>
                            </div>
                          );
                        })}
                    </div>

                    {/* Overall */}
                    <div className="mt-2 pt-2 border-t border-border/30 flex items-center justify-between">
                      <span className="text-[10px] text-muted-foreground/60">
                        Overall: {country.overall_sentiment >= 0 ? "+" : ""}
                        {country.overall_sentiment.toFixed(3)}
                      </span>
                      {isHigh && (
                        <span className="text-[10px] text-red-400/70">
                          ⚠️ Нарративы сильно расходятся
                        </span>
                      )}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>

          {/* Tier legend */}
          <div className="flex flex-wrap gap-x-4 gap-y-1 px-1">
            {Object.entries(TIER_COLORS).map(([tier, color]) => (
              <div key={tier} className="flex items-center gap-1.5">
                <div
                  className="w-2.5 h-2.5 rounded-sm"
                  style={{ backgroundColor: color, opacity: 0.7 }}
                />
                <span className="text-[10px] text-muted-foreground/60">
                  {TIER_LABELS_SHORT[tier] || tier}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── 4. UN Votes ──────────────────────────────── */}
      {unSorted.length > 0 && (
        <section className="space-y-4">
          <SectionHeader
            icon="🗳️"
            title="Голосования в ООН — совпадение с Россией"
            description={glossary.unVotes.short}
            infoTitle="Голосования в ООН"
            infoContent={glossary.unVotes.detail}
          />
          <Card className="border-border bg-card overflow-x-auto">
            <CardContent className="p-0">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="px-4 py-3 font-medium">#</th>
                    <th className="px-4 py-3 font-medium">Страна</th>
                    <th className="px-4 py-3 font-medium text-right">Год</th>
                    <th className="px-4 py-3 font-medium text-right">Совпадение</th>
                    <th className="px-4 py-3 font-medium text-right">Голосований</th>
                    <th className="px-4 py-3 font-medium text-right">Совпали</th>
                    <th className="px-4 py-3 font-medium text-right">Разошлись</th>
                    <th className="px-4 py-3 font-medium text-right">Тренд</th>
                  </tr>
                </thead>
                <tbody>
                  {unSorted.map((d, i) => {
                    const last = d.lastUN!;
                    const prev =
                      d.unData.length >= 2 ? d.unData[d.unData.length - 2] : null;
                    const trend = prev ? last.agreement_pct - prev.agreement_pct : 0;
                    return (
                      <tr
                        key={d.code}
                        className="border-b border-border/50 hover:bg-white/[0.02]"
                      >
                        <td className="px-4 py-3 text-muted-foreground">{i + 1}</td>
                        <td className="px-4 py-3 font-medium">
                          {d.flag} {d.name}
                        </td>
                        <td className="px-4 py-3 text-right text-muted-foreground">
                          {last.year}
                        </td>
                        <td className="px-4 py-3 text-right">
                          <span
                            className={`font-bold ${pctColorClass(last.agreement_pct)}`}
                          >
                            {last.agreement_pct.toFixed(1)}%
                          </span>
                        </td>
                        <td className="px-4 py-3 text-right text-muted-foreground">
                          {last.total_votes}
                        </td>
                        <td className="px-4 py-3 text-right text-green-400">
                          {last.agree_with_russia}
                        </td>
                        <td className="px-4 py-3 text-right text-red-400">
                          {last.disagree_with_russia}
                        </td>
                        <td className="px-4 py-3 text-right">
                          {trend !== 0 ? (
                            <span
                              className={
                                trend > 0 ? "text-green-400" : "text-red-400"
                              }
                            >
                              {trend > 0 ? "↑" : "↓"} {Math.abs(trend).toFixed(1)}%
                            </span>
                          ) : (
                            <span className="text-muted-foreground">→</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </CardContent>
          </Card>
        </section>
      )}

      {/* ── 5. Trade ─────────────────────────────────── */}
      {tradeSorted.length > 0 && (
        <section className="space-y-4">
          <SectionHeader
            icon="💰"
            title="Торговля с Россией"
            description={glossary.trade.short}
            infoTitle="Торговые данные"
            infoContent={glossary.trade.detail}
          />
          <Card className="border-border bg-card overflow-x-auto">
            <CardContent className="p-0">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground">
                    <th className="px-4 py-3 font-medium">#</th>
                    <th className="px-4 py-3 font-medium">Страна</th>
                    <th className="px-4 py-3 font-medium text-right">Год</th>
                    <th className="px-4 py-3 font-medium text-right">Товарооборот</th>
                    <th className="px-4 py-3 font-medium text-right">Экспорт РФ</th>
                    <th className="px-4 py-3 font-medium text-right">Импорт в РФ</th>
                    <th className="px-4 py-3 font-medium text-right">YoY</th>
                  </tr>
                </thead>
                <tbody>
                  {tradeSorted.map((d, i) => {
                    const last = d.lastTrade!;
                    const yoy = last.yoy_change_pct;
                    return (
                      <tr
                        key={d.code}
                        className="border-b border-border/50 hover:bg-white/[0.02]"
                      >
                        <td className="px-4 py-3 text-muted-foreground">{i + 1}</td>
                        <td className="px-4 py-3 font-medium">
                          {d.flag} {d.name}
                        </td>
                        <td className="px-4 py-3 text-right text-muted-foreground">
                          {last.year}
                        </td>
                        <td className="px-4 py-3 text-right font-bold">
                          {fmtBln(last.total_trade_usd)}
                        </td>
                        <td className="px-4 py-3 text-right text-indigo-400">
                          {fmtBln(last.ru_export_usd)}
                        </td>
                        <td className="px-4 py-3 text-right text-teal-400">
                          {fmtBln(last.ru_import_usd)}
                        </td>
                        <td className="px-4 py-3 text-right">
                          {yoy !== null && yoy !== undefined ? (
                            <span
                              className={
                                yoy >= 0 ? "text-green-400" : "text-red-400"
                              }
                            >
                              {yoy >= 0 ? "+" : ""}
                              {yoy.toFixed(1)}%
                            </span>
                          ) : (
                            <span className="text-muted-foreground">—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </CardContent>
          </Card>

          {/* Trade bar chart */}
          <Card className="border-border bg-card">
            <CardHeader>
              <CardTitle className="text-base">
                Товарооборот с Россией (последний год, $B)
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ResponsiveContainer width="100%" height={360}>
                <BarChart
                  data={tradeBarData}
                  layout="vertical"
                  margin={{ top: 0, right: 20, bottom: 0, left: 10 }}
                >
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="rgba(255,255,255,0.04)"
                  />
                  <XAxis
                    type="number"
                    tick={{
                      fill: "rgba(255,255,255,0.5)",
                      fontSize: 11,
                    }}
                    axisLine={false}
                    tickLine={false}
                    tickFormatter={(v) => `$${v}B`}
                  />
                  <YAxis
                    type="category"
                    dataKey="name"
                    tick={{
                      fill: "rgba(255,255,255,0.7)",
                      fontSize: 11,
                    }}
                    axisLine={false}
                    tickLine={false}
                    width={140}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "rgba(15,15,20,0.95)",
                      border: "1px solid rgba(255,255,255,0.1)",
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                    formatter={(
                      value: number | undefined,
                      name: string | undefined
                    ) => [
                      `$${(value ?? 0).toFixed(2)}B`,
                      name === "export" ? "Экспорт РФ" : "Импорт в РФ",
                    ]}
                  />
                  <Legend
                    formatter={(value) =>
                      value === "export" ? "Экспорт РФ" : "Импорт в РФ"
                    }
                    wrapperStyle={{
                      fontSize: 11,
                      color: "rgba(255,255,255,0.6)",
                    }}
                  />
                  <Bar dataKey="export" stackId="trade" fill="#6366f1" />
                  <Bar
                    dataKey="import"
                    stackId="trade"
                    fill="#14b8a6"
                    radius={[0, 4, 4, 0]}
                  />
                </BarChart>
              </ResponsiveContainer>
            </CardContent>
          </Card>
        </section>
      )}

      {/* ── 6. Correlation Scatter ───────────────────── */}
      {scatterData.length > 0 && (
        <section className="space-y-4">
          <SectionHeader
            icon="🔗"
            title="Корреляция: ООН vs Медийная температура"
            description={glossary.correlation.short}
            infoTitle="Корреляция"
            infoContent={glossary.correlation.detail}
          />
          <Card className="border-border bg-card">
            <CardContent className="pt-6">
              <ResponsiveContainer width="100%" height={400}>
                <ScatterChart
                  margin={{ top: 20, right: 20, bottom: 20, left: 20 }}
                >
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke="rgba(255,255,255,0.04)"
                  />
                  <XAxis
                    type="number"
                    dataKey="x"
                    name="UN Agreement"
                    domain={[30, 100]}
                    tick={{
                      fill: "rgba(255,255,255,0.5)",
                      fontSize: 11,
                    }}
                    axisLine={false}
                    tickLine={false}
                    tickFormatter={(v) => `${v}%`}
                    label={{
                      value: "Совпадение с Россией в ООН, %",
                      position: "insideBottom",
                      offset: -10,
                      fill: "rgba(255,255,255,0.4)",
                      fontSize: 12,
                    }}
                  />
                  <YAxis
                    type="number"
                    dataKey="y"
                    name="Temperature"
                    domain={[-50, 50]}
                    tick={{
                      fill: "rgba(255,255,255,0.5)",
                      fontSize: 11,
                    }}
                    axisLine={false}
                    tickLine={false}
                    tickFormatter={(v) => `${v}°`}
                    label={{
                      value: "Медийная температура, °",
                      angle: -90,
                      position: "insideLeft",
                      offset: 10,
                      fill: "rgba(255,255,255,0.4)",
                      fontSize: 12,
                    }}
                  />
                  <ZAxis range={[120, 120]} />
                  <ReferenceLine
                    segment={[
                      { x: 30, y: -50 },
                      { x: 100, y: 50 },
                    ]}
                    stroke="rgba(255,255,255,0.1)"
                    strokeDasharray="6 4"
                  />
                  <ReferenceLine y={0} stroke="rgba(255,255,255,0.08)" />
                  <ReferenceLine x={65} stroke="rgba(255,255,255,0.08)" />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "rgba(15,15,20,0.95)",
                      border: "1px solid rgba(255,255,255,0.1)",
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                    formatter={(
                      value: number | undefined,
                      name: string | undefined
                    ) => {
                      const v = value ?? 0;
                      if (name === "UN Agreement")
                        return [`${v.toFixed(1)}%`, "ООН"];
                      return [`${v.toFixed(1)}°`, "Температура"];
                    }}
                    labelFormatter={() => ""}
                  />
                  <Scatter data={scatterData} shape="circle">
                    {scatterData.map((entry) => (
                      <Cell
                        key={entry.code}
                        fill={temperatureColor(entry.y)}
                      />
                    ))}
                    <LabelList
                      dataKey="flag"
                      position="top"
                      offset={8}
                      style={{ fontSize: 16 }}
                    />
                    <LabelList
                      dataKey="name"
                      position="bottom"
                      offset={8}
                      style={{
                        fontSize: 10,
                        fill: "rgba(255,255,255,0.5)",
                      }}
                    />
                  </Scatter>
                </ScatterChart>
              </ResponsiveContainer>
              <p className="mt-4 text-center text-sm text-muted-foreground">
                Если точки близки к диагонали — наш медийный термометр
                коррелирует с реальным голосованием в ООН
              </p>
            </CardContent>
          </Card>
        </section>
      )}
    </div>
  );
}

// ── Subcomponents ───────────────────────────────────────

function StatCard({
  label,
  value,
  sub,
  accent = "blue",
}: {
  label: string;
  value: string;
  sub: string;
  accent?: "green" | "yellow" | "red" | "blue";
}) {
  const accentColors = {
    green: "border-green-500/20 bg-green-500/5",
    yellow: "border-yellow-500/20 bg-yellow-500/5",
    red: "border-red-500/20 bg-red-500/5",
    blue: "border-blue-500/20 bg-blue-500/5",
  };
  const valueColors = {
    green: "text-green-400",
    yellow: "text-yellow-400",
    red: "text-red-400",
    blue: "text-blue-400",
  };

  return (
    <Card className={`border ${accentColors[accent]}`}>
      <CardContent className="p-3">
        <p className="text-[10px] text-muted-foreground/70 uppercase tracking-wider">
          {label}
        </p>
        <p className={`text-xl font-bold mt-0.5 ${valueColors[accent]}`}>
          {value}
        </p>
        <p className="text-[10px] text-muted-foreground/50 mt-0.5">{sub}</p>
      </CardContent>
    </Card>
  );
}

// ── Time helpers ────────────────────────────────────────

function formatTimeSince(dateStr: string | null): string {
  if (!dateStr) return "—";
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 1) return "только что";
  if (mins < 60) return `${mins} мин назад`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}ч назад`;
  const days = Math.round(hours / 24);
  return `${days}д назад`;
}

function isRecent(dateStr: string | null): boolean {
  if (!dateStr) return false;
  return Date.now() - new Date(dateStr).getTime() < 3600000; // 1 hour
}
