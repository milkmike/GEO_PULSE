"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import PeriodSelector from "@/components/PeriodSelector";
import StatsCards from "@/components/StatsCards";
import CountryCard from "@/components/CountryCard";
import SectionHeader from "@/components/SectionHeader";
import InfoPopover from "@/components/InfoPopover";
import { glossary } from "@/lib/glossary";
import dynamic from "next/dynamic";
import Headline from "@/components/Headline";
import {
  getCountries,
  getStats,
  getTierDivergence,
  getCountryTemperature,
  API_URL,
  PERIOD_DAYS,
  COUNTRY_FLAGS,
  formatDate,
  minutesAgo,
  temperatureColor,
  type Country,
  type Stats,
  type Thread,
  type TierDivergenceCountry,
} from "@/lib/api";

const GeoMap = dynamic(() => import("@/components/GeoMap"), {
  ssr: false,
  loading: () => (
    <div className="flex h-[520px] items-center justify-center rounded-xl border border-border bg-card">
      <span className="text-sm text-muted-foreground">Загрузка карты…</span>
    </div>
  ),
});

export default function OverviewPage() {
  const [period, setPeriod] = useState("Год");
  const [countries, setCountries] = useState<Country[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<string>("");
  const [topThreads, setTopThreads] = useState<Thread[]>([]);
  const [divergence, setDivergence] = useState<TierDivergenceCountry[]>([]);
  const [selectedCountry, setSelectedCountry] = useState<string | null>(null);
  const [sparklines, setSparklines] = useState<Record<string, number[]>>({});

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const days = PERIOD_DAYS[period];
      const [countriesData, statsData, threadsRes, divRes] = await Promise.all([
        getCountries(days),
        getStats(days),
        fetch(`${API_URL}/api/v1/threads?limit=5&sort=importance`).then(r => r.json()).catch(() => ({ threads: [] })),
        getTierDivergence(14).catch(() => ({ countries: [] })),
      ]);
      setCountries(countriesData.countries);
      setStats(statsData);
      setLastUpdate(statsData.newest_article);
      setTopThreads(threadsRes.threads || []);
      setDivergence(divRes.countries || []);

      // Fetch 7-day sparkline data for each country
      const codes = countriesData.countries.map((c: Country) => c.code);
      const sparklineResults = await Promise.allSettled(
        codes.map((code: string) => getCountryTemperature(code, 7))
      );
      const sparkMap: Record<string, number[]> = {};
      codes.forEach((code: string, i: number) => {
        const result = sparklineResults[i];
        if (result.status === "fulfilled" && result.value.data) {
          sparkMap[code] = result.value.data.map((p) => p.temperature);
        }
      });
      setSparklines(sparkMap);
    } catch (err) {
      console.error("Failed to fetch data:", err);
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const days = PERIOD_DAYS[period];
  const startDate = new Date();
  startDate.setDate(startDate.getDate() - days);

  return (
    <div className="space-y-6">
      {/* Header row: period selector */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <PeriodSelector selected={period} onSelect={setPeriod} />
      </div>

      {/* Period badge */}
      <div className="flex items-center gap-2">
        <Badge variant="secondary" className="text-xs text-muted-foreground">
          {period} · {formatDate(startDate.toISOString())} —{" "}
          {formatDate(new Date().toISOString())}
          {lastUpdate && ` · обновлено ${minutesAgo(lastUpdate)} мин назад`}
        </Badge>
      </div>

      {/* Hero headline */}
      <Headline countries={countries} />

      {/* Stats cards */}
      <StatsCards stats={stats} loading={loading} />

      {/* Top threads — two columns */}
      {topThreads.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2">
          {/* Left: Hero thread */}
          {topThreads[0] && (
            <Link href={`/threads/${topThreads[0].id}`}>
              <div className="rounded-xl border border-red-500/15 bg-gradient-to-br from-red-500/5 to-transparent p-5 hover:border-red-500/30 transition-all cursor-pointer h-full">
                <div className="flex items-center gap-2 mb-3">
                  <span className="text-[10px] font-semibold tracking-widest uppercase text-red-400/70">🔥 Главный сюжет</span>
                </div>
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-sm">{COUNTRY_FLAGS[topThreads[0].country_code]}</span>
                  <span className="text-xs text-muted-foreground">{topThreads[0].country_name}</span>
                  <Badge variant="outline" className="text-[10px]">★ {topThreads[0].importance_score.toFixed(0)}</Badge>
                  <Badge variant="outline" className="text-[10px]">📰 {topThreads[0].article_count}</Badge>
                </div>
                <h3 className="text-lg font-bold hover:text-blue-400 transition-colors leading-snug">{topThreads[0].title}</h3>
                {topThreads[0].narrative && (
                  <p className="mt-2 text-xs text-muted-foreground line-clamp-3">{topThreads[0].narrative}</p>
                )}
              </div>
            </Link>
          )}

          {/* Right: Focus threads stacked */}
          {topThreads.length > 1 && (
            <div className="space-y-2">
              <span className="text-[10px] font-semibold tracking-widest uppercase text-white/30 px-1">🎯 В фокусе</span>
              {topThreads.slice(1, 4).map((thread) => (
                <Link key={thread.id} href={`/threads/${thread.id}`}>
                  <div className="rounded-lg border border-white/[0.06] p-3 hover:border-white/15 transition-all cursor-pointer">
                    <div className="flex items-start gap-3">
                      <span className="text-sm mt-0.5 shrink-0">{COUNTRY_FLAGS[thread.country_code]}</span>
                      <div className="min-w-0 flex-1">
                        <h4 className="font-medium text-sm hover:text-blue-400 transition-colors line-clamp-1">{thread.title}</h4>
                        <div className="mt-1 flex gap-3 text-[11px] text-muted-foreground">
                          <span>★ {thread.importance_score.toFixed(0)}</span>
                          <span>📰 {thread.article_count}</span>
                          <span style={{ color: thread.avg_sentiment > 0.05 ? "#22c55e" : thread.avg_sentiment < -0.05 ? "#ef4444" : "#eab308" }}>
                            💬 {thread.avg_sentiment.toFixed(2)}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Map */}
      <SectionHeader
        icon="🗺️"
        title="Карта"
        description={glossary.temperature.short}
        infoTitle="Медийная температура"
        infoContent={glossary.temperature.detail}
      />
      <GeoMap
        countries={countries}
        selectedCountry={selectedCountry}
        onCountrySelect={setSelectedCountry}
        height={520}
      />

      {/* Narrative divergence */}
      {divergence.length > 0 && (() => {
        const topDiv = [...divergence].sort((a, b) => b.divergence - a.divergence).slice(0, 3);
        return (
          <div className="space-y-3">
            <SectionHeader
              icon="🎭"
              title="Где расходятся нарративы"
              description={glossary.divergence.short}
              infoTitle="Расхождение нарративов"
              infoContent={glossary.divergence.detail}
            />
            <div className="grid gap-3 md:grid-cols-3">
              {topDiv.map((c) => (
                <Link key={c.code} href={`/country/${c.code}`}>
                  <div className={`rounded-lg border p-4 hover:border-white/20 transition-all cursor-pointer ${
                    c.divergence >= 0.5 ? "border-red-500/20 bg-red-500/5" : c.divergence >= 0.2 ? "border-yellow-500/20 bg-yellow-500/5" : "border-white/8"
                  }`}>
                    <div className="flex items-center justify-between mb-2">
                      <span className="font-medium text-sm">{COUNTRY_FLAGS[c.code]} {c.name}</span>
                      <Badge variant="outline" className={`text-[10px] font-mono ${
                        c.divergence >= 0.5 ? "border-red-500/30 text-red-400" : c.divergence >= 0.2 ? "border-yellow-500/30 text-yellow-400" : "border-green-500/30 text-green-400"
                      }`}>
                        Δ {c.divergence.toFixed(2)}
                      </Badge>
                    </div>
                    <div className="text-xs text-muted-foreground">
                      {c.total_articles} статей · {c.tiers.length} тиров
                      {c.most_positive_tier && c.most_negative_tier && c.most_positive_tier !== c.most_negative_tier && (
                        <span className="block mt-1">
                          Позитив: {c.most_positive_tier} → Негатив: {c.most_negative_tier}
                        </span>
                      )}
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        );
      })()}

      {/* Country cards grid */}
      <div>
        <SectionHeader
          icon="🌍"
          title={`Страны (${countries.length})`}
          description="Все отслеживаемые страны СНГ — отсортированы по температуре"
          infoTitle="Медийная температура"
          infoContent={glossary.temperature.detail}
        />
        {loading ? (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={i}
                className="h-32 animate-pulse rounded-lg border border-border bg-card"
              />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {countries
              .sort((a, b) => b.temperature - a.temperature)
              .map((country) => (
                <CountryCard key={country.code} country={country} sparklineData={sparklines[country.code]} />
              ))}
          </div>
        )}
      </div>
    </div>
  );
}
