"use client";

import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { Country } from "@/lib/api";
import { temperatureColor, temperatureLabel, trendIcon } from "@/lib/api";

interface CountryCardProps {
  country: Country;
  sparklineData?: number[];
}

function MiniSparkline({ data }: { data: number[] }) {
  if (data.length < 2) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const w = 80;
  const h = 40;
  const pad = 2;
  const points = data.map((v, i) => {
    const x = pad + (i / (data.length - 1)) * (w - pad * 2);
    const y = h - pad - ((v - min) / range) * (h - pad * 2);
    return `${x},${y}`;
  });
  const avg = data.reduce((s, v) => s + v, 0) / data.length;
  const color = avg <= 0 ? "#3b82f6" : avg <= 10 ? "#eab308" : "#ef4444";
  return (
    <svg width={w} height={h} className="shrink-0">
      <polyline
        points={points.join(" ")}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export default function CountryCard({ country, sparklineData }: CountryCardProps) {
  const temp = country.temperature;
  const color = temperatureColor(temp);
  const label = temperatureLabel(temp);
  const trend = trendIcon(country.trend);

  return (
    <Link href={`/country/${country.code}`}>
      <Card className="group cursor-pointer border-border bg-card transition-all hover:border-blue-500/30 hover:bg-white/[0.06]">
        <CardContent className="p-4">
          <div className="flex items-start justify-between">
            <div>
              <h3 className="text-base font-semibold text-foreground">
                {country.name}
              </h3>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {country.article_count} статей
              </p>
            </div>
            <div className="text-right">
              <div className="flex items-center gap-1">
                <span
                  className="text-2xl font-bold tabular-nums"
                  style={{ color }}
                >
                  {temp > 0 ? "+" : ""}
                  {temp.toFixed(1)}°
                </span>
                <span
                  className="text-sm"
                  style={{ color }}
                >
                  {trend}
                </span>
              </div>
              <Badge
                variant="secondary"
                className="mt-1 text-[10px]"
                style={{ color, borderColor: `${color}33` }}
              >
                {label}
              </Badge>
            </div>
          </div>
          {/* Sparkline */}
          {sparklineData && sparklineData.length >= 2 && (
            <div className="mt-3 flex items-center justify-center">
              <MiniSparkline data={sparklineData} />
            </div>
          )}
          {/* Mini divergence bar */}
          <div className="mt-3">
            <div className="flex items-center justify-between text-[10px] text-muted-foreground">
              <span>Расхождение</span>
              <span>{country.divergence.toFixed(2)}</span>
            </div>
            <div className="mt-1 h-1 w-full overflow-hidden rounded-full bg-white/5">
              <div
                className="h-full rounded-full"
                style={{
                  width: `${Math.min(country.divergence / 1.0, 1) * 100}%`,
                  backgroundColor: country.divergence > 0.5 ? "#ef4444" : country.divergence >= 0.2 ? "#eab308" : "#22c55e",
                }}
              />
            </div>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
