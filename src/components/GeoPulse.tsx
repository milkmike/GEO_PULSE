"use client";

import { useEffect, useState, useMemo } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import SectionHeader from "@/components/SectionHeader";
import {
  ResponsiveContainer,
  ScatterChart,
  Scatter,
  XAxis,
  YAxis,
  ZAxis,
  Tooltip as RechartsTooltip,
  Cell,
  ReferenceLine,
  LineChart,
  Line,
} from "recharts";
import {
  getCountries,
  getHighImpactEvents,
  getCoverage,
  COUNTRY_FLAGS,
  COUNTRY_NAMES,
  temperatureColor,
  type Country,
  type HighImpactEvent,
  type CoverageCountry,
} from "@/lib/api";

// ── Helpers ─────────────────────────────────────────────

function relativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.round(diff / 60000);
  if (mins < 1) return "только что";
  if (mins < 60) return `${mins}м назад`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}ч назад`;
  const days = Math.round(hours / 24);
  return `${days}д назад`;
}

function sentimentIndicator(s: number): string {
  if (s <= -1.5) return "🔴";
  if (s < 0) return "🟠";
  if (s < 1) return "🟡";
  return "🟢";
}

const TIER_BADGE: Record<string, { label: string; cls: string }> = {
  official: { label: "Офиц.", cls: "border-blue-500/30 text-blue-400" },
  mainstream: { label: "Мейнстрим", cls: "border-purple-500/30 text-purple-400" },
  analytics: { label: "Аналитика", cls: "border-cyan-500/30 text-cyan-400" },
  independent: { label: "Независ.", cls: "border-emerald-500/30 text-emerald-400" },
  domestic_opposition: { label: "Оппозиция", cls: "border-orange-500/30 text-orange-400" },
  western_proxy: { label: "Запад", cls: "border-red-500/30 text-red-400" },
  social: { label: "Соцсети", cls: "border-yellow-500/30 text-yellow-400" },
};

// ── Custom Scatter Shape ────────────────────────────────

interface BubbleProps {
  cx?: number;
  cy?: number;
  payload?: {
    code: string;
    flag: string;
    temperature: number;
    article_count: number;
    action_level: number;
    size: number;
  };
}

function HotSpotBubble(props: BubbleProps) {
  const { cx = 0, cy = 0, payload } = props;
  if (!payload) return null;

  const r = Math.max(18, Math.min(40, 14 + payload.size * 0.5));
  const color = temperatureColor(payload.temperature);
  const shouldPulse = (payload.action_level ?? 0) >= 4;

  return (
    <g>
      {shouldPulse && (
        <circle
          cx={cx}
          cy={cy}
          r={r + 6}
          fill="none"
          stroke={color}
          strokeWidth={2}
          opacity={0.4}
        >
          <animate
            attributeName="r"
            values={`${r + 2};${r + 12};${r + 2}`}
            dur="2s"
            repeatCount="indefinite"
          />
          <animate
            attributeName="opacity"
            values="0.5;0.1;0.5"
            dur="2s"
            repeatCount="indefinite"
          />
        </circle>
      )}
      <circle
        cx={cx}
        cy={cy}
        r={r}
        fill={color}
        fillOpacity={0.2}
        stroke={color}
        strokeWidth={1.5}
        strokeOpacity={0.6}
      />
      <text
        x={cx}
        y={cy - 4}
        textAnchor="middle"
        fontSize={r > 24 ? 18 : 14}
        dominantBaseline="central"
      >
        {payload.flag}
      </text>
      <text
        x={cx}
        y={cy + (r > 24 ? 14 : 10)}
        textAnchor="middle"
        fontSize={9}
        fill="rgba(255,255,255,0.7)"
        fontWeight={600}
        dominantBaseline="central"
      >
        {payload.code}
      </text>
    </g>
  );
}

// ── Custom Tooltip ──────────────────────────────────────

interface TooltipPayloadItem {
  payload?: {
    code: string;
    name: string;
    flag: string;
    temperature: number;
    trend: string;
    article_count: number;
    divergence: number;
    raw_sentiment: number;
  };
}

function HotSpotTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: TooltipPayloadItem[];
}) {
  if (!active || !payload?.[0]?.payload) return null;
  const d = payload[0].payload;
  const trendIcon = d.trend === "rising" ? "↑" : d.trend === "falling" ? "↓" : "→";

  return (
    <div className="rounded-lg border border-white/10 bg-zinc-900/95 px-3 py-2 text-xs shadow-xl backdrop-blur">
      <div className="font-medium text-white mb-1">
        {d.flag} {d.name}
      </div>
      <div className="space-y-0.5 text-muted-foreground">
        <div>
          Температура:{" "}
          <span style={{ color: temperatureColor(d.temperature) }} className="font-bold">
            {d.temperature?.toFixed(1)}°
          </span>
        </div>
        <div>Тренд: {trendIcon} {d.trend === "rising" ? "растёт" : d.trend === "falling" ? "падает" : "стабильно"}</div>
        <div>Статей: {d.article_count}</div>
        <div>Sentiment: {d.raw_sentiment?.toFixed(2)}</div>
        <div>Divergence: {d.divergence?.toFixed(2)}</div>
      </div>
    </div>
  );
}

// ── Main Component ──────────────────────────────────────

export default function GeoPulse({ period }: { period: number }) {
  const [countries, setCountries] = useState<Country[]>([]);
  const [events, setEvents] = useState<HighImpactEvent[]>([]);
  const [coverage, setCoverage] = useState<CoverageCountry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchData() {
      setLoading(true);
      try {
        const [countriesRes, eventsRes, coverageRes] = await Promise.all([
          getCountries(period),
          getHighImpactEvents(period, 3, 10),
          getCoverage(Math.min(period, 30)),
        ]);
        setCountries(countriesRes.countries);
        setEvents(eventsRes.events);
        setCoverage(coverageRes.countries);
      } catch (err) {
        console.error("GeoPulse fetch error:", err);
      } finally {
        setLoading(false);
      }
    }
    fetchData();
  }, [period]);

  // ── Derived: Hot Spots ──────────────────────────────

  const hotSpots = useMemo(() => {
    return [...countries]
      .filter((c) => c.temperature != null)
      .sort((a, b) => (b.article_count || 0) - (a.article_count || 0))
      .slice(0, 10)
      .map((c) => ({
        code: c.code,
        name: c.name,
        flag: COUNTRY_FLAGS[c.code] || "🏳️",
        temperature: c.temperature ?? 0,
        raw_sentiment: c.raw_sentiment ?? 0,
        trend: c.trend ?? "stable",
        article_count: c.article_count || 0,
        divergence: c.divergence ?? 0,
        // scatter coords
        x: c.raw_sentiment ?? 0,
        y: Math.random() * 2 - 1, // jitter Y to spread bubbles
        size: c.article_count || 10,
        action_level: Math.abs(c.temperature ?? 0) > 20 ? 4 : 2,
      }));
  }, [countries]);

  // ── Derived: Activity sparklines ────────────────────

  const activityData = useMemo(() => {
    return [...countries]
      .filter((c) => c.temperature != null)
      .sort((a, b) => Math.abs(b.temperature ?? 0) - Math.abs(a.temperature ?? 0))
      .slice(0, 10)
      .map((c) => {
        const covCountry = coverage.find((cv) => cv.code === c.code);
        const sparkline = covCountry
          ? covCountry.days.map((d) => ({ date: d.date, count: d.total }))
          : [];
        return {
          code: c.code,
          name: COUNTRY_NAMES[c.code] || c.name,
          flag: COUNTRY_FLAGS[c.code] || "🏳️",
          temperature: c.temperature ?? 0,
          sparkline,
        };
      });
  }, [countries, coverage]);

  // ── Skeleton ────────────────────────────────────────

  if (loading) {
    return (
      <section className="space-y-3">
        <SectionHeader icon="🔥" title="Геополитический пульс" description="Загрузка данных…" />
        <div className="grid gap-3 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-32 animate-pulse rounded-lg border border-border bg-card" />
          ))}
        </div>
      </section>
    );
  }

  return (
    <section className="space-y-8">
      <SectionHeader
        icon="🔥"
        title="Геополитический пульс"
        description="Горячие точки, ключевые события и индекс активности в реальном времени"
        infoTitle="Геополитический пульс"
        infoContent={
          <>
            <p><strong>Горячие точки</strong> — визуализация 10 ключевых стран. Позиция по X = sentiment, размер = количество статей, цвет = температура.</p>
            <p><strong>Лента высокого напряжения</strong> — последние события с высоким уровнем действия (action_level ≥ 3).</p>
            <p><strong>Индекс активности</strong> — спарклайн-графики публикационной активности за последние дни.</p>
          </>
        }
      />

      {/* ── Section 1: Hot Spots ──────────────────────── */}
      {hotSpots.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-muted-foreground/80 flex items-center gap-1.5">
            <span>🌡️</span> Горячие точки
          </h3>
          <Card className="border-border bg-card">
            <CardContent className="p-4">
              <ResponsiveContainer width="100%" height={280}>
                <ScatterChart margin={{ top: 20, right: 30, bottom: 30, left: 30 }}>
                  <XAxis
                    type="number"
                    dataKey="x"
                    domain={[-3, 3]}
                    tick={{ fill: "rgba(255,255,255,0.4)", fontSize: 10 }}
                    axisLine={false}
                    tickLine={false}
                    label={{
                      value: "← Негативный          Sentiment          Позитивный →",
                      position: "insideBottom",
                      offset: -15,
                      fill: "rgba(255,255,255,0.3)",
                      fontSize: 10,
                    }}
                  />
                  <YAxis
                    type="number"
                    dataKey="y"
                    domain={[-2, 2]}
                    hide
                  />
                  <ZAxis type="number" dataKey="size" range={[200, 1200]} />
                  <ReferenceLine x={0} stroke="rgba(255,255,255,0.08)" />
                  <RechartsTooltip
                    content={<HotSpotTooltip />}
                    cursor={false}
                  />
                  <Scatter
                    data={hotSpots}
                    shape={<HotSpotBubble />}
                  >
                    {hotSpots.map((entry) => (
                      <Cell key={entry.code} fill={temperatureColor(entry.temperature)} />
                    ))}
                  </Scatter>
                </ScatterChart>
              </ResponsiveContainer>

              {/* Legend */}
              <div className="flex items-center justify-center gap-4 mt-2 pt-2 border-t border-border/30">
                {[
                  { temp: -20, label: "Холодно" },
                  { temp: 0, label: "Нейтрально" },
                  { temp: 15, label: "Тепло" },
                  { temp: 25, label: "Горячо" },
                ].map((l) => (
                  <div key={l.label} className="flex items-center gap-1.5">
                    <div
                      className="w-3 h-3 rounded-full"
                      style={{ backgroundColor: temperatureColor(l.temp) }}
                    />
                    <span className="text-[10px] text-muted-foreground/60">{l.label}</span>
                  </div>
                ))}
                <div className="flex items-center gap-1.5 ml-2">
                  <div className="w-3 h-3 rounded-full border-2 border-red-400 animate-pulse" />
                  <span className="text-[10px] text-muted-foreground/60">Пульсация = высокий action_level</span>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* ── Section 2: High Voltage Feed ─────────────── */}
      {events.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-muted-foreground/80 flex items-center gap-1.5">
            <span>⚡</span> Лента высокого напряжения
          </h3>
          <div className="grid gap-2 md:grid-cols-2">
            {events.map((event, i) => {
              const tierInfo = TIER_BADGE[event.tier] || { label: event.tier, cls: "border-zinc-500/30 text-zinc-400" };
              return (
                <Card
                  key={`${event.url}-${i}`}
                  className="border-border bg-card hover:bg-white/[0.02] transition-all"
                  style={{
                    animation: `fadeInUp 0.4s ease-out ${i * 0.06}s both`,
                  }}
                >
                  <CardContent className="p-3">
                    <div className="flex items-start gap-2">
                      {/* Sentiment + flag */}
                      <div className="shrink-0 flex flex-col items-center gap-0.5 pt-0.5">
                        <span className="text-xs">{sentimentIndicator(event.sentiment)}</span>
                        <span className="text-sm">{COUNTRY_FLAGS[event.country_code] || "🏳️"}</span>
                      </div>

                      {/* Content */}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-1.5 mb-1">
                          <span className="text-[10px] text-muted-foreground/60">
                            {event.country_name}
                          </span>
                          <span className="text-[10px]">
                            {event.action_level >= 4 ? "💥" : "⚡"}
                          </span>
                          <span className="text-[10px] text-muted-foreground/40">
                            AL:{event.action_level}
                          </span>
                        </div>

                        <a
                          href={event.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs font-medium text-white/90 hover:text-white line-clamp-2 leading-tight"
                        >
                          {event.title}
                        </a>

                        <div className="flex items-center gap-2 mt-1.5">
                          <span className="text-[10px] text-muted-foreground/50 truncate max-w-[120px]">
                            {event.source}
                          </span>
                          <Badge variant="outline" className={`text-[9px] px-1 py-0 ${tierInfo.cls}`}>
                            {tierInfo.label}
                          </Badge>
                          <span className="text-[10px] text-muted-foreground/40 ml-auto shrink-0">
                            {event.published_at ? relativeTime(event.published_at) : ""}
                          </span>
                        </div>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>
      )}

      {/* ── Section 3: Activity Index ────────────────── */}
      {activityData.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium text-muted-foreground/80 flex items-center gap-1.5">
            <span>📈</span> Индекс активности
          </h3>
          <Card className="border-border bg-card">
            <CardContent className="p-4 space-y-1">
              {activityData.map((country) => (
                <div
                  key={country.code}
                  className="flex items-center gap-3 py-1.5 border-b border-border/20 last:border-0"
                >
                  {/* Flag + Name */}
                  <div className="w-28 shrink-0 flex items-center gap-1.5">
                    <span className="text-sm">{country.flag}</span>
                    <span className="text-xs text-muted-foreground truncate">{country.name}</span>
                  </div>

                  {/* Sparkline */}
                  <div className="flex-1 h-8">
                    {country.sparkline.length > 1 ? (
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={country.sparkline} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
                          <Line
                            type="monotone"
                            dataKey="count"
                            stroke={temperatureColor(country.temperature)}
                            strokeWidth={1.5}
                            dot={false}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    ) : (
                      <div className="h-full flex items-center">
                        <span className="text-[10px] text-muted-foreground/30">нет данных</span>
                      </div>
                    )}
                  </div>

                  {/* Temperature */}
                  <div className="w-14 shrink-0 text-right">
                    <span
                      className="text-xs font-bold"
                      style={{ color: temperatureColor(country.temperature) }}
                    >
                      {country.temperature.toFixed(1)}°
                    </span>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        </div>
      )}

      {/* CSS Animation */}
      <style jsx global>{`
        @keyframes fadeInUp {
          from {
            opacity: 0;
            transform: translateY(8px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
      `}</style>
    </section>
  );
}
