"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Health } from "@/lib/types";

const COLOR: Record<Health["verdict"], string> = {
  HEALTHY: "text-ally border-ally",
  WARNING: "text-cooling border-cooling",
  DEGRADED: "text-hostile border-hostile",
  UNHEALTHY: "text-hostile border-hostile",
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
  return (
    <span
      className={`rounded-full border px-2.5 py-0.5 text-[11px] ${COLOR[health.verdict]}`}
      title={`Источники: ${health.sources_ok}/${health.sources_total} OK · GDELT: ${health.gdelt.status}`}
    >
      {health.verdict} · {health.coverage_pct}%
    </span>
  );
}
