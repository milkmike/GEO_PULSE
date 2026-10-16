"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useFeatureFlags } from "@/components/FeatureFlagsProvider";
import PageShell from "@/components/ui/PageShell";
import SectionHeader from "@/components/ui/SectionHeader";
import Card from "@/components/ui/Card";
import { ChangeRow, dayLabel, groupChanges } from "@/components/ChangesFeed";
import EmptyLine from "@/components/ui/EmptyLine";
import { api } from "@/lib/api";
import type { ChangesResponse, ChangeType, CountrySummary } from "@/lib/types";

const FILTERS: [ChangeType, string][] = [["all", "Все"], ["index", "Индекс"], ["tone", "Тон СМИ"],
  ["waves", "Волны в нескольких странах"], ["stories", "Новые сюжеты"], ["attention", "Внимание"]];
function ChangesContent() {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const selectedType = search.get("type");
  const type: ChangeType = FILTERS.some(([key]) => key === selectedType) ? selectedType as ChangeType : "all";
  const country = (search.get("country") ?? "").toUpperCase();
  const important = search.get("important") === "1";
  const { signalDetail } = useFeatureFlags();
  const [countries, setCountries] = useState<CountrySummary[]>([]);
  const [countriesFailed, setCountriesFailed] = useState(false);
  const [data, setData] = useState<ChangesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [moreLoading, setMoreLoading] = useState(false);
  const [error, setError] = useState(false);
  const [reload, setReload] = useState(0);
  const generation = useRef(0);
  const moreController = useRef<AbortController | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.countries().then((result) => { if (!cancelled) setCountries(result.countries); })
      .catch(() => { if (!cancelled) setCountriesFailed(true); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    generation.current += 1;
    moreController.current?.abort();
    moreController.current = null;
    setLoading(true); setMoreLoading(false); setError(false); setData(null);
    api.changes({ type, country, important }, null, controller.signal).then((result) => {
      if (!controller.signal.aborted) setData(result);
    }).catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); moreController.current?.abort(); };
  }, [type, country, important, reload]);

  const filter = (key: string, value: string) => {
    const params = new URLSearchParams(search.toString());
    params.delete("cursor");
    if (value && value !== "all") params.set(key, value); else params.delete(key);
    router.replace(`${pathname}${params.size ? `?${params.toString()}` : ""}`, { scroll: false });
  };
  const loadMore = async () => {
    if (!data?.next_cursor || moreController.current) return;
    const controller = new AbortController();
    moreController.current = controller;
    const current = generation.current;
    setMoreLoading(true); setError(false);
    try {
      const result = await api.changes({ type, country, important }, data.next_cursor, controller.signal);
      if (!controller.signal.aborted && current === generation.current) setData((previous) => {
        const ids = new Set(previous?.items.map((item) => item.id));
        return { ...result, items: [...(previous?.items ?? []), ...result.items.filter((item) => !ids.has(item.id))] };
      });
    } catch { if (!controller.signal.aborted && current === generation.current) setError(true); }
    finally {
      if (!controller.signal.aborted && current === generation.current) { setMoreLoading(false); moreController.current = null; }
    }
  };

  return <PageShell active="/changes">
    <div className="flex flex-wrap items-baseline justify-between gap-3 pb-4 pt-8">
      <h1 className="display text-h2 leading-tight">Изменения</h1>
      {data && <p className="text-sm text-dim">за 7 дней · {data.total} событий ({data.raw_total - data.total} повторов склеено)</p>}
    </div>
    <div className="mb-6 flex flex-wrap items-center gap-2">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Тип изменения">
        {FILTERS.map(([key, label]) => <button key={key} type="button" aria-pressed={type === key} onClick={() => filter("type", key)}
          className={`min-h-11 rounded-full border px-3 text-sm focus-visible:outline-2 focus-visible:outline-accent ${type === key ? "border-accent bg-panel2 text-accent" : "border-line text-dim hover:text-fg"}`}>{label}</button>)}
      </div>
      <select aria-label="Страна" value={country} onChange={(event) => filter("country", event.target.value)} className="min-h-11 rounded-full border border-line bg-panel px-3 text-sm text-fg sm:ml-auto">
        <option value="">Все страны</option>
        {country && !countries.some((item) => item.code === country) && <option value={country}>Выбранная страна</option>}
        {countries.map((item) => <option key={item.code} value={item.code}>{item.name}</option>)}
      </select>
      <button type="button" role="switch" aria-checked={important} onClick={() => filter("important", important ? "" : "1")}
        className={`min-h-11 rounded-full border px-3 text-sm focus-visible:outline-2 focus-visible:outline-accent ${important ? "border-accent text-accent" : "border-line text-dim"}`}>Только важные</button>
    </div>
    {countriesFailed && <p role="status" className="mb-3 text-sm text-dim">Список стран временно недоступен.</p>}
    {loading && <p role="status" className="py-3 text-sm text-dim">Загружаем изменения…</p>}
    {error && <div role="alert" className="py-3 text-sm"><p>Не удалось загрузить изменения.</p><button type="button" onClick={() => data ? loadMore() : setReload((value) => value + 1)} className="min-h-11 text-accent">Повторить</button></div>}
    {!loading && data?.items.length === 0 && <EmptyLine>За 7 дней изменений по выбранным фильтрам нет.</EmptyLine>}
    {data && groupChanges(data.items).map(([day, items]) => <section key={day} className="mb-5" aria-label={dayLabel(day)}>
      <SectionHeader title={dayLabel(day)} />
      <Card className="mt-2">{items.map((item) => <ChangeRow key={item.id} item={item} detailEnabled={signalDetail} />)}</Card>
    </section>)}
    {data?.next_cursor && <button type="button" disabled={moreLoading} onClick={loadMore} className="min-h-11 rounded border border-line px-6 text-sm text-accent disabled:text-dim">{moreLoading ? "Загружаем…" : "Ещё"}</button>}
    <footer className="mt-6 border-t border-line pt-4 text-xs text-dim">Совпадение по времени не означает причину.</footer>
  </PageShell>;
}

export default function ChangesPage() {
  return <Suspense fallback={<p className="p-4 text-sm text-dim">Загружаем изменения…</p>}><ChangesContent /></Suspense>;
}
