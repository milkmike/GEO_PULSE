"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { LoaderCircle, Radar } from "lucide-react";
import { useFeatureFlags } from "./FeatureFlagsProvider";
import TrendCard from "./TrendCard";
import { api } from "@/lib/api";
import type { RadarFilters, RadarTrend, RadarTrendPage } from "@/lib/types";

const EMPTY_FILTERS: Omit<RadarFilters, "limit"> = {};
const MAX_COMPACT_PAGES = 3;

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
  const [searchScope, setSearchScope] = useState<"unknown" | "exhausted" | "partial">("unknown");
  const [reload, setReload] = useState(0);
  const shouldFetch = trends === undefined && earlyWarningRadar;
  const filterState = filters.state;
  const filterContour = filters.contour;
  const filterCountry = filters.country;
  const storyId = filters.storyId;
  const signalId = filters.signalId;

  useEffect(() => {
    if (!shouldFetch) return;
    const controller = new AbortController();
    const pageLimit = Math.max(limit * 3, 12);
    const requestFilters: RadarFilters = {
      ...(filterState ? { state: filterState } : {}),
      ...(filterContour ? { contour: filterContour } : {}),
      ...(filterCountry ? { country: filterCountry } : {}),
      ...(storyId ? { storyId } : {}),
      ...(signalId ? { signalId } : {}),
      limit: pageLimit,
    };
    const hasExactRelation = Boolean(storyId || signalId);
    setState("loading"); setLoaded([]); setSearchScope("unknown");

    async function loadPriorityPages() {
      let cursor: string | null = null;
      let collected: RadarTrend[] = [];
      let nextCursor: string | null = null;
      for (let page = 0; page < MAX_COMPACT_PAGES; page += 1) {
        const payload: RadarTrendPage = countryCode && !hasExactRelation
          ? await api.countryRadar(countryCode, requestFilters, cursor, controller.signal)
          : await api.radar(requestFilters, cursor, controller.signal);
        if (controller.signal.aborted) return;
        const known = new Set(collected.map((item) => item.public_id));
        collected = [...collected, ...payload.items.filter((item) => !known.has(item.public_id))];
        nextCursor = payload.next_cursor;
        if (collected.filter(isPriorityRadarTrend).length >= limit || nextCursor === null) break;
        cursor = nextCursor;
      }
      if (controller.signal.aborted) return;
      setLoaded(collected);
      setSearchScope(nextCursor === null ? "exhausted" : "partial");
      setState("ready");
    }

    loadPriorityPages().catch((reason: unknown) => {
      if (!controller.signal.aborted && !(reason instanceof DOMException && reason.name === "AbortError")) setState("error");
    });
    return () => controller.abort();
  }, [countryCode, filterContour, filterCountry, filterState, limit, reload, shouldFetch, signalId, storyId]);

  const items = useMemo(() => (trends ?? loaded).filter(isPriorityRadarTrend).slice(0, limit), [loaded, limit, trends]);
  if (trends === undefined && !earlyWarningRadar) return null;
  const relationParams = new URLSearchParams();
  if (storyId) relationParams.set("story_id", String(storyId));
  if (signalId) relationParams.set("signal_id", String(signalId));
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
      {state === "ready" && items.length === 0 && searchScope === "partial" && <p className="px-4 py-7 text-xs text-dim">В проверенной части радара приоритетных трендов пока нет. Полный список доступен в радаре.</p>}
      {state === "ready" && items.length === 0 && searchScope === "exhausted" && <p className="px-4 py-7 text-xs text-dim">Среди всех сохранённых трендов приоритетных сейчас нет.</p>}
      {state === "ready" && items.length === 0 && searchScope === "unknown" && <p className="px-4 py-7 text-xs text-dim">В переданной выборке приоритетных трендов нет.</p>}
      {state === "ready" && items.length > 0 && <div className="divide-y divide-line">{items.map((trend) => <TrendCard key={trend.public_id} trend={trend} compact />)}</div>}
      {state === "ready" && items.length > 0 && searchScope === "partial" && <p className="border-t border-line px-4 py-3 text-[11px] text-dim">Показаны приоритетные тренды из проверенной части радара.</p>}
    </section>
  );
}
