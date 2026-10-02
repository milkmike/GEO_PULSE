"use client";

import { useEffect, useId, useState } from "react";
import { ArrowUpRight, LoaderCircle } from "lucide-react";
import { api } from "@/lib/api";
import type { EarlySignal, EarlySignalEvidence, EarlySignalsResponse } from "@/lib/earlySignalTypes";

type Snapshot = { scope: string; value: EarlySignalsResponse };

function displayDate(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "long", year: "numeric", timeZone: "Europe/Moscow" }).format(date);
}

function safeUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch { return null; }
}

function Evidence({ item }: { item: EarlySignalEvidence }) {
  const href = safeUrl(item.url);
  const published = displayDate(item.published_at);
  const collected = displayDate(item.collected_at);
  return <div className="mt-2 min-w-0 break-words border-l border-line pl-3 text-xs leading-5 text-dim">
    <p>{item.source_name} · {item.title}</p>
    {(published || collected) && <p>{published ? `Опубликовано ${published}` : ""}{collected ? ` · собрано ${collected}` : ""}</p>}
    {href && <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center gap-1 text-xs text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
      Открыть публикацию <ArrowUpRight size={13} aria-hidden="true" />
    </a>}
  </div>;
}

function SignalArticle({ item }: { item: EarlySignal }) {
  const [expanded, setExpanded] = useState(false);
  const horizon = displayDate(item.horizon_date);
  const observations = item.observations ?? [];
  const watch = item.watch ?? [];
  const evidence = item.evidence ?? [];
  const citedCount = new Set(observations.filter((observation) => evidence.some((source) => source.id === observation.article_id)).map((observation) => observation.article_id)).size;
  const sourceNoun = citedCount % 10 === 1 && citedCount % 100 !== 11 ? "публикация" : citedCount % 10 >= 2 && citedCount % 10 <= 4 && (citedCount % 100 < 12 || citedCount % 100 > 14) ? "публикации" : "публикаций";
  return <article className="min-w-0 border-t border-line py-5 first:border-t-0 sm:py-6">
    <div className="grid min-w-0 gap-x-8 gap-y-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
      <div className="min-w-0">
        <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-ru-red">Рабочая гипотеза</p>
        <h4 className="display mt-2 max-w-xl break-words text-[22px] leading-[1.22] sm:text-[25px]">{item.headline_ru}</h4>
        {horizon && <p className="mt-2 text-xs leading-5 text-dim">Горизонт наблюдения: {horizon}</p>}
      </div>
      <div className="min-w-0">
        {item.interpretation_ru && <p className="max-w-3xl break-words text-sm leading-6 text-fg sm:text-[15px] sm:leading-7">{item.interpretation_ru}</p>}
        <button type="button" aria-expanded={expanded} onClick={() => setExpanded((current) => !current)} className="mt-3 inline-flex min-h-11 items-center rounded-sm text-sm font-medium text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
          {expanded ? "Свернуть гипотезу" : "Раскрыть гипотезу"}
        </button>
      </div>
    </div>
    {expanded && <div className="mx-auto mt-5 w-full max-w-[900px] min-w-0 space-y-5 border-l-2 border-ru-blue/60 pl-4 sm:mt-6 sm:pl-6">
            {item.hypothesis_ru && <section><h5 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-dim">Что может происходить</h5><p className="mt-1 break-words text-sm leading-6 text-fg">{item.hypothesis_ru}</p></section>}
            {item.opportunity_ru && <section><h5 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-dim">Возможность для России</h5><p className="mt-1 break-words text-sm leading-6 text-fg">{item.opportunity_ru}</p></section>}
            {item.russia_link === "unestablished" && <p className="text-xs leading-5 text-dim">Связь с Россией пока не установлена.</p>}
            {item.counterargument_ru && <section><h5 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-dim">Другая версия</h5><p className="mt-1 break-words text-sm leading-6 text-fg">{item.counterargument_ru}</p></section>}
            {watch.length > 0 && <section><h5 className="text-[10px] font-semibold uppercase tracking-[0.14em] text-dim">Что наблюдать дальше</h5><ul className="mt-2 space-y-2">{watch.map((point, index) => <li key={`${point.observation_ru}-${index}`} className="break-words text-sm leading-6 text-fg"><span className="font-medium text-ru-white">{point.effect === "strengthens" ? "В пользу гипотезы" : "Ослабит гипотезу"}:</span> {point.observation_ru}{displayDate(point.by_date) ? ` · до ${displayDate(point.by_date)}` : ""}</li>)}</ul></section>}
            {observations.length > 0 && <details className="min-w-0 border-t border-line pt-2"><summary className="flex min-h-11 cursor-pointer items-center rounded-sm text-sm font-medium text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">На чём основана гипотеза · {citedCount} {sourceNoun}</summary><ul className="mt-2 space-y-4">{observations.map((observation) => {
              const source = evidence.find((entry) => entry.id === observation.article_id);
              return <li key={observation.id} className="min-w-0 break-words text-sm leading-6 text-fg"><p>{observation.text_ru}</p>{observation.quote && <blockquote className="mt-1 border-l border-line pl-3 text-xs leading-5 text-dim">«{observation.quote}»</blockquote>}{source && <Evidence item={source} />}</li>;
            })}</ul></details>}
            {item.review_note && <p className="border-t border-line pt-3 text-xs leading-5 text-dim">{item.review_note}</p>}
    </div>}
  </article>;
}

export default function EarlySignalPanel({ country, availableCountry, countryName, signalOnly = false, onWorld, onCountry, refreshToken = 0 }: { country: string | null; availableCountry?: string | null; countryName?: string | null; signalOnly?: boolean; onWorld?: () => void; onCountry?: () => void; refreshToken?: number }) {
  const titleId = useId();
  const [scopeMode, setScopeMode] = useState<"world" | "country">(country ? "country" : "world");
  const countryCode = (country || availableCountry)?.trim().toUpperCase() || null;
  const scope = scopeMode === "country" && countryCode ? countryCode : "world";
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => { setScopeMode(country ? "country" : "world"); }, [country]);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(false);
    api.earlySignals(scope === "world" ? null : scope, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setSnapshot({ scope, value });
      setLoading(false);
    }).catch(() => {
      if (controller.signal.aborted) return;
      setError(true);
      setLoading(false);
    });
    return () => controller.abort();
  }, [scope, refreshToken, retry]);

  const value = snapshot?.scope === scope ? snapshot.value : null;
  const items = value?.items ?? [];
  const reviewTimes = items.map((item) => Date.parse(item.as_of)).filter(Number.isFinite);
  const latestReview = reviewTimes.length > 0 ? displayDate(new Date(Math.max(...reviewTimes)).toISOString()) : null;
  if (value && items.length === 0) {
    if (error) return <p role="alert" className="mt-5 text-xs leading-5 text-dim">Не удалось обновить ранние сигналы. <button type="button" onClick={() => setRetry((current) => current + 1)} className="min-h-11 px-1 text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">Повторить</button></p>;
    if (scope === "world") return null;
    return <div className="mt-5 border-t border-line pt-2">{signalOnly && <p className="text-xs leading-5 text-dim"><span className="font-medium text-ru-white">{countryName || countryCode}</span> · опубликованных гипотез пока нет.</p>}<button type="button" onClick={() => { setScopeMode("world"); onWorld?.(); }} className="min-h-11 text-sm text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">Посмотреть ранние сигналы в мире</button></div>;
  }
  if (!value && !error) return <p role="status" className="mt-5 flex min-h-11 items-center gap-2 text-xs text-dim"><LoaderCircle aria-hidden="true" size={14} className="animate-spin motion-reduce:animate-none" />Ищем ранние сигналы…</p>;
  if (!value && error) return <p role="alert" className="mt-5 text-xs leading-5 text-dim">Ранние сигналы сейчас недоступны. <button type="button" onClick={() => setRetry((current) => current + 1)} className="min-h-11 px-1 text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">Повторить</button></p>;
  return <section aria-labelledby={titleId} className="mt-7 min-w-0 border-t border-line pt-5 sm:mt-8" aria-busy={loading}>
    <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
      <div><p className="text-[10px] font-medium uppercase tracking-[0.18em] text-ru-red">Ранние сигналы</p><h3 id={titleId} className="display mt-1 break-words text-[27px] leading-tight sm:text-[32px]">{scope === "world" ? "На горизонте: мир" : `На горизонте: ${countryName || scope}`}</h3></div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        {countryCode && <nav aria-label="Масштаб ранних сигналов" className="flex items-center gap-1 text-xs">
          <button type="button" aria-pressed={scope === "world"} onClick={() => { setScopeMode("world"); onWorld?.(); }} className={`min-h-11 rounded-sm px-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${scope === "world" ? "text-ru-white underline underline-offset-4" : "text-accent"}`}>Мир</button>
          <button type="button" aria-pressed={scope !== "world"} onClick={() => { if (scope === "world") { setScopeMode("country"); onCountry?.(); } }} className={`min-h-11 rounded-sm px-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${scope !== "world" ? "text-ru-white underline underline-offset-4" : "text-accent"}`}>{countryName || countryCode}</button>
        </nav>}
        {latestReview && <p className="text-xs text-dim">Разбор от {latestReview}</p>}
      </div>
    </header>
    <p className="mt-2 max-w-3xl text-sm leading-6 text-fg/80">Небольшие изменения, за которыми стоит следить.</p>
    {signalOnly && scope !== "world" && <p className="mt-1 text-xs leading-5 text-dim">Фильтр ранних сигналов: {countryName || countryCode}. Страновой обзор остаётся на прежней стране.</p>}
    {value?.notice && <p className="mt-3 max-w-3xl border-l-2 border-ru-blue/60 pl-3 text-xs leading-5 text-dim">{value.notice}</p>}
    {error && <p role="alert" className="mt-4 text-xs leading-5 text-dim">{value ? "Не удалось обновить сигналы. Показан предыдущий обзор." : "Ранние сигналы сейчас недоступны."} <button type="button" onClick={() => setRetry((current) => current + 1)} className="min-h-11 px-1 text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">Повторить</button></p>}
    {items.length > 0 && <div className="mt-4 min-w-0 border-b border-line">{items.map((item) => <SignalArticle key={item.id} item={item} />)}</div>}
  </section>;
}
