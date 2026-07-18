"use client";

import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { CircleAlert, LoaderCircle } from "lucide-react";
import SiteHeader from "@/components/SiteHeader";
import TrendInvestigation, { type RadarView } from "@/components/TrendInvestigation";
import { useFeatureFlags } from "@/components/FeatureFlagsProvider";
import { api } from "@/lib/api";
import type { RadarCoverage, RadarEvidencePage, RadarMethodology, RadarTimeline, RadarTrend } from "@/lib/types";

const VIEWS = new Set<RadarView>(["propagation", "evidence", "coverage", "method"]);
const isAbort = (reason: unknown) => reason instanceof DOMException && reason.name === "AbortError";

export default function RadarTrendPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { earlyWarningRadar } = useFeatureFlags();
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const query = searchParams.toString();
  const view = useMemo<RadarView>(() => {
    const candidate = new URLSearchParams(query).get("view") as RadarView | null;
    return candidate && VIEWS.has(candidate) ? candidate : "propagation";
  }, [query]);
  const [payload, setPayload] = useState<null | { trend: RadarTrend; timeline: RadarTimeline; evidence: RadarEvidencePage; coverage: RadarCoverage; methodology: RadarMethodology }>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    if (!earlyWarningRadar) return;
    const controller = new AbortController();
    setState("loading"); setPayload(null);
    Promise.all([
      api.radarTrend(id, controller.signal), api.radarTimeline(id, controller.signal),
      api.radarEvidence(id, null, 25, controller.signal), api.radarCoverage(controller.signal), api.radarMethodology(controller.signal),
    ]).then(([trend, timeline, evidence, coverage, methodology]) => {
      if (!controller.signal.aborted) { setPayload({ trend, timeline, evidence, coverage, methodology }); setState("ready"); }
    }).catch((reason: unknown) => { if (!controller.signal.aborted && !isAbort(reason)) setState("error"); });
    return () => controller.abort();
  }, [earlyWarningRadar, id, reload]);

  function setView(nextView: RadarView) {
    const next = new URLSearchParams(query);
    if (nextView === "propagation") next.delete("view"); else next.set("view", nextView);
    const value = next.toString();
    router.replace(value ? `${pathname}?${value}` : pathname);
  }

  if (!earlyWarningRadar) return <main className="mx-auto max-w-[1240px] px-3 pb-16"><SiteHeader /><p className="mt-16 border-y border-line py-10 text-center text-sm text-dim">Раздел раннего предупреждения отключён.</p></main>;
  return (
    <main className="mx-auto max-w-[1240px] px-3 pb-16">
      <SiteHeader active="/radar" />
      <div className="pt-7"><Link href="/radar" className="inline-flex min-h-11 items-center text-xs uppercase tracking-wide text-dim hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent">← все тренды</Link></div>
      {state === "loading" && <p role="status" className="flex items-center justify-center gap-2 py-20 text-sm text-dim"><LoaderCircle size={20} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />собираем расследование…</p>}
      {state === "error" && <div role="alert" className="mt-12 border-y border-ru-red/40 py-12 text-center"><CircleAlert size={20} className="mx-auto mb-3 text-ru-red" aria-hidden="true" /><p className="text-sm text-dim">Не удалось загрузить расследование тренда.</p><button type="button" onClick={() => setReload((value) => value + 1)} className="mt-3 min-h-11 text-accent underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">повторить</button></div>}
      {state === "ready" && payload && <div className="reveal reveal-1 mt-4"><header className="mb-6 border-b border-line pb-7"><p className="section-num">TREND INVESTIGATION / {payload.trend.state.toUpperCase()}</p><h1 className="display mt-3 max-w-5xl text-[38px] leading-[1.05] sm:text-[54px]">{payload.trend.thesis}</h1></header><TrendInvestigation {...payload} view={view} onViewChange={setView} /></div>}
    </main>
  );
}
