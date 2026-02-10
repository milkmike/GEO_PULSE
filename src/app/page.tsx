"use client";

import { useEffect, useState, useCallback } from "react";
import { Badge } from "@/components/ui/badge";
import PeriodSelector from "@/components/PeriodSelector";
import StatsCards from "@/components/StatsCards";
import CountryCard from "@/components/CountryCard";
import dynamic from "next/dynamic";
import {
  getCountries,
  getStats,
  PERIOD_DAYS,
  formatDate,
  minutesAgo,
  type Country,
  type Stats,
} from "@/lib/api";

const PlotlyMap = dynamic(() => import("@/components/PlotlyMap"), {
  ssr: false,
  loading: () => (
    <div className="flex h-[420px] items-center justify-center rounded-lg border border-border bg-card">
      <span className="text-sm text-muted-foreground">Загрузка карты…</span>
    </div>
  ),
});

type ViewMode = "thermometer" | "connections";

export default function OverviewPage() {
  const [period, setPeriod] = useState("Год");
  const [viewMode, setViewMode] = useState<ViewMode>("thermometer");
  const [countries, setCountries] = useState<Country[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState<string>("");

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const days = PERIOD_DAYS[period];
      const [countriesData, statsData] = await Promise.all([
        getCountries(days),
        getStats(days),
      ]);
      setCountries(countriesData.countries);
      setStats(statsData);
      setLastUpdate(statsData.newest_article);
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
      {/* Header row: period selector + view toggle */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <PeriodSelector selected={period} onSelect={setPeriod} />
        <div className="flex gap-1">
          <button
            onClick={() => setViewMode("thermometer")}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-all ${
              viewMode === "thermometer"
                ? "bg-blue-500/20 text-blue-400"
                : "text-muted-foreground hover:bg-white/5"
            }`}
          >
            🌡️ Термометр
          </button>
          <button
            onClick={() => setViewMode("connections")}
            className={`rounded-full px-3 py-1 text-xs font-medium transition-all ${
              viewMode === "connections"
                ? "bg-blue-500/20 text-blue-400"
                : "text-muted-foreground hover:bg-white/5"
            }`}
          >
            🔗 Связи
          </button>
        </div>
      </div>

      {/* Period badge */}
      <div className="flex items-center gap-2">
        <Badge variant="secondary" className="text-xs text-muted-foreground">
          {period} · {formatDate(startDate.toISOString())} —{" "}
          {formatDate(new Date().toISOString())}
          {lastUpdate && ` · обновлено ${minutesAgo(lastUpdate)} мин назад`}
        </Badge>
      </div>

      {/* Stats cards */}
      <StatsCards stats={stats} loading={loading} />

      {/* Map */}
      <PlotlyMap countries={countries} />

      {/* Country cards grid */}
      <div>
        <h2 className="mb-4 text-lg font-semibold text-foreground">
          Страны ({countries.length})
        </h2>
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
                <CountryCard key={country.code} country={country} />
              ))}
          </div>
        )}
      </div>
    </div>
  );
}
