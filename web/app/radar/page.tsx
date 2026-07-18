"use client";

import { Suspense, useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { CircleAlert, LoaderCircle, Radar } from "lucide-react";
import SiteHeader from "@/components/SiteHeader";
import TrendCard from "@/components/TrendCard";
import { useFeatureFlags } from "@/components/FeatureFlagsProvider";
import { api } from "@/lib/api";
import type { RadarContour, RadarFilters, RadarTrend, RadarTrendState } from "@/lib/types";

const STATES: Array<["" | RadarTrendState, string]> = [["", "все состояния"], ["confirmed", "подтверждён"], ["emerging", "зарождается"], ["cooling", "затухает"], ["candidate", "кандидат"], ["resolved", "завершён"]];
const CONTOURS: Array<["" | RadarContour, string]> = [["", "оба контура"], ["media", "медиаконтур"], ["action", "контур действий"]];
const FILTER_IDS = ["radar-state", "radar-contour", "radar-country"];

function isAbort(reason: unknown) { return reason instanceof DOMException && reason.name === "AbortError"; }

function RadarPageContent() {
  const { earlyWarningRadar } = useFeatureFlags();
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const query = searchParams.toString();
  const [items, setItems] = useState<RadarTrend[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [loadingMore, setLoadingMore] = useState(false);
  const [reload, setReload] = useState(0);
  const pageController = useRef<AbortController | null>(null);

  const filters = useMemo<RadarFilters>(() => {
    const params = new URLSearchParams(query);
    const stateValue = params.get("state");
    const contour = params.get("contour");
    const country = params.get("country");
    return {
      ...(STATES.some(([value]) => value === stateValue) && stateValue ? { state: stateValue as RadarTrendState } : {}),
      ...(CONTOURS.some(([value]) => value === contour) && contour ? { contour: contour as RadarContour } : {}),
      ...(country && /^[a-z]{2}$/iu.test(country) ? { country: country.toUpperCase() } : {}),
      limit: 25,
    };
  }, [query]);
  const [countryDraft, setCountryDraft] = useState(filters.country ?? "");

  useEffect(() => setCountryDraft(filters.country ?? ""), [filters.country]);

  useEffect(() => {
    if (!earlyWarningRadar) return;
    const controller = new AbortController();
    pageController.current?.abort();
    pageController.current = controller;
    setState("loading"); setItems([]); setNextCursor(null);
    api.radar(filters, null, controller.signal).then((payload) => {
      if (!controller.signal.aborted) { setItems(payload.items); setNextCursor(payload.next_cursor); setState("ready"); }
    }).catch((reason: unknown) => { if (!controller.signal.aborted && !isAbort(reason)) setState("error"); });
    return () => controller.abort();
  }, [earlyWarningRadar, filters, reload]);

  function updateFilter(key: "state" | "contour" | "country", raw: string) {
    const next = new URLSearchParams(query);
    const value = key === "country" ? raw.trim().toUpperCase() : raw;
    if (value) next.set(key, value); else next.delete(key);
    const nextQuery = next.toString();
    router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname);
  }

  function moveFilterFocus(event: KeyboardEvent<HTMLElement>, index: number) {
    if (!["ArrowRight", "ArrowLeft"].includes(event.key)) return;
    event.preventDefault();
    const next = (index + (event.key === "ArrowRight" ? 1 : -1) + FILTER_IDS.length) % FILTER_IDS.length;
    document.getElementById(FILTER_IDS[next])?.focus();
  }

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    const controller = new AbortController();
    setLoadingMore(true);
    try {
      const payload = await api.radar(filters, nextCursor, controller.signal);
      if (!controller.signal.aborted) {
        setItems((current) => [...current, ...payload.items.filter((item) => !current.some((existing) => existing.public_id === item.public_id))]);
        setNextCursor(payload.next_cursor);
      }
    } catch (reason) { if (!isAbort(reason)) setState("error"); }
    finally { setLoadingMore(false); }
  }

  if (!earlyWarningRadar) return <main className="mx-auto max-w-[1240px] px-3 pb-16"><SiteHeader /><div className="mx-auto mt-16 max-w-xl border-y border-line py-10 text-center"><Radar size={24} className="mx-auto mb-3 text-dim" aria-hidden="true" /><h1 className="display text-2xl">Радар раннего предупреждения</h1><p className="mt-3 text-sm text-dim">Раздел раннего предупреждения отключён.</p></div></main>;

  return (
    <main className="mx-auto max-w-[1240px] px-3 pb-16">
      <SiteHeader active="/radar" />
      <header className="reveal reveal-1 border-b border-line pb-7 pt-10"><p className="section-num">EARLY WARNING / 01</p><h1 className="display mt-2 text-[40px] leading-none sm:text-[58px]">Радар перемен</h1><p className="mt-5 max-w-3xl text-[15px] leading-7 text-dim">Подтверждённые межстрановые волны и ранние признаки ускорения. Медиа и действия показаны раздельно; пробелы покрытия не скрываются.</p></header>

      <section aria-label="Фильтры радара" className="my-5 grid gap-2 border-y border-line py-3 sm:grid-cols-[1fr_1fr_1fr_auto]">
        <label className="text-[10px] uppercase tracking-wide text-dim">Состояние<select id="radar-state" value={filters.state ?? ""} onKeyDown={(event) => moveFilterFocus(event, 0)} onChange={(event) => updateFilter("state", event.target.value)} className="mt-1 min-h-11 w-full rounded-md border border-line bg-panel px-3 text-sm text-fg focus-visible:outline-2 focus-visible:outline-accent">{STATES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="text-[10px] uppercase tracking-wide text-dim">Контур<select id="radar-contour" value={filters.contour ?? ""} onKeyDown={(event) => moveFilterFocus(event, 1)} onChange={(event) => updateFilter("contour", event.target.value)} className="mt-1 min-h-11 w-full rounded-md border border-line bg-panel px-3 text-sm text-fg focus-visible:outline-2 focus-visible:outline-accent">{CONTOURS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="text-[10px] uppercase tracking-wide text-dim">Страна<input id="radar-country" value={countryDraft} maxLength={2} placeholder="ISO · ES" onKeyDown={(event) => { moveFilterFocus(event, 2); if (event.key === "Enter") updateFilter("country", countryDraft); }} onBlur={() => updateFilter("country", countryDraft)} onChange={(event) => setCountryDraft(event.target.value.toUpperCase())} className="mt-1 min-h-11 w-full rounded-md border border-line bg-panel px-3 text-sm uppercase text-fg placeholder:text-dim focus-visible:outline-2 focus-visible:outline-accent" /></label>
        <button type="button" onClick={() => router.replace(pathname)} className="min-h-11 self-end px-3 text-xs text-dim underline underline-offset-4 hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">сбросить</button>
      </section>

      {state === "loading" && <p role="status" className="flex items-center justify-center gap-2 py-16 text-sm text-dim"><LoaderCircle size={18} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />сверяем сохранённые тренды…</p>}
      {state === "error" && <div role="alert" className="border-y border-ru-red/40 py-12 text-center"><CircleAlert size={20} className="mx-auto mb-3 text-ru-red" aria-hidden="true" /><p className="text-sm text-dim">Не удалось загрузить радар.</p><button type="button" onClick={() => setReload((value) => value + 1)} className="mt-3 min-h-11 text-accent underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">повторить</button></div>}
      {state === "ready" && items.length === 0 && <p className="border-y border-line py-14 text-center text-sm text-dim">По выбранным фильтрам трендов нет. Это не означает отсутствия изменений — только отсутствие сохранённых трендов, прошедших эти условия.</p>}
      {state === "ready" && items.length > 0 && <section aria-label="Тренды радара" className="divide-y divide-line border-y border-line">{items.map((trend) => <TrendCard key={trend.public_id} trend={trend} />)}</section>}
      {nextCursor && <button type="button" disabled={loadingMore} onClick={loadMore} className="mt-6 min-h-11 rounded-md border border-line px-5 text-xs uppercase tracking-wide hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50">{loadingMore ? "загружаем…" : "следующие тренды"}</button>}
    </main>
  );
}

export default function RadarPage() {
  return (
    <Suspense fallback={<main className="mx-auto max-w-[1240px] px-3 pb-16"><SiteHeader active="/radar" /><div role="status" className="py-16 text-center text-dim">Готовим радар…</div></main>}>
      <RadarPageContent />
    </Suspense>
  );
}
