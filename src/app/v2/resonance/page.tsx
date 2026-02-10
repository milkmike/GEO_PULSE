"use client";

import { useEffect, useState, useCallback } from "react";
import { getResonance, type ResonanceEvent } from "@/lib/api-v2";
import { COUNTRY_FLAGS, COUNTRY_NAMES, COUNTRY_CODES } from "@/lib/api";

function sentimentColor(s: number) {
  if (s < -0.3) return "text-red-400";
  if (s < -0.1) return "text-orange-400";
  if (s > 0.3) return "text-green-400";
  if (s > 0.1) return "text-emerald-400";
  return "text-muted-foreground";
}

function actionBadge(level: number) {
  const colors = [
    "bg-zinc-700 text-zinc-300",
    "bg-zinc-700 text-zinc-300",
    "bg-yellow-500/20 text-yellow-400",
    "bg-orange-500/20 text-orange-400",
    "bg-red-500/20 text-red-400",
    "bg-red-700/30 text-red-300",
    "bg-red-900/40 text-red-200",
  ];
  return colors[Math.min(level, 6)] || colors[0];
}

export default function V2ResonancePage() {
  const [country, setCountry] = useState<string>("KZ");
  const [days, setDays] = useState(14);
  const [events, setEvents] = useState<ResonanceEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getResonance(country, days, 20);
      setEvents(res.events);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [country, days]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const maxResonance = Math.max(...events.map((e) => e.resonance_score), 1);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">🔥 Резонансные события</h1>
        <p className="text-sm text-muted-foreground">
          Топ событий по резонансу — объём × разнообразие × скорость × уровень действия
        </p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <select
          className="rounded-md border border-border bg-zinc-800 px-3 py-1.5 text-sm"
          value={country}
          onChange={(e) => setCountry(e.target.value)}
        >
          {COUNTRY_CODES.map((c) => (
            <option key={c} value={c}>
              {COUNTRY_FLAGS[c]} {COUNTRY_NAMES[c]}
            </option>
          ))}
        </select>
        <div className="flex gap-1">
          {[7, 14, 30].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d)}
              className={`rounded-md px-3 py-1.5 text-sm transition ${
                days === d
                  ? "bg-blue-500/20 text-blue-400"
                  : "text-muted-foreground hover:bg-accent"
              }`}
            >
              {d}д
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="rounded-md border border-red-500/30 bg-red-900/20 p-3 text-sm text-red-300">
          {error}
        </div>
      )}

      {loading ? (
        <div className="py-12 text-center text-muted-foreground">Загрузка…</div>
      ) : events.length === 0 ? (
        <div className="py-12 text-center text-muted-foreground">
          Нет резонансных событий за {days} дней
        </div>
      ) : (
        <div className="space-y-3">
          {events.map((ev, i) => (
            <div
              key={ev.event_key}
              className="rounded-lg border border-border bg-card p-4 transition hover:border-zinc-600"
            >
              <div className="flex items-start gap-4">
                {/* Rank */}
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-zinc-800 text-sm font-bold text-muted-foreground">
                  {i + 1}
                </div>

                <div className="flex-1 space-y-2">
                  {/* Title + resonance bar */}
                  <div className="flex items-start justify-between gap-4">
                    <div className="font-medium">{ev.event_key}</div>
                    <div className="shrink-0 text-right">
                      <div className="text-lg font-bold text-blue-400">
                        {ev.resonance_score.toFixed(1)}
                      </div>
                      <div className="text-xs text-muted-foreground">резонанс</div>
                    </div>
                  </div>

                  {/* Resonance bar */}
                  <div className="h-1.5 rounded-full bg-zinc-800">
                    <div
                      className="h-1.5 rounded-full bg-gradient-to-r from-blue-600 to-blue-400"
                      style={{
                        width: `${(ev.resonance_score / maxResonance) * 100}%`,
                      }}
                    />
                  </div>

                  {/* Metrics */}
                  <div className="flex flex-wrap gap-4 text-xs">
                    <span className="text-muted-foreground">
                      📰 {ev.article_count} статей
                    </span>
                    <span className="text-muted-foreground">
                      📡 {ev.source_count} источников
                    </span>
                    <span className="text-muted-foreground">
                      🏷️ {ev.tier_count} уровней
                    </span>
                    <span className={sentimentColor(ev.avg_sentiment)}>
                      💬 {ev.avg_sentiment.toFixed(2)}
                    </span>
                    <span className={`inline-flex items-center rounded-full px-1.5 py-0.5 ${actionBadge(ev.max_action_level)}`}>
                      ⚡ уровень {ev.max_action_level}
                    </span>
                    <span className="text-muted-foreground">
                      ⏱️ {ev.spread_hours.toFixed(0)}ч разброс
                    </span>
                  </div>

                  {/* Sources & tiers */}
                  <div className="flex flex-wrap gap-1">
                    {ev.tiers.map((t) => (
                      <span
                        key={t}
                        className="rounded bg-zinc-800 px-1.5 py-0.5 text-xs text-muted-foreground"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    Источники: {ev.source_names.slice(0, 5).join(", ")}
                    {ev.source_names.length > 5 && ` +${ev.source_names.length - 5}`}
                  </div>

                  {/* Time range */}
                  <div className="text-xs text-muted-foreground">
                    {new Date(ev.first_seen).toLocaleString("ru-RU", {
                      day: "2-digit",
                      month: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}{" "}
                    →{" "}
                    {new Date(ev.last_seen).toLocaleString("ru-RU", {
                      day: "2-digit",
                      month: "2-digit",
                      hour: "2-digit",
                      minute: "2-digit",
                    })}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
