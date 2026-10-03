"use client";

import { FormEvent, useEffect, useId, useState } from "react";
import { ChevronDown, LoaderCircle, Search } from "lucide-react";
import { api } from "@/lib/api";
import type { AgendaArticle, AgendaCoverage, AgendaItem, AgendasResponse } from "@/lib/types";
import { safeHttpUrl } from "@/lib/urls";

const RELATIONS: Record<AgendaArticle["relation"], string> = {
  seed: "Исходная публикация",
  same_event: "То же событие",
  development: "Развитие и реакция",
};
const RUN_STATUS: Record<AgendaCoverage["status"], string> = {
  never_run: "Сбор повесток ещё не запускался.",
  ok: "Последний проход завершён",
  budget_exhausted: "Лимит бюджета достигнут. Поиск новых связей приостановлен; собранные публикации доступны.",
  error: "Последний проход завершился с ошибкой. Показаны ранее собранные повестки.",
  disabled: "Поиск новых повесток отключён. Собранные публикации доступны.",
  running: "Идёт обновление повесток. Показаны уже собранные публикации.",
};

function formatDate(value: string | null): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow",
  }).format(date);
}

function countryName(code: string): string {
  try { return new Intl.DisplayNames(["ru"], { type: "region" }).of(code) ?? code; }
  catch { return code; }
}

function SourceTitle({ title, titleRu, href, heading = false }: {
  title: string;
  titleRu?: string | null;
  href?: string | null;
  heading?: boolean;
}) {
  const [showOriginal, setShowOriginal] = useState(false);
  const titleId = useId();
  const translation = typeof titleRu === "string" ? titleRu.trim() : "";
  const hasTranslation = Boolean(translation && translation !== title.trim());
  const isTranslation = hasTranslation && !showOriginal;
  const displayedTitle = isTranslation ? translation : title;
  return <>
    {heading ? (
      <h3 id={titleId} lang={isTranslation ? "ru" : undefined} className="mt-3 break-words text-lg font-medium leading-7">{displayedTitle}</h3>
    ) : href ? (
      <a id={titleId} lang={isTranslation ? "ru" : undefined} href={href} target="_blank" rel="noopener noreferrer" className="break-words text-sm leading-6 text-fg underline decoration-line underline-offset-4 hover:text-accent focus-visible:outline-2 focus-visible:outline-accent">{displayedTitle}</a>
    ) : <p id={titleId} lang={isTranslation ? "ru" : undefined} className="break-words text-sm leading-6 text-fg">{displayedTitle}</p>}
    {hasTranslation && <div className="mt-1 flex flex-wrap items-center gap-x-3 text-xs text-dim">
      <span>{isTranslation ? "Машинный перевод" : "Оригинал источника"}</span>
      <button type="button" aria-controls={titleId} onClick={() => setShowOriginal((current) => !current)} className="min-h-11 text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-accent">
        {showOriginal ? "Показать перевод" : "Показать оригинал"}
      </button>
    </div>}
  </>;
}

function Evidence({ article, model }: { article: AgendaArticle; model: string | null }) {
  const url = safeHttpUrl(article.url);
  const published = article.date_warning ? null : formatDate(article.published_at);
  const date = published ?? formatDate(article.collected_at);
  return (
    <li className="min-w-0 py-4 first:pt-0 last:pb-0">
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-dim">
        <span className="rounded border border-line px-2 py-1 text-fg">{RELATIONS[article.relation] ?? "Связь не указана"}</span>
        <span>{article.source_name} · {countryName(article.country_code)}</span>
      </div>
      <SourceTitle title={article.title} titleRu={article.title_ru} href={url} />
      {url && <a
        href={`https://translate.google.com/translate?sl=auto&tl=ru&u=${encodeURIComponent(url)}`}
        target="_blank" rel="noopener noreferrer"
        title="Открыть русский перевод в Google Переводчике"
        className="mt-1 inline-flex min-h-11 items-center text-xs text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-accent"
      >Перевести публикацию</a>}
      <p className="mt-2 text-xs leading-5 text-dim">
        {date ? `${published ? "Опубликовано" : "Собрано"}: ${date} МСК` : "Дата не указана"}
        {article.date_warning && " · дата публикации требует проверки"}
      </p>
      {article.relation !== "seed" && article.confidence != null && Number.isFinite(article.confidence) && (
        <p className="mt-1 text-xs leading-5 text-dim">{/^typesafe\/jev(?:-|$)/i.test(model ?? "") ? "Уверенность в связи" : "Оценка модели"}: {Math.round(article.confidence * 100)}%</p>
      )}
    </li>
  );
}

export function AgendaCard({ item }: { item: AgendaItem }) {
  const [expanded, setExpanded] = useState(false);
  const evidenceId = useId();
  const verified = Boolean(item.model && item.model !== "lexical-fallback"
    && item.same_event_count + item.development_count > 0);
  return (
    <article className="card min-w-0 p-4 sm:p-5">
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-dim">
        <span className="uppercase tracking-[0.08em]">Заголовок источника</span>
        {verified && <span title={item.model ?? undefined} className="rounded border border-line px-2 py-1">Связи сопоставлены ИИ</span>}
      </div>
      <SourceTitle title={item.title} titleRu={item.title_ru} heading />
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-dim">
        <span>Публикаций: {item.article_count}</span><span>Источников: {item.source_count}</span>
        <span>Последняя публикация собрана: {formatDate(item.last_seen) ?? "дата не указана"}{formatDate(item.last_seen) && " МСК"}</span>
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-dim">
        <span>Страны издателей:</span>
        {item.countries.map((code) => <span key={code} className="rounded border border-line px-2 py-1">{countryName(code)}</span>)}
        {!item.countries.length && <span>не указаны</span>}
      </div>
      <button
        type="button" aria-expanded={expanded} aria-controls={evidenceId}
        onClick={() => setExpanded((current) => !current)}
        className="mt-4 flex min-h-11 items-center gap-2 rounded text-xs text-accent focus-visible:outline-2 focus-visible:outline-accent"
      >
        {expanded ? "Скрыть публикации" : "Показать публикации"}
        <ChevronDown aria-hidden="true" size={14} className={expanded ? "rotate-180" : ""} />
      </button>
      {expanded && (
        <div id={evidenceId} className="mt-2 border-t border-line pt-4">
          <p className="mb-4 text-xs leading-5 text-dim">Оценка модели относится к связи публикаций, а не к достоверности утверждений. Перепечатки могут повторять один источник.</p>
          <ul className="divide-y divide-line">{item.articles.map((article) => <Evidence key={article.id} article={article} model={item.model} />)}</ul>
          {item.article_count > item.articles.length && <p className="mt-4 text-xs text-dim">Показаны последние {item.articles.length} из {item.article_count} публикаций.</p>}
        </div>
      )}
    </article>
  );
}

export default function AgendaPanel() {
  const titleId = useId();
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [reload, setReload] = useState(0);
  const [payload, setPayload] = useState<AgendasResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(false); setPayload(null);
    api.agendas({ limit: 20, ...(query ? { q: query } : {}) }, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setPayload(value); })
      .catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [query, reload]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = draft.trim();
    if (next === query) setReload((current) => current + 1);
    else setQuery(next);
  }

  const coverage = payload?.coverage;
  const lastRun = formatDate(coverage?.last_run_at ?? null);
  return (
    <section aria-labelledby={titleId} className="border-b border-line pb-8 pt-10">
      <div className="flex flex-wrap items-end justify-between gap-5">
        <div className="max-w-2xl">
          <p className="section-num">ПУБЛИКАЦИИ / ПОВЕСТКИ</p>
          <h2 id={titleId} className="display mt-2 text-[32px] leading-tight sm:text-[40px]">Новостные повестки</h2>
          <p className="mt-3 text-sm leading-6 text-dim">События и их развитие среди подключённых источников. Заголовок взят из публикации; откройте источники, чтобы сопоставить сообщения.</p>
        </div>
        <form onSubmit={submit} role="search" aria-label="Поиск повесток" className="flex w-full gap-2 sm:w-auto">
          <label className="relative min-w-0 flex-1">
            <span className="sr-only">Поиск по повесткам</span>
            <Search aria-hidden="true" size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-dim" />
            <input type="search" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Найти повестку…" className="min-h-11 w-full rounded-md border border-line bg-panel2 pl-9 pr-3 text-base text-fg outline-none focus:border-accent sm:w-52 sm:text-sm" />
          </label>
          <button type="submit" aria-label="Найти повестки" className="min-h-11 rounded-md border border-line px-4 text-sm text-fg hover:border-accent focus-visible:outline-2 focus-visible:outline-accent">Найти</button>
        </form>
      </div>
      {coverage && (
        <div className="mt-5 border-l-2 border-ru-blue/70 pl-3 text-xs leading-5 text-dim">
          <p role="status">{RUN_STATUS[coverage.status]}</p>
          {lastRun && <p>Последний проход: {lastRun} МСК</p>}
          <p>Просмотрено публикаций: {coverage.articles_scanned} · Групп-кандидатов: {coverage.candidate_groups} · Сопоставлено связей: {coverage.decisions} · Принято связей: {coverage.accepted}</p>
          <p>Страны обозначают местонахождение издателей.</p>
        </div>
      )}
      {loading && <p role="status" className="mt-6 flex items-center gap-2 text-sm text-dim"><LoaderCircle aria-hidden="true" size={16} className="animate-spin motion-reduce:animate-none" />Загружаем повестки…</p>}
      {error && (
        <div role="alert" className="card mt-6 p-4 text-sm">
          <p>Не удалось загрузить повестки.</p>
          <button type="button" onClick={() => setReload((current) => current + 1)} className="mt-2 min-h-11 rounded text-accent focus-visible:outline-2 focus-visible:outline-accent">Повторить загрузку повесток</button>
        </div>
      )}
      {payload && !payload.items.length && coverage?.status !== "never_run" && <p className="card mt-6 p-5 text-sm leading-6 text-dim">{query ? `По запросу «${query}» повесток не найдено. Попробуйте другие слова.` : "Повестки пока не сформированы среди просмотренных публикаций. Это не означает отсутствия событий."}</p>}
      {!!payload?.items.length && <div className="mt-6 grid gap-3">{payload.items.map((item) => <AgendaCard key={item.id} item={item} />)}</div>}
      {payload?.has_more && <p className="mt-4 text-xs leading-5 text-dim">Показаны первые {payload.items.length} повесток. Уточните поиск, чтобы найти другие.</p>}
    </section>
  );
}
