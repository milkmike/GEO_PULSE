"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { GlobalCoverageCountry, GlobalCoverageResponse } from "@/lib/globalMonitoringTypes";

function collectedAt(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow",
  }).format(date) + " мск";
}

function collectionState(country: GlobalCoverageCountry): string {
  if (country.sampled_articles_7d > 0) return "Есть публикации";
  if (country.working_direct_publishers > 0) return "Публикаций пока нет";
  if (country.configured_sources > 0) return "Нет местных публикаций в выборке";
  return "Источники не настроены";
}

export default function GlobalCoverageDisclosure({ refreshToken = 0 }: { refreshToken?: number }) {
  const [snapshot, setSnapshot] = useState<GlobalCoverageResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);
  const [query, setQuery] = useState("");
  const [gapsOnly, setGapsOnly] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    api.globalCoverage(controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setSnapshot(value);
      setLoading(false);
    }).catch(() => {
      if (controller.signal.aborted) return;
      setError(true);
      setLoading(false);
    });
    return () => controller.abort();
  }, [refreshToken, retry]);

  if (!snapshot) {
    if (loading) return null;
    return error ? <p role="alert" className="mt-3 text-xs text-dim">Обзор охвата сейчас недоступен. <button type="button" onClick={() => setRetry((value) => value + 1)} className="min-h-11 text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">Повторить</button></p> : null;
  }
  if (snapshot.status !== "ok" || snapshot.scope_count < 1 || snapshot.countries.length === 0) return null;

  const freshCount = snapshot.countries.filter((country) => country.sampled_articles_7d > 0).length;
  const gapCount = Math.max(snapshot.scope_count - freshCount, 0);
  const search = query.trim().toLocaleLowerCase("ru-RU");
  const rows = snapshot.countries.filter((country) =>
    (!gapsOnly || country.sampled_articles_7d === 0)
    && (!search || country.name_ru.toLocaleLowerCase("ru-RU").includes(search) || country.code.toLowerCase().includes(search)));
  const sampleLimit = snapshot.limits?.per_country;

  return <details className="mt-3 min-w-0 border-y border-line py-1 text-sm">
    <summary className="min-h-11 cursor-pointer rounded-sm py-3 text-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
      <span className="font-medium underline underline-offset-4">Какой мир мы видим</span>
      <span className="ml-2 text-fg/80">{snapshot.stale ? "Прежний срез" : "На дату среза"} · местные публикации за 7 дней: {freshCount} из {snapshot.scope_count} стран и территорий · пробелы по {gapCount}</span>
    </summary>
    <div className="min-w-0 border-t border-line pb-4 pt-4">
      <p className="max-w-3xl text-sm leading-6 text-fg">Это ограниченная выборка местных публикаций, а не полный поток новостей из каждой страны.</p>
      <p className="mt-1 max-w-3xl text-xs leading-5 text-dim">Доступность — успешный опрос за последние 72 часа; публикации могут выходить реже.</p>
      <p className="mt-1 text-xs leading-5 text-dim">{snapshot.stale ? "Показан прежний срез сбора" : "Срез сбора"}{collectedAt(snapshot.as_of) ? ` · ${collectedAt(snapshot.as_of)}` : ""}. Время относится к сбору источников.</p>
      {snapshot.screening?.status !== "ok" && <p className="mt-3 max-w-3xl border-l-2 border-ru-red/60 pl-3 text-xs leading-5 text-fg/80">Автоматический разбор новых материалов приостановлен. Сбор источников продолжается.</p>}
      {error && <p role="alert" className="mt-3 text-xs leading-5 text-dim">Не удалось обновить обзор. Показан предыдущий срез. <button type="button" onClick={() => setRetry((value) => value + 1)} className="min-h-11 text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">Повторить</button></p>}
      <div className="mt-4 flex flex-wrap items-end gap-x-5 gap-y-2">
        <label className="min-w-[180px] flex-1 text-xs text-dim sm:max-w-xs">Найти страну или территорию
          <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} className="mt-1 block min-h-11 w-full rounded-md border border-line bg-panel2 px-3 text-sm text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent" />
        </label>
        <label className="flex min-h-11 cursor-pointer items-center gap-2 text-xs text-fg"><input type="checkbox" checked={gapsOnly} onChange={(event) => setGapsOnly(event.target.checked)} className="size-4 accent-ru-blue" />Показать пробелы</label>
      </div>
      <p className="mt-2 text-xs text-dim">Показано {rows.length} из {snapshot.countries.length}</p>
      <div className="mt-2 max-h-[360px] min-w-0 overflow-auto border-y border-line">
        <table className="w-full min-w-[650px] border-collapse text-left text-xs leading-5">
          <thead className="sticky top-0 bg-panel text-dim"><tr><th scope="col" className="px-3 py-2 font-medium">Страна или территория</th><th scope="col" className="px-3 py-2 font-medium">Источников настроено</th><th scope="col" className="px-3 py-2 font-medium">Местных СМИ доступно</th><th scope="col" className="px-3 py-2 font-medium">Публикаций в выборке{sampleLimit ? ` (до ${sampleLimit})` : ""}</th><th scope="col" className="px-3 py-2 font-medium">Последний сбор</th><th scope="col" className="px-3 py-2 font-medium">Состояние</th></tr></thead>
          <tbody className="divide-y divide-line">{rows.map((country) => <tr key={country.code} className="align-top"><th scope="row" className="min-w-[150px] px-3 py-2 font-medium text-fg">{country.name_ru}</th><td className="tnum px-3 py-2 text-dim">{country.configured_sources}</td><td className="tnum px-3 py-2 text-dim">{country.working_direct_publishers}</td><td className="tnum px-3 py-2 text-fg">{country.sampled_articles_7d}</td><td className="whitespace-nowrap px-3 py-2 text-dim">{collectedAt(country.latest_local_collected_at) ?? "—"}</td><td className="min-w-[150px] px-3 py-2 text-dim">{collectionState(country)}</td></tr>)}</tbody>
        </table>
        {rows.length === 0 && <p className="px-3 py-4 text-xs text-dim">По этому поиску стран нет.</p>}
      </div>
      {snapshot.notice && <p className="mt-3 max-w-3xl text-xs leading-5 text-dim">{snapshot.notice}</p>}
    </div>
  </details>;
}
