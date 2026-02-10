"use client";

import { useEffect, useState, useMemo } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  getSources,
  COUNTRY_FLAGS,
  COUNTRY_NAMES,
  formatDate,
  type Source,
} from "@/lib/api";

const TIER_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  official: { label: "Официальный", color: "text-red-400", bg: "bg-red-500/15 border-red-500/30" },
  mainstream: { label: "Мейнстрим", color: "text-blue-400", bg: "bg-blue-500/15 border-blue-500/30" },
  independent: { label: "Независимый", color: "text-green-400", bg: "bg-green-500/15 border-green-500/30" },
  domestic_opposition: { label: "Оппозиция", color: "text-yellow-400", bg: "bg-yellow-500/15 border-yellow-500/30" },
  analytics: { label: "Аналитика", color: "text-purple-400", bg: "bg-purple-500/15 border-purple-500/30" },
  western_proxy: { label: "Западный прокси", color: "text-gray-400", bg: "bg-gray-500/15 border-gray-500/30" },
};

const ALL_TIERS = Object.keys(TIER_CONFIG);

function sentimentColor(s: number | null): string {
  if (s === null) return "text-muted-foreground";
  if (s > 0.3) return "text-green-400";
  if (s > -0.3) return "text-yellow-400";
  return "text-red-400";
}

export default function SourcesPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterCountry, setFilterCountry] = useState<string | null>(null);
  const [filterTier, setFilterTier] = useState<string | null>(null);
  const [filterLang, setFilterLang] = useState<string | null>(null);

  useEffect(() => {
    getSources()
      .then((res) => setSources(res.sources))
      .catch((err) => console.error("Failed to fetch sources:", err))
      .finally(() => setLoading(false));
  }, []);

  // Derive unique values
  const countries = useMemo(() => {
    const codes = [...new Set(sources.map((s) => s.country_code))].sort();
    return codes;
  }, [sources]);

  const languages = useMemo(() => {
    return [...new Set(sources.map((s) => s.language))].sort();
  }, [sources]);

  // Filtered and sorted
  const filtered = useMemo(() => {
    let result = [...sources];
    if (filterCountry) result = result.filter((s) => s.country_code === filterCountry);
    if (filterTier) result = result.filter((s) => s.tier === filterTier);
    if (filterLang) result = result.filter((s) => s.language === filterLang);
    return result.sort((a, b) => b.article_count - a.article_count);
  }, [sources, filterCountry, filterTier, filterLang]);

  // Metrics
  const totalSources = sources.length;
  const activeSources = sources.filter((s) => s.active).length;
  const totalArticles = sources.reduce((sum, s) => sum + s.article_count, 0);
  const countriesCovered = new Set(sources.map((s) => s.country_code)).size;

  if (loading) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold">📡 Источники</h1>
        <p className="text-muted-foreground">Загрузка…</p>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-24 animate-pulse rounded-lg border border-border bg-card" />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">📡 Источники</h1>
        <p className="mt-1 text-muted-foreground">Медиа-источники, отслеживаемые системой</p>
      </div>

      {/* Metric Cards */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Card className="border-border bg-card">
          <CardContent className="p-4 text-center">
            <div className="text-3xl font-bold">{totalSources}</div>
            <div className="mt-1 text-xs text-muted-foreground">Всего источников</div>
          </CardContent>
        </Card>
        <Card className="border-border bg-card">
          <CardContent className="p-4 text-center">
            <div className="text-3xl font-bold text-green-400">{activeSources}</div>
            <div className="mt-1 text-xs text-muted-foreground">Активных</div>
          </CardContent>
        </Card>
        <Card className="border-border bg-card">
          <CardContent className="p-4 text-center">
            <div className="text-3xl font-bold text-blue-400">{totalArticles.toLocaleString()}</div>
            <div className="mt-1 text-xs text-muted-foreground">Статей собрано</div>
          </CardContent>
        </Card>
        <Card className="border-border bg-card">
          <CardContent className="p-4 text-center">
            <div className="text-3xl font-bold text-amber-400">{countriesCovered}</div>
            <div className="mt-1 text-xs text-muted-foreground">Стран покрыто</div>
          </CardContent>
        </Card>
      </div>

      {/* Filters */}
      <div className="space-y-3">
        {/* Country filter */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground mr-1">Страна:</span>
          <button
            onClick={() => setFilterCountry(null)}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-all ${
              filterCountry === null
                ? "bg-blue-500/20 text-blue-400"
                : "text-muted-foreground hover:bg-white/5"
            }`}
          >
            Все
          </button>
          {countries.map((code) => (
            <button
              key={code}
              onClick={() => setFilterCountry(filterCountry === code ? null : code)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-all ${
                filterCountry === code
                  ? "bg-blue-500/20 text-blue-400"
                  : "text-muted-foreground hover:bg-white/5"
              }`}
            >
              {COUNTRY_FLAGS[code] || ""} {COUNTRY_NAMES[code] || code}
            </button>
          ))}
        </div>

        {/* Tier filter */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground mr-1">Тир:</span>
          <button
            onClick={() => setFilterTier(null)}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-all ${
              filterTier === null
                ? "bg-blue-500/20 text-blue-400"
                : "text-muted-foreground hover:bg-white/5"
            }`}
          >
            Все
          </button>
          {ALL_TIERS.map((tier) => {
            const cfg = TIER_CONFIG[tier];
            return (
              <button
                key={tier}
                onClick={() => setFilterTier(filterTier === tier ? null : tier)}
                className={`rounded-full px-3 py-1 text-xs font-medium transition-all ${
                  filterTier === tier
                    ? `${cfg.bg} ${cfg.color} border`
                    : "text-muted-foreground hover:bg-white/5"
                }`}
              >
                {cfg.label}
              </button>
            );
          })}
        </div>

        {/* Language filter */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs text-muted-foreground mr-1">Язык:</span>
          <button
            onClick={() => setFilterLang(null)}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-all ${
              filterLang === null
                ? "bg-blue-500/20 text-blue-400"
                : "text-muted-foreground hover:bg-white/5"
            }`}
          >
            Все
          </button>
          {languages.map((lang) => (
            <button
              key={lang}
              onClick={() => setFilterLang(filterLang === lang ? null : lang)}
              className={`rounded-full px-3 py-1 text-xs font-medium transition-all ${
                filterLang === lang
                  ? "bg-blue-500/20 text-blue-400"
                  : "text-muted-foreground hover:bg-white/5"
              }`}
            >
              {lang}
            </button>
          ))}
        </div>
      </div>

      {/* Results count */}
      <div className="text-sm text-muted-foreground">
        Показано: {filtered.length} из {totalSources}
      </div>

      {/* Sources Table */}
      <Card className="border-border bg-card overflow-x-auto">
        <CardContent className="p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="px-4 py-3 font-medium">Название</th>
                <th className="px-4 py-3 font-medium">Страна</th>
                <th className="px-4 py-3 font-medium">Тир</th>
                <th className="px-4 py-3 font-medium">Язык</th>
                <th className="px-4 py-3 font-medium text-right">Статей</th>
                <th className="px-4 py-3 font-medium text-right">Релевантных</th>
                <th className="px-4 py-3 font-medium text-right">Avg Sentiment</th>
                <th className="px-4 py-3 font-medium text-right">Последний сбор</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((source) => {
                const tierCfg = TIER_CONFIG[source.tier] || {
                  label: source.tier,
                  color: "text-gray-400",
                  bg: "bg-gray-500/15 border-gray-500/30",
                };
                return (
                  <tr key={source.id} className="border-b border-border/50 hover:bg-white/[0.02]">
                    <td className="px-4 py-3">
                      <a
                        href={source.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="font-medium text-blue-400 hover:underline"
                      >
                        {source.name}
                      </a>
                    </td>
                    <td className="px-4 py-3">
                      <span className="whitespace-nowrap">
                        {COUNTRY_FLAGS[source.country_code] || ""}{" "}
                        {COUNTRY_NAMES[source.country_code] || source.country_code}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <Badge
                        variant="outline"
                        className={`text-[10px] ${tierCfg.color} ${tierCfg.bg}`}
                      >
                        {tierCfg.label}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">{source.language}</td>
                    <td className="px-4 py-3 text-right font-medium">{source.article_count}</td>
                    <td className="px-4 py-3 text-right text-muted-foreground">{source.relevant_count}</td>
                    <td className={`px-4 py-3 text-right ${sentimentColor(source.avg_sentiment)}`}>
                      {source.avg_sentiment !== null ? source.avg_sentiment.toFixed(2) : "—"}
                    </td>
                    <td className="px-4 py-3 text-right text-muted-foreground whitespace-nowrap">
                      {source.last_collected ? formatDate(source.last_collected) : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}
