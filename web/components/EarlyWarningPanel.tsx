"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { LoaderCircle, Radar } from "lucide-react";
import { useFeatureFlags } from "./FeatureFlagsProvider";
import TrendCard from "./TrendCard";
import { api } from "@/lib/api";
import type { RadarFilters, RadarTrend } from "@/lib/types";

const EMPTY_FILTERS: Omit<RadarFilters, "limit"> = {};

export function isPriorityRadarTrend(trend: RadarTrend): boolean {
  return trend.state === "confirmed" || (
    trend.state === "emerging"
    && (trend.velocity ?? 0) >= 2
    && (trend.confidence ?? 0) >= 0.8
    && (trend.coverage_confidence ?? 0) > 0.5
  );
}

export default function EarlyWarningPanel({
  trends,
  countryCode,
  filters = EMPTY_FILTERS,
  title = "Раннее предупреждение",
  limit = 3,
}: {
  trends?: RadarTrend[];
  countryCode?: string;
  filters?: Omit<RadarFilters, "limit">;
  title?: string;
  limit?: number;
}) {
  const { earlyWarningRadar } = useFeatureFlags();
  const [loaded, setLoaded] = useState<RadarTrend[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "error">(trends ? "ready" : "loading");
  const [reload, setReload] = useState(0);
  const shouldFetch = trends === undefined && earlyWarningRadar;

  useEffect(() => {
    if (!shouldFetch) return;
    const controller = new AbortController();
    setState("loading");
    const hasExactRelation = Boolean(filters.storyId || filters.signalId);
    const request = countryCode && !hasExactRelation
      ? api.countryRadar(countryCode, { limit: Math.max(limit * 3, 12) }, null, controller.signal)
      : api.radar({ ...filters, limit: Math.max(limit * 3, 12) }, null, controller.signal);
    request.then((payload) => {
      if (!controller.signal.aborted) { setLoaded(payload.items); setState("ready"); }
    }).catch((reason: unknown) => {
      if (!controller.signal.aborted && !(reason instanceof DOMException && reason.name === "AbortError")) setState("error");
    });
    return () => controller.abort();
  }, [countryCode, filters, limit, reload, shouldFetch]);

  const items = useMemo(() => (trends ?? loaded).filter(isPriorityRadarTrend).slice(0, limit), [loaded, limit, trends]);
  if (trends === undefined && !earlyWarningRadar) return null;
  const relationParams = new URLSearchParams();
  if (filters.storyId) relationParams.set("story_id", String(filters.storyId));
  if (filters.signalId) relationParams.set("signal_id", String(filters.signalId));
  if (!relationParams.size && countryCode) relationParams.set("country", countryCode.toUpperCase());
  const radarHref = relationParams.size ? `/radar?${relationParams.toString()}` : "/radar";

  return (
    <section className="card overflow-hidden" aria-labelledby={`early-warning-${countryCode ?? "global"}`}>
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="flex items-center gap-2">
          <Radar aria-hidden="true" size={15} className="text-ru-red" />
          <h2 id={`early-warning-${countryCode ?? "global"}`} className="card-title text-fg">{title}</h2>
        </div>
        <Link href={radarHref} className="min-h-11 py-3 text-[11px] text-dim hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:min-h-0 sm:py-0">весь радар →</Link>
      </div>
      {state === "loading" && <p role="status" className="flex items-center gap-2 px-4 py-7 text-xs text-dim"><LoaderCircle size={14} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />сверяем подтверждённые волны…</p>}
      {state === "error" && <div role="alert" className="px-4 py-6 text-xs text-dim">Радар сейчас недоступен. <button type="button" onClick={() => setReload((value) => value + 1)} className="ml-1 min-h-11 text-accent underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:min-h-0">повторить</button></div>}
      {state === "ready" && items.length === 0 && <p className="px-4 py-7 text-xs text-dim">Подтверждённых или исключительных ранних трендов сейчас нет.</p>}
      {state === "ready" && items.length > 0 && <div className="divide-y divide-line">{items.map((trend) => <TrendCard key={trend.public_id} trend={trend} compact />)}</div>}
    </section>
  );
}
