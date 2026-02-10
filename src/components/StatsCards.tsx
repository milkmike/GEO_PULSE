"use client";

import type { Stats } from "@/lib/api";

interface StatsCardsProps {
  stats: Stats | null;
  loading?: boolean;
}

const metrics = [
  { key: "total_articles" as const, label: "статей", icon: "📰" },
  { key: "total_analyzed" as const, label: "проанализировано", icon: "🔬" },
  { key: "total_relevant" as const, label: "релевантных", icon: "✅" },
  { key: "active_sources" as const, label: "источников", icon: "📡" },
];

export default function StatsCards({ stats, loading }: StatsCardsProps) {
  if (loading || !stats) {
    return (
      <div className="h-5 w-64 animate-pulse rounded bg-white/5" />
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-white/40">
      {metrics.map((m, i) => (
        <span key={m.key} className="flex items-center gap-1.5">
          <span>{m.icon}</span>
          <span className="font-semibold text-white/60 tabular-nums">
            {stats[m.key].toLocaleString("ru-RU")}
          </span>
          <span>{m.label}</span>
          {i < metrics.length - 1 && (
            <span className="ml-2 text-white/10">·</span>
          )}
        </span>
      ))}
    </div>
  );
}
