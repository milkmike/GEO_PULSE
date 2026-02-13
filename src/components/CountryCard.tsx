"use client";

import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { Country } from "@/lib/api";
import { temperatureColor, temperatureLabel, trendIcon } from "@/lib/api";

interface CountryCardProps {
  country: Country;
}

function MiniSparkline({ data }: { data: number[] }) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const w = 64;
  const h = 28;
  const pad = 2;
  const points = data.map((v, i) => {
    const x = pad + (i / (data.length - 1)) * (w - pad * 2);
    const y = h - pad - ((v - min) / range) * (h - pad * 2);
    return `${x},${y}`;
  });
  const last = data[data.length - 1];
  const color = last <= -10 ? "#3b82f6" : last <= 0 ? "#60a5fa" : last <= 10 ? "#eab308" : "#ef4444";
  return (
    <svg width={w} height={h} className="shrink-0">
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={0.6}
      />
    </svg>
  );
}

export default function CountryCard({ country }: CountryCardProps) {
  const temp = country.temperature;
  const color = temperatureColor(temp);
  const label = temperatureLabel(temp);
  const trend = trendIcon(country.trend);
  const topThread = (country as any).top_thread;
  const activeThreads = (country as any).active_threads || 0;
  const sparkline = (country as any).sparkline || [];
  const divergence = country.divergence;

  return (
    <Link href={`/country/${country.code}`}>
      <Card className="group cursor-pointer border-border bg-card transition-all hover:border-blue-500/30 hover:bg-white/[0.06] h-full">
        <CardContent className="p-4 flex flex-col h-full">
          {/* Header: name + temperature */}
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0 flex-1">
              <h3 className="text-base font-semibold text-foreground">
                {country.name}
              </h3>
              <div className="mt-0.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                <span>{country.article_count} ст.</span>
                {activeThreads > 0 && (
                  <span className="text-blue-400/60">🧵 {activeThreads}</span>
                )}
              </div>
            </div>
            <div className="text-right shrink-0">
              <div className="flex items-center gap-1">
                <span
                  className="text-2xl font-bold tabular-nums"
                  style={{ color }}
                >
                  {temp > 0 ? "+" : ""}
                  {temp.toFixed(1)}°
                </span>
                <span className="text-sm" style={{ color }}>
                  {trend}
                </span>
              </div>
              <Badge
                variant="secondary"
                className="mt-0.5 text-[10px]"
                style={{ color, borderColor: `${color}33` }}
              >
                {label}
              </Badge>
            </div>
          </div>

          {/* Sparkline + divergence row */}
          <div className="mt-3 flex items-center gap-3">
            {sparkline.length >= 2 && (
              <MiniSparkline data={sparkline} />
            )}
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between text-[10px] text-muted-foreground mb-1">
                <span>Расхождение</span>
                <span className={`font-medium tabular-nums ${
                  divergence >= 0.8 ? "text-red-400/70" : divergence >= 0.3 ? "text-amber-400/70" : "text-green-400/70"
                }`}>{divergence.toFixed(2)}</span>
              </div>
              <div className="h-1 w-full overflow-hidden rounded-full bg-white/5">
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${Math.min(divergence / 2.0, 1) * 100}%`,
                    backgroundColor: divergence >= 0.8 ? "#ef4444" : divergence >= 0.3 ? "#eab308" : "#22c55e",
                  }}
                />
              </div>
            </div>
          </div>

          {/* Top thread */}
          {topThread && (
            <div className="mt-3 rounded-lg bg-white/[0.03] border border-white/[0.04] px-3 py-2">
              <div className="text-[11px] text-white/60 line-clamp-2 leading-relaxed">
                {topThread.title}
              </div>
              <div className="mt-1 flex items-center gap-2 text-[10px] text-white/25">
                <span>{topThread.article_count} ст.</span>
                <span className={topThread.sentiment > 0 ? "text-green-400/50" : topThread.sentiment < 0 ? "text-red-400/50" : ""}>
                  {topThread.sentiment >= 0 ? "+" : ""}{topThread.sentiment.toFixed(2)}
                </span>
              </div>
            </div>
          )}

          {/* Footer links */}
          <div className="mt-auto pt-3 flex gap-3 text-[10px]">
            <Link
              href={`/threads?country=${country.code}&from=/country`}
              className="text-blue-400/60 hover:text-blue-400 transition-colors"
              onClick={(e) => e.stopPropagation()}
            >
              🧵 Сюжеты →
            </Link>
            <Link
              href={`/vox?country=${country.code}&from=/country`}
              className="text-blue-400/60 hover:text-blue-400 transition-colors"
              onClick={(e) => e.stopPropagation()}
            >
              📢 VOX →
            </Link>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
