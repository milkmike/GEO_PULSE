"use client";

import { useEffect, useState, useMemo } from "react";
import { useParams, useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import PeriodSelector from "@/components/PeriodSelector";
import TemperatureChart from "@/components/TemperatureChart";
import UNVotesChart from "@/components/UNVotesChart";
import TradeChart from "@/components/TradeChart";
import InfoPopover from "@/components/InfoPopover";
import NarrativeXrayExpanded from "@/components/NarrativeXrayExpanded";
import ErrorBoundary from "@/components/ErrorBoundary";
import { glossary } from "@/lib/glossary";
import Link from "next/link";
import SectionHeader from "@/components/SectionHeader";
import {
  getCountries,
  getCountryEvents,
  getCountryDigest,
  getCountryTemperature,
  getCountryUNVotes,
  getCountryTrade,
  getCountryThreads,
  getSources,
  PERIOD_DAYS,
  type Country,
  type CountryEvent,
  type CountryDigest,
  type CountryEventsResponse,
  type TemperaturePoint,
  type UNVoteYear,
  type TradeYear,
  type Thread,
  temperatureColor,
  trendIcon,
  formatDate,
} from "@/lib/api";

const TIER_COLORS: Record<string, string> = {
  official: "bg-red-500/20 text-red-400 border-red-500/30",
  mainstream: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  independent: "bg-green-500/20 text-green-400 border-green-500/30",
  domestic_opposition: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  analytics: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  western_proxy: "bg-gray-500/20 text-gray-400 border-gray-500/30",
};

const TIER_LABELS: Record<string, string> = {
  official: "Официальные",
  mainstream: "Мейнстрим",
  independent: "Независимые",
  domestic_opposition: "Оппозиция",
  analytics: "Аналитика",
  western_proxy: "Западные",
};

const EVENT_TYPE_COLORS: Record<string, string> = {
  diplomatic: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  economic: "bg-green-500/20 text-green-400 border-green-500/30",
  security: "bg-red-500/20 text-red-400 border-red-500/30",
  cultural: "bg-purple-500/20 text-purple-400 border-purple-500/30",
};

const EVENT_TYPE_LABELS: Record<string, string> = {
  diplomatic: "Дипломатия",
  economic: "Экономика",
  security: "Безопасность",
  cultural: "Культура",
};

function sentimentBar(sentiment: number | null) {
  if (sentiment === null || sentiment === undefined) {
    return <div className="h-1.5 w-16 rounded-full bg-gray-500/30" />;
  }
  // sentiment is typically -1 to 1
  const pct = Math.abs(sentiment) * 100;
  const color = sentiment > 0.05 ? "bg-green-500" : sentiment < -0.05 ? "bg-red-500" : "bg-gray-400";
  return (
    <div className="flex items-center gap-1.5">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-white/10">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${Math.max(pct, 8)}%` }} />
      </div>
      <span className="text-[10px] text-muted-foreground">{sentiment.toFixed(2)}</span>
    </div>
  );
}

function actionIcon(level: number) {
  return level >= 4 ? "💥" : "⚡";
}

export default function CountryPage() {
  const params = useParams();
  const router = useRouter();
  const code = (params.code as string).toUpperCase();

  const [period, setPeriod] = useState("Месяц");
  const [country, setCountry] = useState<Country | null>(null);
  const [events, setEvents] = useState<CountryEvent[]>([]);
  const [digest, setDigest] = useState<CountryDigest | null>(null);
  const [tempData, setTempData] = useState<TemperaturePoint[]>([]);
  const [unVotes, setUnVotes] = useState<UNVoteYear[]>([]);
  const [tradeData, setTradeData] = useState<TradeYear[]>([]);
  const [loading, setLoading] = useState(true);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [visibleCount, setVisibleCount] = useState(20);
  const [typeFilter, setTypeFilter] = useState<string | null>(null);
  const [tierFilter, setTierFilter] = useState<string | null>(null);
  const [sourceTierMap, setSourceTierMap] = useState<Record<string, string>>({});
  const [threads, setThreads] = useState<Thread[]>([]);

  // Load UN votes + trade + threads + sources once
  useEffect(() => {
    getCountryUNVotes(code)
      .then((r) => setUnVotes(r.data || []))
      .catch(() => setUnVotes([]));
    getCountryTrade(code)
      .then((r) => setTradeData(r.data || []))
      .catch(() => setTradeData([]));
    getCountryThreads(code, { limit: 10 })
      .then((r) => setThreads(r.threads || []))
      .catch(() => setThreads([]));
    getSources()
      .then((r) => {
        const map: Record<string, string> = {};
        for (const s of r.sources) {
          map[s.name] = s.tier;
        }
        setSourceTierMap(map);
      })
      .catch(() => {});
  }, [code]);

  // Load country info + events + digest + temperature when period changes
  useEffect(() => {
    const days = PERIOD_DAYS[period] || 30;
    setEventsLoading(true);
    setVisibleCount(20);
    getCountries(days).then((d) => {
      const c = d.countries.find((c) => c.code === code);
      setCountry(c || null);
    });
    getCountryTemperature(code, days)
      .then((r) => setTempData(r.data || []))
      .catch(() => setTempData([]));
    Promise.all([
      getCountryEvents(code, days).catch(() => null),
      getCountryDigest(code, days).catch(() => null),
    ]).then(([evResp, dig]) => {
      if (evResp && (evResp as CountryEventsResponse).events) {
        setEvents((evResp as CountryEventsResponse).events);
      } else {
        setEvents([]);
      }
      setDigest(dig);
      setEventsLoading(false);
      setLoading(false);
    });
  }, [code, period]);

  // Sorted & filtered events
  const filteredEvents = useMemo(() => {
    let evs = [...events].sort(
      (a, b) => new Date(b.published_at).getTime() - new Date(a.published_at).getTime()
    );
    if (typeFilter) {
      evs = evs.filter((e) => e.event_type === typeFilter);
    }
    if (tierFilter) {
      evs = evs.filter((e) => sourceTierMap[e.source] === tierFilter);
    }
    return evs;
  }, [events, typeFilter, tierFilter, sourceTierMap]);

  const visibleEvents = filteredEvents.slice(0, visibleCount);

  // Unique event types for filter chips
  const eventTypes = useMemo(() => {
    const types = new Set(events.map((e) => e.event_type));
    return Array.from(types).sort();
  }, [events]);

  // Unique tiers for filter chips
  const availableTiers = useMemo(() => {
    const tiers = new Set<string>();
    for (const e of events) {
      const t = sourceTierMap[e.source];
      if (t) tiers.add(t);
    }
    return Array.from(tiers).sort();
  }, [events, sourceTierMap]);

  if (loading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <div className="grid gap-4 md:grid-cols-3">
          <Skeleton className="h-28" />
          <Skeleton className="h-28" />
          <Skeleton className="h-28" />
        </div>
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-60 w-full" />
      </div>
    );
  }

  if (!country) {
    return <p className="text-muted-foreground">Страна {code} не найдена</p>;
  }

  const color = temperatureColor(country.temperature);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => router.push("/")}
            className="text-muted-foreground hover:text-foreground"
          >
            ← Обзор
          </Button>
          <h1 className="text-2xl font-bold">{country.name}</h1>
          <Badge variant="secondary" className="text-xs">
            {country.code}
          </Badge>
          <span className="text-3xl font-bold" style={{ color }}>
            {country.temperature > 0 ? "+" : ""}
            {country.temperature.toFixed(1)}°
          </span>
          <span className="text-lg" style={{ color }}>
            {trendIcon(country.trend)}
          </span>
        </div>
        <PeriodSelector selected={period} onSelect={setPeriod} />
      </div>

      {/* Metrics */}
      <div className="grid gap-4 md:grid-cols-3">
        <Card className="border-border bg-card">
          <CardContent className="p-6 text-center">
            <div className="text-4xl font-bold" style={{ color }}>
              {country.temperature > 0 ? "+" : ""}
              {country.temperature.toFixed(1)}°
            </div>
            <div className="mt-2 text-sm text-muted-foreground flex items-center justify-center gap-1">
              Температура {trendIcon(country.trend)}
              <InfoPopover title="Медийная температура">{glossary.temperature.detail}</InfoPopover>
            </div>
          </CardContent>
        </Card>
        <Card className="border-border bg-card">
          <CardContent className="p-6 text-center">
            <div className="text-4xl font-bold">{country.article_count}</div>
            <div className="mt-2 text-sm text-muted-foreground">Статей</div>
          </CardContent>
        </Card>
        <Card className="border-border bg-card">
          <CardContent className="p-6 text-center">
            <div className="text-4xl font-bold">{country.divergence.toFixed(2)}</div>
            <div className="mt-2 text-sm text-muted-foreground flex items-center justify-center gap-1">
              Расхождение
              <InfoPopover title="Расхождение нарративов">{glossary.divergence.detail}</InfoPopover>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Narrative Xray */}
      <ErrorBoundary name="Нарративный расклад">
        <NarrativeXrayExpanded code={code} days={PERIOD_DAYS[period] || 30} />
      </ErrorBoundary>

      {/* Digest */}
      {digest && digest.digest && (
        <Card className="border-border bg-card">
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="text-lg flex items-center gap-2">📋 Дайджест
                <InfoPopover title="Дайджест">
                  <p>AI-сгенерированная сводка ключевых событий за период.</p>
                  <p>Включает ссылки на оригинальные статьи. Обновляется при каждом пересчёте температуры.</p>
                </InfoPopover>
              </CardTitle>
              {digest.generated_at && (
                <span className="text-xs text-muted-foreground">
                  {formatDate(digest.generated_at)}
                </span>
              )}
            </div>
          </CardHeader>
          <CardContent className="prose prose-invert prose-sm max-w-none text-sm text-muted-foreground">
            <ReactMarkdown
              components={{
                a: ({ href, children }) => (
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-blue-400 underline hover:text-blue-300"
                  >
                    {children}
                  </a>
                ),
              }}
            >
              {digest.digest}
            </ReactMarkdown>
          </CardContent>
        </Card>
      )}

      {/* Threads / storylines */}
      <ErrorBoundary name="Сюжеты">
      {threads.length > 0 && (() => {
        const PHASE_CFG: Record<string, { emoji: string; color: string; label: string }> = {
          emerging:   { emoji: "🌱", color: "#3b82f6", label: "Зарождение" },
          escalating: { emoji: "📈", color: "#f59e0b", label: "Эскалация" },
          peak:       { emoji: "🔥", color: "#ef4444", label: "Пик" },
          cooling:    { emoji: "❄️", color: "#06b6d4", label: "Затухание" },
          resolved:   { emoji: "✅", color: "#22c55e", label: "Завершён" },
        };
        const PHASE_ORDER = ["emerging", "escalating", "peak", "cooling", "resolved"];

        const sorted = [...threads].sort((a, b) => b.importance_score - a.importance_score);
        const hero = sorted[0];
        const rest = sorted.slice(1, 6);
        const heroPhase = PHASE_CFG[hero.arc_phase] || PHASE_CFG.emerging;

        return (
          <div className="space-y-4">
            <SectionHeader
              icon="🧵"
              title={`Сюжеты (${threads.length})`}
              description="Ключевые сюжетные линии по этой стране — от зарождения до завершения"
              infoTitle="Сюжеты"
              infoContent={glossary.threads.detail}
            />

            {/* Hero thread */}
            <Link href={`/threads/${hero.id}`}>
              <div
                className="rounded-xl border-2 p-5 hover:brightness-110 transition-all cursor-pointer"
                style={{
                  borderColor: heroPhase.color + "33",
                  background: `linear-gradient(135deg, ${heroPhase.color}08 0%, rgba(10,10,15,0.95) 60%)`,
                }}
              >
                <div className="flex flex-wrap items-center gap-2 mb-2">
                  <span
                    className="text-xs px-2 py-0.5 rounded-full font-semibold"
                    style={{ backgroundColor: heroPhase.color + "22", color: heroPhase.color }}
                  >
                    {heroPhase.emoji} {heroPhase.label}
                  </span>
                  <Badge variant="outline" className="text-[10px]">★ {hero.importance_score.toFixed(0)}</Badge>
                  <Badge variant="outline" className="text-[10px]">📰 {hero.article_count} статей</Badge>
                  {(hero as any).velocity > 1 && (
                    <span className="text-xs text-orange-400">⚡ {((hero as any).velocity).toFixed(1)} ст/день</span>
                  )}
                </div>
                <h3 className="text-xl font-bold hover:text-blue-400 transition-colors">{hero.title}</h3>
                {/* Arc bar */}
                <div className="flex gap-1 my-3">
                  {PHASE_ORDER.map((p, i) => (
                    <div
                      key={p}
                      className="flex-1 rounded-full h-1.5"
                      style={{
                        backgroundColor: i <= PHASE_ORDER.indexOf(hero.arc_phase)
                          ? (PHASE_CFG[p]?.color || "#555")
                          : "rgba(255,255,255,0.06)",
                      }}
                    />
                  ))}
                </div>
                <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
                  <span style={{ color: hero.avg_sentiment > 0.05 ? "#22c55e" : hero.avg_sentiment < -0.05 ? "#ef4444" : "#eab308" }}>
                    💬 Sentiment: {hero.avg_sentiment.toFixed(2)}
                  </span>
                  <span>⚡ Макс. уровень: {hero.max_action_level}</span>
                  <span>{formatDate(hero.first_seen)} → {formatDate(hero.last_seen)}</span>
                </div>
                {hero.narrative && (
                  <p className="mt-3 text-sm text-muted-foreground line-clamp-2">{hero.narrative}</p>
                )}
              </div>
            </Link>

            {/* Rest of threads */}
            {rest.length > 0 && (
              <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
                {rest.map((thread) => {
                  const phase = PHASE_CFG[thread.arc_phase] || PHASE_CFG.emerging;
                  return (
                    <Link key={thread.id} href={`/threads/${thread.id}`}>
                      <div
                        className="rounded-lg border p-4 hover:border-white/20 transition-all cursor-pointer h-full"
                        style={{ borderColor: phase.color + "20" }}
                      >
                        <div className="flex items-center gap-2 mb-2">
                          <span
                            className="text-[10px] px-1.5 py-0.5 rounded-full"
                            style={{ backgroundColor: phase.color + "22", color: phase.color }}
                          >
                            {phase.emoji} {phase.label}
                          </span>
                          <span className="text-xs text-muted-foreground ml-auto">★ {thread.importance_score.toFixed(0)}</span>
                        </div>
                        <h4 className="font-semibold text-sm hover:text-blue-400 transition-colors line-clamp-2">{thread.title}</h4>
                        {/* Mini arc bar */}
                        <div className="flex gap-0.5 my-2">
                          {PHASE_ORDER.map((p, i) => (
                            <div
                              key={p}
                              className="flex-1 rounded-full h-1"
                              style={{
                                backgroundColor: i <= PHASE_ORDER.indexOf(thread.arc_phase)
                                  ? (PHASE_CFG[p]?.color || "#555")
                                  : "rgba(255,255,255,0.06)",
                              }}
                            />
                          ))}
                        </div>
                        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                          <span>📰 {thread.article_count}</span>
                          <span style={{ color: thread.avg_sentiment > 0.05 ? "#22c55e" : thread.avg_sentiment < -0.05 ? "#ef4444" : "#eab308" }}>
                            💬 {thread.avg_sentiment.toFixed(2)}
                          </span>
                          <span>{formatDate(thread.last_seen)}</span>
                        </div>
                        {thread.narrative && (
                          <p className="mt-2 text-xs text-muted-foreground line-clamp-2">{thread.narrative}</p>
                        )}
                      </div>
                    </Link>
                  );
                })}
              </div>
            )}

            {/* Link to full threads page filtered by country */}
            <div className="text-center">
              <Link
                href={`/threads`}
                className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
              >
                Все сюжеты на странице «Сюжеты» →
              </Link>
            </div>
          </div>
        );
      })()}
      </ErrorBoundary>

      {/* Temperature Chart */}
      <Card className="border-border bg-card">
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">🌡️ Температура
            <InfoPopover title="Медийная температура">{glossary.temperature.detail}</InfoPopover>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <TemperatureChart data={tempData} />
        </CardContent>
      </Card>

      {/* UN Votes & Trade — 2 columns */}
      <div className="grid gap-6 md:grid-cols-2">
        <Card className="border-border bg-card">
          <CardHeader>
            <CardTitle className="text-lg flex items-center gap-2">🗳️ Голосования в ООН
              <InfoPopover title="Голосования в ООН">{glossary.unVotes.detail}</InfoPopover>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <UNVotesChart data={unVotes} />
          </CardContent>
        </Card>
        <Card className="border-border bg-card">
          <CardHeader>
            <CardTitle className="text-lg flex items-center gap-2">💰 Торговля с Россией
              <InfoPopover title="Торговые данные">{glossary.trade.detail}</InfoPopover>
            </CardTitle>
          </CardHeader>
          <CardContent>
            <TradeChart data={tradeData} />
          </CardContent>
        </Card>
      </div>

      {/* Events */}
      <Card className="border-border bg-card">
        <CardHeader>
          <div className="flex flex-col gap-3">
            <CardTitle className="text-lg flex items-center gap-2">
              📅 События ({filteredEvents.length})
              <InfoPopover title="Action Level">{glossary.actionLevel.detail}</InfoPopover>
            </CardTitle>
            <div className="flex flex-wrap items-center gap-1">
              <span className="mr-1 text-[11px] text-muted-foreground">Тип:</span>
              <button
                onClick={() => setTypeFilter(null)}
                className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-all ${
                  !typeFilter
                    ? "bg-white/15 text-foreground"
                    : "text-muted-foreground hover:bg-white/5"
                }`}
              >
                Все
              </button>
              {eventTypes.map((t) => (
                <button
                  key={t}
                  onClick={() => setTypeFilter(typeFilter === t ? null : t)}
                  className={`rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-all ${
                    typeFilter === t
                      ? EVENT_TYPE_COLORS[t] || "bg-white/15 text-foreground"
                      : "border-transparent text-muted-foreground hover:bg-white/5"
                  }`}
                >
                  {EVENT_TYPE_LABELS[t] || t}
                </button>
              ))}
            </div>
            {availableTiers.length > 0 && (
              <div className="flex flex-wrap items-center gap-1">
                <span className="mr-1 text-[11px] text-muted-foreground">Источник:</span>
                <button
                  onClick={() => setTierFilter(null)}
                  className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-all ${
                    !tierFilter
                      ? "bg-white/15 text-foreground"
                      : "text-muted-foreground hover:bg-white/5"
                  }`}
                >
                  Все
                </button>
                {availableTiers.map((t) => (
                  <button
                    key={t}
                    onClick={() => setTierFilter(tierFilter === t ? null : t)}
                    className={`rounded-full border px-2.5 py-0.5 text-[11px] font-medium transition-all ${
                      tierFilter === t
                        ? TIER_COLORS[t] || "bg-white/15 text-foreground"
                        : "border-transparent text-muted-foreground hover:bg-white/5"
                    }`}
                  >
                    {TIER_LABELS[t] || t}
                  </button>
                ))}
              </div>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {eventsLoading ? (
            <div className="space-y-3">
              {[...Array(5)].map((_, i) => (
                <Skeleton key={i} className="h-12 w-full" />
              ))}
            </div>
          ) : filteredEvents.length === 0 ? (
            <p className="text-sm text-muted-foreground">Нет событий за выбранный период.</p>
          ) : (
            <>
              {/* Desktop table */}
              <div className="hidden md:block">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-left text-xs text-muted-foreground">
                      <th className="pb-2 pr-3">Дата</th>
                      <th className="pb-2 pr-3">Заголовок</th>
                      <th className="pb-2 pr-3">Источник</th>
                      <th className="pb-2 pr-3">Тип</th>
                      <th className="pb-2 pr-3">Влияние</th>
                      <th className="pb-2">Sentiment</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleEvents.map((ev, i) => (
                      <tr key={i} className="border-b border-border/50 hover:bg-white/[0.02]">
                        <td className="py-2.5 pr-3 text-xs text-muted-foreground whitespace-nowrap">
                          {formatDate(ev.published_at)}
                        </td>
                        <td className="py-2.5 pr-3">
                          <a
                            href={ev.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-foreground hover:text-blue-400 hover:underline"
                          >
                            {ev.title}
                          </a>
                        </td>
                        <td className="py-2.5 pr-3 text-xs text-muted-foreground whitespace-nowrap">
                          {ev.source}
                        </td>
                        <td className="py-2.5 pr-3">
                          <Badge
                            variant="outline"
                            className={`text-[10px] ${EVENT_TYPE_COLORS[ev.event_type] || ""}`}
                          >
                            {EVENT_TYPE_LABELS[ev.event_type] || ev.event_type}
                          </Badge>
                        </td>
                        <td className="py-2.5 pr-3 text-center">
                          <span title={`Уровень ${ev.action_level}`}>
                            {actionIcon(ev.action_level)} {ev.action_level}
                          </span>
                        </td>
                        <td className="py-2.5">{sentimentBar(ev.sentiment)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {/* Mobile cards */}
              <div className="space-y-3 md:hidden">
                {visibleEvents.map((ev, i) => (
                  <div
                    key={i}
                    className="rounded-lg border border-border/50 bg-white/[0.02] p-3"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <a
                        href={ev.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-sm font-medium text-foreground hover:text-blue-400 hover:underline"
                      >
                        {ev.title}
                      </a>
                      <span className="shrink-0 text-sm" title={`Уровень ${ev.action_level}`}>
                        {actionIcon(ev.action_level)} {ev.action_level}
                      </span>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <span className="text-[11px] text-muted-foreground">
                        {formatDate(ev.published_at)}
                      </span>
                      <span className="text-[11px] text-muted-foreground">·</span>
                      <span className="text-[11px] text-muted-foreground">{ev.source}</span>
                      <Badge
                        variant="outline"
                        className={`text-[10px] ${EVENT_TYPE_COLORS[ev.event_type] || ""}`}
                      >
                        {EVENT_TYPE_LABELS[ev.event_type] || ev.event_type}
                      </Badge>
                    </div>
                    <div className="mt-2">{sentimentBar(ev.sentiment)}</div>
                  </div>
                ))}
              </div>

              {/* Show more */}
              {visibleCount < filteredEvents.length && (
                <div className="mt-4 text-center">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => setVisibleCount((v) => v + 20)}
                    className="text-muted-foreground hover:text-foreground"
                  >
                    Показать ещё ({filteredEvents.length - visibleCount} осталось)
                  </Button>
                </div>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
