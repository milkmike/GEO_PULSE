"use client";

import { Card, CardContent } from "@/components/ui/card";
import type { Stats } from "@/lib/api";

interface StatsCardsProps {
  stats: Stats | null;
  loading?: boolean;
}

const metrics = [
  { key: "total_articles" as const, label: "Статей собрано", icon: "📰" },
  { key: "total_analyzed" as const, label: "Проанализировано", icon: "🔬" },
  { key: "total_relevant" as const, label: "Релевантных", icon: "✅" },
  { key: "active_sources" as const, label: "Источников", icon: "📡" },
];

export default function StatsCards({ stats, loading }: StatsCardsProps) {
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
      {metrics.map((m) => (
        <Card key={m.key} className="border-border bg-card">
          <CardContent className="p-4">
            <div className="flex items-center gap-2">
              <span className="text-lg">{m.icon}</span>
              <span className="text-xs text-muted-foreground">{m.label}</span>
            </div>
            <div className="mt-2 text-2xl font-bold tabular-nums text-foreground">
              {loading || !stats ? (
                <div className="h-8 w-20 animate-pulse rounded bg-white/5" />
              ) : (
                stats[m.key].toLocaleString("ru-RU")
              )}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
