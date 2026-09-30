"use client";

import { useEffect, useId, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, LoaderCircle } from "lucide-react";
import { api } from "@/lib/api";
import type { DecisionEvidence, DecisionWorkspaceResponse } from "@/lib/decisionTypes";

type LoadState = "loading" | "ready" | "error";

function dateTime(value: string | null): string {
  if (!value) return "нет данных";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "нет данных";
  return `${new Intl.DateTimeFormat("ru-RU", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC",
  }).format(date)} UTC`;
}

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

function Heading({ number, title, description }: { number: string; title: string; description?: string }) {
  return <div className="border-b border-line pb-3">
    <p className="section-num text-[10px]">{number}</p>
    <h3 className="display mt-1 text-[21px] leading-tight">{title}</h3>
    {description && <p className="mt-1 text-xs leading-5 text-dim">{description}</p>}
  </div>;
}

export default function DecisionWorkspace() {
  const titleId = useId();
  const [initialized, setInitialized] = useState(false);
  const [selectedCountry, setSelectedCountry] = useState<string | null>(null);
  const [payload, setPayload] = useState<DecisionWorkspaceResponse | null>(null);
  const [state, setState] = useState<LoadState>("loading");
  const [reload, setReload] = useState(0);
  const [windowSize, setWindowSize] = useState<"day" | "week">("day");

  useEffect(() => {
    const readCountry = () => setSelectedCountry(new URLSearchParams(window.location.search).get("country"));
    readCountry();
    setInitialized(true);
    window.addEventListener("popstate", readCountry);
    return () => window.removeEventListener("popstate", readCountry);
  }, []);

  useEffect(() => {
    if (!initialized) return;
    const controller = new AbortController();
    setState("loading");
    api.decisionWorkspace(selectedCountry, controller.signal).then((result) => {
      if (controller.signal.aborted) return;
      if (selectedCountry && result.country.code !== selectedCountry.toUpperCase()) {
        setState("error");
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
      if (!controller.signal.aborted) setState("error");
    });
    return () => controller.abort();
  }, [initialized, selectedCountry, reload]);

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
  const content = state === "ready" && (!selectedCountry || payload?.country.code === selectedCountry.toUpperCase()) ? payload : null;
  const brief = content?.brief[windowSize] ?? [];
  const coverage = content?.coverage;

  return (
    <section aria-labelledby={titleId} className="reveal reveal-1 mt-6 border-y border-line pb-6 pt-5">
      <header className="mb-5 flex flex-wrap items-end justify-between gap-x-5 gap-y-3">
        <div>
          <p className="section-num">АНАЛИТИЧЕСКОЕ РАБОЧЕЕ МЕСТО / 01</p>
          <h2 id={titleId} className="display mt-1 text-[28px] leading-tight sm:text-[34px]">Россия и мир: что изменилось</h2>
          <p className="mt-2 max-w-3xl text-xs leading-5 text-dim">Сообщения подключённых СМИ, отобранные машинным анализом. Цитаты позволяют проверить основание; сообщение не означает подтверждённый факт или действующую норму.</p>
        </div>
        {content && <p className="tnum text-[11px] text-dim">срез {dateTime(content.as_of)}</p>}
      </header>

      <div className="grid gap-3 lg:grid-cols-[minmax(230px,280px)_minmax(0,1fr)]">
        <aside className="card h-fit overflow-hidden p-4" aria-labelledby={`${titleId}-attention`}>
          <p className="section-num text-[10px]">01 / ПРИОРИТЕТЫ</p>
          <h3 id={`${titleId}-attention`} className="display mt-1 text-xl leading-tight">Где требуется внимание сегодня</h3>
          <p className="mt-2 text-xs leading-5 text-dim">Страны с новыми публикациями, где модель выделила связь с Россией; число сообщений не определяет важность.</p>
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
          {content && content.attention.length === 0 && <Empty>За последние сутки новых отобранных сообщений нет. Это не означает отсутствия событий.</Empty>}
          {content && content.attention.length > 0 && (
            <div className="mt-3 max-h-[430px] divide-y divide-line overflow-y-auto border-y border-line">
              {content.attention.map((item) => (
                <button key={item.code} type="button" onClick={() => selectCountry(item.code)} aria-current={currentCountry === item.code ? "true" : undefined}
                  className={`flex min-h-11 w-full items-start justify-between gap-3 px-2 py-2.5 text-left focus-visible:outline-2 focus-visible:outline-inset focus-visible:outline-accent ${currentCountry === item.code ? "bg-panel2 text-ru-white" : "text-fg hover:bg-panel2/60"}`}>
                  <span className="min-w-0"><span className="block text-sm font-medium">{item.name}</span><span className="mt-0.5 block text-[11px] leading-4 text-dim">{item.reason}</span></span>
                  <span className="tnum shrink-0 text-right text-xs"><strong className="text-ru-white">{item.count_24h}</strong><span className="block text-[10px] text-dim">24 ч · {item.count_7d} / 7 д</span></span>
                </button>
              ))}
            </div>
          )}
        </aside>

        <div className="min-w-0" aria-busy={state === "loading"}>
          {state === "loading" && <div role="status" className="card flex min-h-48 items-center gap-3 px-5 text-sm text-dim"><LoaderCircle aria-hidden="true" size={17} className="animate-spin motion-reduce:animate-none" />Собираем срез по стране…</div>}
          {state === "error" && <div role="alert" className="card border-ru-red/50 p-5"><p className="text-sm text-fg">Не удалось загрузить аналитический срез.</p><button type="button" onClick={() => setReload((value) => value + 1)} className="mt-3 min-h-11 text-sm text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">Повторить загрузку</button></div>}
          {content && <>
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2 border-b border-line pb-3">
              <div><p className="section-num text-[10px]">ВЫБРАННАЯ СТРАНА</p><h3 className="display text-[27px] leading-tight">{content.country.name} ↔ Россия</h3></div>
              <Link href={`/country/${encodeURIComponent(content.country.code)}`} className="min-h-11 content-center text-xs text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:min-h-0">Полное досье страны →</Link>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <section className="card p-4 md:col-span-2" aria-labelledby={`${titleId}-brief`}>
                <div className="flex flex-wrap items-start justify-between gap-3"><div id={`${titleId}-brief`}><Heading number="02 / СВОДКА" title="Страна за 60 секунд" description="Короткие сообщения с проверяемыми цитатами из исходных публикаций." /></div>
                  <label className="text-[10px] uppercase tracking-wide text-dim">Период<select aria-label="Период сводки" value={windowSize} onChange={(event) => setWindowSize(event.target.value as "day" | "week")} className="mt-1 min-h-11 rounded-md border border-line bg-panel2 px-3 text-sm text-fg focus-visible:outline-2 focus-visible:outline-accent"><option value="day">24 часа</option><option value="week">7 суток</option></select></label>
                </div>
                {brief.length === 0 ? <Empty>За выбранный период среди обработанных источников нет публикаций, где модель выделила явную связь со страной и Россией.</Empty> : <div className="mt-2 grid gap-x-6 lg:grid-cols-2">{brief.map((item) => <EvidenceRow key={item.article_id} item={item} />)}</div>}
              </section>

              <section className="card min-w-0 p-4" aria-labelledby={`${titleId}-positions`}>
                <div id={`${titleId}-positions`}><Heading number="03 / АКТОРЫ" title="Кто какую позицию занимает" description="Только прямо названные заявления и действия в публикации." /></div>
                {content.positions.length === 0 ? <Empty>Модель не выделила явно атрибутированных позиций в обработанных материалах.</Empty> : <div className="divide-y divide-line">{content.positions.map((item) => <article key={item.id} className="py-3"><p className="text-xs font-semibold text-ru-white">{item.actor}</p><p className="mt-1 text-sm leading-5">{item.position_ru}</p><p className="mt-1 text-[11px] text-dim">По сообщению: {item.evidence.publisher_name} · {dateTime(item.evidence.published_at)}</p><SourceEvidence evidence={item.evidence} quote={item.evidence_quote} /></article>)}</div>}
              </section>

              <section className="card min-w-0 p-4" aria-labelledby={`${titleId}-changes`}>
                <div id={`${titleId}-changes`}><Heading number="04 / ПОСЛЕДСТВИЯ" title="Что меняется для российских граждан и организаций" description="Предложения, решения и вступившие в силу меры различаются по тексту источника." /></div>
                {content.changes.length === 0 ? <Empty>Модель не выделила конкретных изменений для граждан или организаций РФ в обработанных материалах.</Empty> : <div className="divide-y divide-line">{content.changes.map((item) => <article key={item.id} className="py-3"><p className="text-sm leading-5 text-fg">{item.change_ru}</p><p className="mt-1 text-[11px] text-dim">По сообщению: {item.evidence.publisher_name} · {dateTime(item.evidence.published_at)}</p><SourceEvidence evidence={item.evidence} quote={item.evidence_quote} /></article>)}</div>}
              </section>

              <section className="card min-w-0 p-4" aria-labelledby={`${titleId}-topics`}>
                <div id={`${titleId}-topics`}><Heading number="05 / ВОПРОСЫ" title="Темы для разговора" description="Вопросы для проверки, без предположения о согласии сторон." /></div>
                {content.topics.length === 0 ? <Empty>По обработанным публикациям пока нет тем с проверяемым основанием.</Empty> : <div className="divide-y divide-line">{content.topics.map((item) => <article key={item.id} className="py-3"><h4 className="text-sm font-medium text-ru-white">{item.title}</h4><p className="mt-1 text-xs leading-5">{item.question}</p>{item.evidence.map((evidence) => <SourceEvidence key={evidence.article_id} evidence={evidence} />)}</article>)}</div>}
              </section>

              <section className="card min-w-0 p-4" aria-labelledby={`${titleId}-coverage`}>
                <div id={`${titleId}-coverage`}><Heading number="06 / ПОКРЫТИЕ" title="Насколько полна картина" description="Только подключённые источники и обработанная часть корпуса." /></div>
                {coverage && <>
                  <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-3 text-xs"><div><dt className="text-dim">Публикаций местных СМИ за 7 суток</dt><dd className="tnum mt-1 text-lg text-ru-white">{coverage.collected_from_country_7d}</dd></div><div><dt className="text-dim">Из них разобрано моделью</dt><dd className="tnum mt-1 text-lg text-ru-white">{coverage.reviewed_from_country_7d}</dd></div><div><dt className="text-dim">Модель выделила связь со страной и РФ</dt><dd className="tnum mt-1 text-lg text-ru-white">{coverage.relevant_to_country_7d}</dd></div><div><dt className="text-dim">Семейств издателей в выборке</dt><dd className="tnum mt-1 text-lg text-ru-white">{coverage.publisher_families}</dd></div></dl>
                  <p className="mt-3 text-xs leading-5 text-dim">Местных семейств издателей в выборке: {coverage.local_publisher_families}. Последний сбор: {dateTime(coverage.last_collected_at)}. Последний анализ: {dateTime(coverage.last_analyzed_at)}.</p>
                  <p className="mt-2 border-l-2 border-ru-blue/70 pl-3 text-xs leading-5 text-dim">Независимость подтверждений не оценивалась. Перепечатки могут повторять одно сообщение.</p>
                  {coverage.truncated && <p role="status" className="mt-2 text-xs leading-5 text-cooling">Выборка ограничена: показана только часть подходящих публикаций.</p>}
                  {coverage.limitations.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-5 text-dim">{coverage.limitations.map((item, index) => <li key={`${index}-${item}`}>{item}</li>)}</ul>}
                </>}
              </section>
            </div>
          </>}
        </div>
      </div>
    </section>
  );
}
