"use client";

import { useEffect, useId, useState, type ReactNode } from "react";
import Link from "next/link";
import { ArrowUpRight, Globe2, LoaderCircle } from "lucide-react";
import { api } from "@/lib/api";
import type { DecisionEvidence, DecisionWorkspaceResponse } from "@/lib/decisionTypes";

import NewsReadingList, { newsDateTime as dateTime } from "./NewsReadingList";

type LoadState = "loading" | "ready" | "refreshing" | "refreshError" | "error";

function safeSourceUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
  } catch { return null; }
}

function SourceEvidence({ evidence, quote }: { evidence: DecisionEvidence; quote?: string }) {
  const href = safeSourceUrl(evidence.url);
  return (
    <details className="group mt-2 border-t border-line pt-2 text-xs">
      <summary className="w-fit cursor-pointer rounded-sm py-1 text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
        Цитата и источник
      </summary>
      <div className="mt-2 min-w-0 space-y-2 break-words border-l-2 border-ru-blue/60 pl-3 leading-5 text-dim">
        <p className="text-fg">{evidence.title_original}</p>
        {quote && <blockquote className="whitespace-pre-wrap text-fg">«{quote}»</blockquote>}
        {evidence.russia_evidence_quote && quote !== evidence.russia_evidence_quote && (
          <p>Связь с Россией: «{evidence.russia_evidence_quote}»</p>
        )}
        {evidence.country_evidence_quote && quote !== evidence.country_evidence_quote && (
          <p>Связь со страной: «{evidence.country_evidence_quote}»</p>
        )}
        <p>{evidence.publisher_name}{evidence.publisher_country_code ? ` · издатель: ${evidence.publisher_country_code}` : ""}</p>
        <p>Опубликовано: {dateTime(evidence.published_at)} · собрано: {dateTime(evidence.collected_at)}</p>
        {href ? (
          <a className="inline-flex min-h-11 items-center gap-1 text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:min-h-0" href={href} target="_blank" rel="noopener noreferrer">
            Открыть публикацию <ArrowUpRight aria-hidden="true" size={13} />
          </a>
        ) : <p>Ссылка на оригинал недоступна.</p>}
      </div>
    </details>
  );
}

function Empty({ children }: { children: string }) {
  return <p className="border-t border-line py-5 text-sm leading-6 text-dim">{children}</p>;
}

function EvidenceRow({ item }: { item: DecisionEvidence }) {
  return (
    <article className="border-t border-line py-3 first:border-t-0">
      <h4 className="break-words text-sm font-medium leading-5 text-ru-white">{item.title_ru || item.title_original}</h4>
      <p className="mt-1 text-xs leading-5 text-fg">{item.summary_ru}</p>
      {item.russia_explanation_ru && <p className="mt-1 text-xs leading-5 text-dim">Связь с Россией: {item.russia_explanation_ru}</p>}
      <p className="mt-1 text-[11px] text-dim">{item.publisher_name} · опубликовано {dateTime(item.published_at)}</p>
      <SourceEvidence evidence={item} />
    </article>
  );
}

function AttentionItem({ item, currentCountry, onSelect }: {
  item: DecisionWorkspaceResponse["attention"][number];
  currentCountry: string;
  onSelect: (code: string) => void;
}) {
  return <button type="button" onClick={() => onSelect(item.code)} aria-current={currentCountry === item.code ? "true" : undefined}
    className={`flex min-h-11 w-full items-start justify-between gap-3 px-2 py-2.5 text-left focus-visible:outline-2 focus-visible:outline-inset focus-visible:outline-accent ${currentCountry === item.code ? "bg-panel2 text-ru-white" : "text-fg hover:bg-panel2/60"}`}>
    <span className="min-w-0"><span className="block text-sm font-medium">{item.name}</span><span className="mt-1 block text-xs leading-5 text-fg/75">{item.reason.replace(": появились сообщения для проверки", " · новые публикации")}</span></span>
    <span className="tnum shrink-0 text-right text-xs"><strong className="text-ru-white">{item.count_24h}</strong><span className="block text-xs leading-5 text-fg/75">за сутки · {item.count_7d} за неделю</span></span>
  </button>;
}

function Heading({ title, description }: { title: string; description?: string }) {
  return <div>
    <h3 className="display mt-1 text-[25px] leading-tight">{title}</h3>
    {description && <p className="mt-2 max-w-2xl text-sm leading-6 text-fg/75">{description}</p>}
  </div>;
}

interface DecisionWorkspaceProps {
  renderMap?: (props: { selectedCountry: string | null; onSelectCountry: (code: string) => void }) => ReactNode;
  mapControls?: ReactNode;
  activeMapFilters?: number;
}

export default function DecisionWorkspace({ renderMap, mapControls, activeMapFilters = 0 }: DecisionWorkspaceProps) {
  const titleId = useId();
  const [initialized, setInitialized] = useState(false);
  const [selectedCountry, setSelectedCountry] = useState<string | null>(null);
  const [payload, setPayload] = useState<DecisionWorkspaceResponse | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [reload, setReload] = useState(0);
  const [windowSize, setWindowSize] = useState<"day" | "week">("day");

  useEffect(() => {
    const readCountry = () => {
      const country = new URLSearchParams(window.location.search).get("country")?.trim().toUpperCase();
      setSelectedCountry(country || null);
    };
    readCountry();
    setInitialized(true);
    window.addEventListener("popstate", readCountry);
    return () => window.removeEventListener("popstate", readCountry);
  }, []);

  useEffect(() => {
    if (!initialized) return;
    const controller = new AbortController();
    const requestCountry = selectedCountry ?? payload?.country.code ?? null;
    const hasMatchingPayload = Boolean(payload && (!requestCountry || payload.country.code === requestCountry.toUpperCase()));
    setState(hasMatchingPayload ? "refreshing" : "loading");
    api.decisionWorkspace(requestCountry, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      if (requestCountry && result.country.code !== requestCountry.toUpperCase()) {
        setState(hasMatchingPayload ? "refreshError" : "error");
        return;
      }
      setPayload(result);
      setState("ready");
      if (!selectedCountry) {
        const url = new URL(window.location.href);
        url.searchParams.set("country", result.country.code);
        window.history.replaceState(window.history.state, "", url);
      }
    }).catch(() => {
      if (!controller.signal.aborted) setState(hasMatchingPayload ? "refreshError" : "error");
    });
    return () => controller.abort();
    // The current payload is intentionally read only when a country/reload request starts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialized, selectedCountry, reload]);

  useEffect(() => {
    const timer = window.setInterval(() => setReload((value) => value + 1), 120_000);
    return () => window.clearInterval(timer);
  }, []);

  function selectCountry(code: string) {
    if (!code || code === (selectedCountry ?? payload?.country.code)) return;
    const url = new URL(window.location.href);
    url.searchParams.set("country", code);
    window.history.pushState(window.history.state, "", url);
    setSelectedCountry(code);
    setWindowSize("day");
  }

  const currentCountry = selectedCountry ?? payload?.country.code ?? "";
  const countryOptions = payload?.countries ?? [];
  const content = state !== "loading" && state !== "error"
    && (!selectedCountry || payload?.country.code === selectedCountry.toUpperCase()) ? payload : null;
  const brief = content?.brief[windowSize] ?? [];
  const leads = content?.discovery?.[windowSize];
  const unassignedLeads = content?.discovery?.[windowSize === "day" ? "unassigned_day" : "unassigned_week"]?.filter((lead) => lead.countries.length === 0) ?? [];
  const coverage = content?.coverage;
  const hasCountryMaterial = Boolean(content && (
    (leads?.length ?? 0) > 0 || brief.length > 0 || content.positions.length > 0
    || content.changes.length > 0 || content.topics.length > 0
  ));

  return (
    <section aria-labelledby={titleId} className="reveal reveal-1 mt-5 pb-10">
      <header className="mb-4 flex flex-wrap items-end justify-between gap-x-5 gap-y-2 border-b border-line pb-3">
        <div>
          <h2 id={titleId} className="display text-[29px] leading-tight sm:text-[36px]">Россия и мир</h2>
        </div>
        {content && <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <p className="tnum text-[11px] text-dim">Обновлено {dateTime(content.as_of)}</p>
          <button type="button" disabled={state === "refreshing"} onClick={() => setReload((value) => value + 1)}
            className="min-h-11 text-xs text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-wait disabled:opacity-60 sm:min-h-0">
            Обновить
          </button>
        </div>}
      </header>

      <div className="grid gap-3 lg:grid-cols-[minmax(0,2fr)_minmax(270px,1fr)]">
        <section className="card flex h-[280px] min-w-0 flex-col sm:h-[400px] lg:h-[520px] xl:h-[540px]" aria-label="Карта отношений России и мира">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-b border-line px-4 py-2 sm:px-5">
            <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-dim">Карта отношений</p>
            {mapControls && <details className="relative z-20 text-xs">
              <summary className="cursor-pointer text-accent focus-visible:outline-2 focus-visible:outline-accent">Фильтры обзора{activeMapFilters > 0 ? ` · ${activeMapFilters}` : ""}</summary>
              <div className="card absolute right-0 top-full z-30 mt-2 w-[min(90vw,520px)] p-3 shadow-xl">
                <p className="text-xs leading-5 text-dim">Регион и уровень — карта; тема — новости и брифинг. Выбранная страна остаётся на карте.</p>
                {mapControls}
              </div>
            </details>}
          </div>
          <div className="min-h-0 flex-1 overflow-hidden rounded-b-[10px] bg-[#0b0f14]">{renderMap?.({ selectedCountry: currentCountry || null, onSelectCountry: selectCountry })}</div>
        </section>
        <aside className="card min-w-0 overflow-hidden p-4 lg:flex lg:h-[520px] lg:flex-col xl:h-[540px]" aria-labelledby={`${titleId}-attention`}>
          <p className="text-[10px] font-medium uppercase tracking-[0.18em] text-ru-red">Свежие сигналы</p>
          <h3 id={`${titleId}-attention`} className="display mt-1 text-[23px] leading-tight">Где требуется внимание</h3>
          <p className="mt-2 text-xs leading-5 text-dim">Страны, о которых появились новые публикации.</p>
          <label htmlFor={`${titleId}-country`} className="mt-4 block text-[10px] uppercase tracking-wide text-dim">Выбранная страна</label>
          <select
            id={`${titleId}-country`}
            value={currentCountry}
            disabled={countryOptions.length === 0}
            onChange={(event) => selectCountry(event.target.value)}
            className="mt-1 min-h-11 w-full rounded-md border border-line bg-panel2 px-3 text-sm text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-60"
          >
            {countryOptions.length === 0 && <option value="">Загружаем страны…</option>}
            {countryOptions.map((country) => <option key={country.code} value={country.code}>{country.name}</option>)}
          </select>
          {state === "loading" && <p className="mt-4 text-xs text-dim">Загружаем сообщения…</p>}
          {state === "error" && <p className="mt-4 text-xs text-dim">Сообщения сейчас недоступны.</p>}
          {content && content.attention.length === 0 && <Empty>За последние сутки новых отобранных сообщений нет. Это не означает отсутствия событий.</Empty>}
          {content && content.attention.length > 0 && (
            <div className="mt-3 min-h-0 divide-y divide-line overflow-y-auto border-y border-line lg:flex-1">
              {content.attention.slice(0, 4).map((item) => <AttentionItem key={item.code} item={item} currentCountry={currentCountry} onSelect={selectCountry} />)}
              {content.attention.length > 4 && <details className="border-t border-line px-2 py-2 text-xs">
                <summary className="cursor-pointer py-1 text-accent focus-visible:outline-2 focus-visible:outline-accent">Показать остальные страны ({content.attention.length})</summary>
                <div className="mt-2 divide-y divide-line">{content.attention.slice(4).map((item) => <AttentionItem key={item.code} item={item} currentCountry={currentCountry} onSelect={selectCountry} />)}</div>
              </details>}
            </div>
          )}
        </aside>
      </div>

      <div id="country-overview" className="mt-8 min-w-0 scroll-mt-5 border-t border-line pt-5" aria-busy={state === "loading" || state === "refreshing"}>
          {state === "loading" && <div role="status" className="card flex min-h-48 items-center gap-3 px-5 text-sm text-dim"><LoaderCircle aria-hidden="true" size={17} className="animate-spin motion-reduce:animate-none" />Загружаем обзор…</div>}
          {state === "error" && <div role="alert" className="card border-ru-red/50 p-5"><p className="text-sm text-fg">Не удалось загрузить обзор страны.</p><button type="button" onClick={() => setReload((value) => value + 1)} className="mt-3 min-h-11 text-sm text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">Повторить загрузку</button></div>}
          {state === "refreshing" && <p role="status" className="mb-3 flex items-center gap-2 border-l-2 border-ru-blue/70 pl-3 text-xs text-dim"><LoaderCircle aria-hidden="true" size={13} className="animate-spin motion-reduce:animate-none" />Обновляем обзор…</p>}
          {state === "refreshError" && content && <p role="alert" className="mb-3 border-l-2 border-ru-red/70 pl-3 text-xs leading-5 text-dim">Не удалось обновить обзор. Показаны данные от {dateTime(content.as_of)}. Используйте «Обновить», чтобы повторить.</p>}
          {content && <>
            <div className="mb-6 flex flex-wrap items-end justify-between gap-x-5 gap-y-3 border-b border-line pb-5">
              <div><p className="text-[10px] font-medium uppercase tracking-[0.18em] text-ru-red">Выбранная страна</p><h3 className="display mt-1 text-[30px] leading-tight sm:text-[36px]">{content.country.name} ↔ Россия</h3></div>
              <div className="flex flex-wrap items-end gap-x-5 gap-y-2">
                <label className="text-[10px] uppercase tracking-wide text-dim">Период<select aria-label="Период материалов" value={windowSize} onChange={(event) => setWindowSize(event.target.value as "day" | "week")} className="mt-1 block min-h-11 rounded-md border border-line bg-panel2 px-3 text-sm text-fg focus-visible:outline-2 focus-visible:outline-accent"><option value="day">24 часа</option><option value="week">7 суток</option></select></label>
                <Link href={`/country/${encodeURIComponent(content.country.code)}`} className="min-h-11 content-center text-xs text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">Полное досье страны →</Link>
              </div>
            </div>
            <div className="grid gap-x-8 gap-y-8 lg:grid-cols-[minmax(0,1fr)_300px]">
            <div className="min-w-0 space-y-8">
            {leads === undefined && <p className="border-t border-line pt-4 text-sm leading-6 text-dim">Не удалось загрузить сообщения по стране.</p>}
            {leads !== undefined && !hasCountryMaterial && <p className="border-t border-line pt-4 text-sm leading-6 text-dim">По выбранной стране за этот период материалов пока нет. Отсутствие сообщений не означает отсутствия событий.</p>}
            {leads && leads.length > 0 && <section className="min-w-0" aria-labelledby={`${titleId}-discovery`}>
              <div id={`${titleId}-discovery`}><Heading title="В новостях" description="Что пишут СМИ о стране и России. Сообщения ещё не проверены." /></div>
              <NewsReadingList key={`${currentCountry}-${windowSize}`} leads={leads} />
            </section>}
              {brief.length > 0 && <section className="min-w-0 border-t border-line pt-4" aria-labelledby={`${titleId}-brief`}>
                <div id={`${titleId}-brief`}><Heading title="Страна за 60 секунд" /></div>
                <div className="mt-2 grid gap-x-6 lg:grid-cols-2">{brief.map((item) => <EvidenceRow key={item.article_id} item={item} />)}</div>
              </section>}

              {content.positions.length > 0 && <details className="min-w-0 border-t border-line py-4" aria-labelledby={`${titleId}-positions`}>
                <summary id={`${titleId}-positions`} className="cursor-pointer text-sm font-medium text-ru-white focus-visible:outline-2 focus-visible:outline-accent">Кто какую позицию занимает <span className="tnum text-dim">({content.positions.length})</span></summary>
                <p className="mt-2 text-xs text-dim">Только прямо названные заявления и действия в публикации.</p>
                <div className="divide-y divide-line">{content.positions.map((item) => <article key={item.id} className="py-3"><p className="text-xs font-semibold text-ru-white">{item.actor}</p><p className="mt-1 text-sm leading-5">{item.position_ru}</p><p className="mt-1 text-[11px] text-dim">По сообщению: {item.evidence.publisher_name} · {dateTime(item.evidence.published_at)}</p><SourceEvidence evidence={item.evidence} quote={item.evidence_quote} /></article>)}</div>
              </details>}

              {content.changes.length > 0 && <details className="min-w-0 border-t border-line py-4" aria-labelledby={`${titleId}-changes`}>
                <summary id={`${titleId}-changes`} className="cursor-pointer text-sm font-medium text-ru-white focus-visible:outline-2 focus-visible:outline-accent">Что меняется для российских граждан и организаций <span className="tnum text-dim">({content.changes.length})</span></summary>
                <p className="mt-2 text-xs text-dim">Предложения, решения и вступившие в силу меры различаются по тексту источника.</p>
                <div className="divide-y divide-line">{content.changes.map((item) => <article key={item.id} className="py-3"><p className="text-sm leading-5 text-fg">{item.change_ru}</p><p className="mt-1 text-[11px] text-dim">По сообщению: {item.evidence.publisher_name} · {dateTime(item.evidence.published_at)}</p><SourceEvidence evidence={item.evidence} quote={item.evidence_quote} /></article>)}</div>
              </details>}

              {content.topics.length > 0 && <details className="min-w-0 border-t border-line py-4" aria-labelledby={`${titleId}-topics`}>
                <summary id={`${titleId}-topics`} className="cursor-pointer text-sm font-medium text-ru-white focus-visible:outline-2 focus-visible:outline-accent">Темы для разговора <span className="tnum text-dim">({content.topics.length})</span></summary>
                <p className="mt-2 text-xs text-dim">Вопросы для проверки, без предположения о согласии сторон.</p>
                <div className="divide-y divide-line">{content.topics.map((item) => <article key={item.id} className="py-3"><h4 className="text-sm font-medium text-ru-white">{item.title}</h4><p className="mt-1 text-xs leading-5">{item.question}</p>{item.evidence.map((evidence) => <SourceEvidence key={evidence.article_id} evidence={evidence} />)}</article>)}</div>
              </details>}
            </div>

              <aside className="min-w-0 self-start rounded-xl border border-line bg-panel/70 p-5" aria-labelledby={`${titleId}-coverage`}>
                <div id={`${titleId}-coverage`}><Heading title="Что вошло в обзор" description="Местные источники · за 7 дней" /></div>
                {coverage && <>
                  <p className="mt-3 text-sm leading-6 text-fg/75">Мы видим только часть информационной картины. Отсутствие новостей не означает, что ничего не происходит.</p>
                  {coverage.triage_status && <p role="status" className={`mt-3 text-xs leading-5 ${coverage.triage_status === "budget_exhausted" ? "text-cooling" : "text-dim"}`}>{{ not_started: "Обработка ещё не началась.", partial: "Обработана часть материалов.", up_to_date: "Поступившие материалы обработаны.", budget_exhausted: "Обработка приостановлена: достигнут лимит." }[coverage.triage_status]}</p>}
                  <dl className="mt-4 grid grid-cols-2 gap-4 text-xs">
                    <div><dt className="text-dim">Публикаций за неделю</dt><dd className="tnum mt-1 text-xl text-ru-white">{coverage.collected_from_country_7d}</dd></div>
                    <div><dt className="text-dim">Групп источников</dt><dd className="tnum mt-1 text-xl text-ru-white">{coverage.local_publisher_families}</dd></div>
                  </dl>
                  <p className="mt-3 text-[11px] leading-5 text-dim">Новые статьи получены: {dateTime(coverage.last_collected_at)}</p>
                  {coverage.truncated && <p role="status" className="mt-2 text-xs leading-5 text-cooling">Выборка ограничена: показана часть публикаций.</p>}
                  <details className="mt-4 border-t border-line pt-2">
                    <summary className="inline-flex min-h-11 cursor-pointer items-center text-sm text-accent focus-visible:outline-2 focus-visible:outline-accent">Как собраны данные</summary>
                  <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-xs"><div><dt className="text-dim">Собрано у местных издателей</dt><dd className="tnum mt-1 text-lg text-ru-white">{coverage.collected_from_country_7d}</dd></div>{coverage.classified_from_country_7d !== undefined && <div><dt className="text-dim">Размечено</dt><dd className="tnum mt-1 text-lg text-ru-white">{coverage.classified_from_country_7d}</dd></div>}{coverage.pending_from_country_7d !== undefined && <div><dt className="text-dim">Ожидает разметки</dt><dd className="tnum mt-1 text-lg text-ru-white">{coverage.pending_from_country_7d}</dd></div>}<div><dt className="text-dim">Подробно проверено моделью</dt><dd className="tnum mt-1 text-lg text-ru-white">{coverage.reviewed_from_country_7d}</dd></div><div><dt className="text-dim">Выделена связь со страной и РФ</dt><dd className="tnum mt-1 text-lg text-ru-white">{coverage.relevant_to_country_7d}</dd></div><div><dt className="text-dim">Семейств издателей в выборке</dt><dd className="tnum mt-1 text-lg text-ru-white">{coverage.publisher_families}</dd></div></dl>
                  {coverage.discovered_to_country_7d !== undefined && <p className="mt-3 border-t border-line pt-3 text-xs leading-5 text-dim">Кандидаты о стране из всех источников: <strong className="tnum text-ru-white">{coverage.discovered_to_country_7d}</strong>. Это другой круг публикаций; число не входит в долю местных издателей.</p>}
                  {coverage.triage_status && <p className="mt-3 text-xs leading-5 text-dim">Последняя разметка: {dateTime(coverage.last_classified_at ?? null)}.</p>}
                  <p className="mt-3 text-xs leading-5 text-dim">Связанные издания объединены в группы: несколько сайтов одной медиагруппы не считаются независимыми источниками. Новые статьи получены: {dateTime(coverage.last_collected_at)}. Последний анализ: {dateTime(coverage.last_analyzed_at)}.</p>
                  <p className="mt-2 border-l-2 border-ru-blue/70 pl-3 text-xs leading-5 text-dim">Независимость подтверждений не оценивалась. Перепечатки могут повторять одно сообщение.</p>
                  {coverage.limitations.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-5 text-dim">{coverage.limitations.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>}
                  </details>
                </>}
              </aside>
            </div>
          </>}
        </div>
        {content && unassignedLeads.length > 0 && <section aria-labelledby={`${titleId}-world`}
          className="mt-10 grid min-w-0 gap-x-10 gap-y-3 rounded-xl border border-line bg-panel/70 p-5 sm:p-6 lg:grid-cols-[minmax(180px,240px)_minmax(0,1fr)]">
          <header>
            <Globe2 aria-hidden="true" size={22} className="mb-3 text-accent" />
            <h3 id={`${titleId}-world`} className="display text-[28px] leading-tight">В мире</h3>
            <p className="mt-2 text-xs font-medium text-accent">{windowSize === "day" ? "За последние 24 часа" : "За последние 7 дней"}</p>
            <p className="mt-3 text-sm leading-6 text-fg/80">Новости о России из разных стран. Связь этих публикаций с выбранной страной не установлена.</p>
            <p className="mt-3 text-xs leading-5 text-fg/65">Сообщения СМИ ещё не проверены.</p>
          </header>
          <NewsReadingList key={`world-${currentCountry}-${windowSize}`} leads={unassignedLeads} initialCount={3} />
        </section>}
    </section>
  );
}
