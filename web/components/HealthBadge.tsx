"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Health } from "@/lib/types";

// Readers get a freshness dot; collection percentages stay in the tooltip and /admin.
const DOT: Record<Health["verdict"], string> = {
  HEALTHY: "bg-ally",
  WARNING: "bg-cooling",
  DEGRADED: "bg-cooling",
  UNHEALTHY: "bg-cooling",
};

const VERDICT_RU: Record<Health["verdict"], string> = {
  HEALTHY: "Данные обновляются в обычном режиме",
  WARNING: "Часть источников обновляется с задержкой",
  DEGRADED: "Часть источников сейчас недоступна",
  UNHEALTHY: "Большая часть источников сейчас недоступна",
};

export default function HealthBadge() {
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    const load = () => api.health().then(setHealth).catch(() => {});
    load();
    const t = setInterval(load, 120_000);
    return () => clearInterval(t);
  }, []);

  if (!health) return null;
  const label = VERDICT_RU[health.verdict];
  return (
    <span
      className="inline-flex items-center"
      title={`${label}. Работают ${health.sources_ok} из ${health.sources_total} источников.`}
    >
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${DOT[health.verdict]}`} />
      <span className="sr-only">{label}</span>
    </span>
  );
}
