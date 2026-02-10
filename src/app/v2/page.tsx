"use client";

import { useEffect, useState, useCallback } from "react";
import { Badge } from "@/components/ui/badge";
import { getPipelineStats, getAlerts, type PipelineStats, type Alert } from "@/lib/api-v2";
import { getStats, getSources, type Stats, type Source } from "@/lib/api";

function SeverityBadge({ severity }: { severity: string }) {
  const colors: Record<string, string> = {
    critical: "bg-red-500/20 text-red-400 border-red-500/30",
    high: "bg-orange-500/20 text-orange-400 border-orange-500/30",
    medium: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
    low: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  };
  return (
    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium ${colors[severity] || colors.low}`}>
      {severity}
    </span>
  );
}

function StatCard({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: string | number;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className={`mt-1 text-2xl font-bold ${accent || "text-foreground"}`}>
        {value}
      </div>
      {sub && <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

export default function V2DashboardPage() {
  const [pipeline, setPipeline] = useState<PipelineStats | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const [pRes, aRes, sRes, srcRes] = await Promise.allSettled([
        getPipelineStats(),
        getAlerts(20),
        getStats(7),
        getSources(),
      ]);
      if (pRes.status === "fulfilled") setPipeline(pRes.value);
      if (aRes.status === "fulfilled") setAlerts(aRes.value.alerts);
      if (sRes.status === "fulfilled") setStats(sRes.value);
      if (srcRes.status === "fulfilled") setSources(srcRes.value.sources);
      setLastRefresh(new Date());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, 30000); // auto-refresh 30s
    return () => clearInterval(interval);
  }, [refresh]);

  const activeSources = sources.filter((s) => s.active).length;
  const inactiveSources = sources.filter((s) => !s.active).length;
  const recentSources = sources.filter(
    (s) => s.last_collected && Date.now() - new Date(s.last_collected).getTime() < 86400000
  ).length;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Пульт управления</h1>
          <p className="text-sm text-muted-foreground">
            Операционный дашборд v2 — пайплайн, источники, алерты
          </p>
        </div>
        <div className="flex items-center gap-3">
          {lastRefresh && (
            <span className="text-xs text-muted-foreground">
              обновлено {lastRefresh.toLocaleTimeString("ru-RU")}
            </span>
          )}
          <button
            onClick={refresh}
            disabled={loading}
            className="rounded-md bg-blue-500/20 px-3 py-1.5 text-sm text-blue-400 transition hover:bg-blue-500/30 disabled:opacity-50"
          >
            {loading ? "⏳" : "🔄"} Обновить
          </button>
        </div>
      </div>

      {/* Pipeline + System Stats */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="📰 Статей (7д)"
          value={stats?.total_articles?.toLocaleString("ru-RU") ?? "—"}
          sub={`${stats?.total_relevant?.toLocaleString("ru-RU") ?? "?"} релевантных`}
          accent="text-blue-400"
        />
        <StatCard
          label="📡 Источники"
          value={`${activeSources} / ${sources.length}`}
          sub={`${inactiveSources} отключены · ${recentSources} активны за 24ч`}
          accent="text-green-400"
        />
        <StatCard
          label="🔁 Дупликаты"
          value={stats?.total_duplicates?.toLocaleString("ru-RU") ?? "—"}
          sub={stats?.total_articles ? `${((stats.total_duplicates / stats.total_articles) * 100).toFixed(1)}% от всех` : ""}
          accent="text-yellow-400"
        />
        <StatCard
          label="🌡️ Последнее обновление"
          value={
            stats?.last_temperature_update
              ? new Date(stats.last_temperature_update).toLocaleString("ru-RU", {
                  day: "2-digit",
                  month: "2-digit",
                  hour: "2-digit",
                  minute: "2-digit",
                })
              : "—"
          }
          sub="температура"
        />
      </div>

      {/* Pipeline Status */}
      <div className="rounded-lg border border-border bg-card p-5">
        <h2 className="mb-3 text-lg font-semibold">⚡ Pipeline</h2>
        {pipeline ? (
          !pipeline.error ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-6">
              <div>
                <div className="text-xs text-muted-foreground">Ожидают сбора</div>
                <div className="text-xl font-bold text-blue-400">{(pipeline as any).raw_articles_pending ?? 0}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Ожидают анализа</div>
                <div className="text-xl font-bold text-yellow-400">{(pipeline as any).analyzed_pending ?? 0}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Dead Letters</div>
                <div className="text-xl font-bold text-red-400">{(pipeline as any).dead_letter ?? 0}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Проанализировано сегодня</div>
                <div className="text-xl font-bold text-green-400">{(pipeline as any).analyzer_today_count ?? 0}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Последний сбор</div>
                <div className="text-sm font-medium">
                  {(pipeline as any).collector_last_run
                    ? new Date((pipeline as any).collector_last_run).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
                    : "—"}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Последний анализ</div>
                <div className="text-sm font-medium">
                  {(pipeline as any).analyzer_last_run
                    ? new Date((pipeline as any).analyzer_last_run).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })
                    : "—"}
                </div>
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-yellow-400">
              <span className="text-lg">⚠️</span>
              <div>
                <div className="font-medium">Ошибка pipeline</div>
                <div className="text-xs text-muted-foreground">
                  {pipeline.error}
                </div>
              </div>
            </div>
          )
        ) : (
          <div className="text-sm text-muted-foreground">Загрузка…</div>
        )}
      </div>

      {/* Source Health */}
      <div className="rounded-lg border border-border bg-card p-5">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-lg font-semibold">📡 Здоровье источников</h2>
          <a href="/v2/sources" className="text-sm text-blue-400 hover:underline">
            Управление →
          </a>
        </div>
        <div className="space-y-2">
          {/* Tier breakdown */}
          {(() => {
            const tiers: Record<string, { total: number; active: number; recent: number }> = {};
            sources.forEach((s) => {
              const t = s.tier || "mainstream";
              if (!tiers[t]) tiers[t] = { total: 0, active: 0, recent: 0 };
              tiers[t].total++;
              if (s.active) tiers[t].active++;
              if (s.last_collected && Date.now() - new Date(s.last_collected).getTime() < 86400000)
                tiers[t].recent++;
            });
            const tierLabels: Record<string, string> = {
              official: "🏛️ Официальные",
              mainstream: "📰 Мейнстрим",
              analytics: "🔍 Аналитика",
              opposition: "📢 Оппозиция",
              independent: "🎯 Независимые",
              western_proxy: "🌐 Западные прокси",
            };
            return Object.entries(tiers)
              .sort(([, a], [, b]) => b.total - a.total)
              .map(([tier, d]) => (
                <div key={tier} className="flex items-center gap-3 text-sm">
                  <span className="w-40 text-muted-foreground">{tierLabels[tier] || tier}</span>
                  <div className="flex-1">
                    <div className="h-2 rounded-full bg-zinc-800">
                      <div
                        className="h-2 rounded-full bg-blue-500/60"
                        style={{ width: `${(d.recent / Math.max(d.total, 1)) * 100}%` }}
                      />
                    </div>
                  </div>
                  <span className="w-24 text-right text-xs text-muted-foreground">
                    {d.recent}/{d.active}/{d.total}
                  </span>
                </div>
              ));
          })()}
          <div className="pt-1 text-xs text-muted-foreground">
            Формат: активны 24ч / включены / всего
          </div>
        </div>
      </div>

      {/* Alerts */}
      <div className="rounded-lg border border-border bg-card p-5">
        <h2 className="mb-3 text-lg font-semibold">🚨 Алерты</h2>
        {alerts.length > 0 ? (
          <div className="space-y-2">
            {alerts.slice(0, 10).map((a) => (
              <div
                key={a.id}
                className="flex items-start gap-3 rounded-md border border-border bg-zinc-900/50 p-3"
              >
                <SeverityBadge severity={a.severity} />
                <div className="flex-1">
                  <div className="text-sm font-medium">{a.title}</div>
                  <div className="text-xs text-muted-foreground">
                    {a.country_name} · {a.type} ·{" "}
                    {new Date(a.created_at).toLocaleString("ru-RU", {
                      day: "2-digit",
                      month: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </div>
                  {a.description && (
                    <div className="mt-1 text-xs text-muted-foreground">{a.description}</div>
                  )}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-sm text-muted-foreground">Нет алертов</div>
        )}
      </div>
    </div>
  );
}
