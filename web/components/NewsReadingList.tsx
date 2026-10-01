"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ArrowDown, ArrowUpRight, ChevronDown } from "lucide-react";
import type { NewsLead } from "@/lib/decisionTypes";

export function newsDateTime(value: string | null): string {
  if (!value || Number.isNaN(new Date(value).getTime())) return "нет данных";
  return `${new Intl.DateTimeFormat("ru-RU", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow",
  }).format(new Date(value))} мск`;
}

const topics: Record<string, string> = {
  sanctions: "Санкции", travel: "Поездки", business: "Бизнес", education: "Образование",
  culture: "Культура", security: "Безопасность", diplomacy: "Дипломатия", other: "Другие темы",
};
const events: Record<string, string> = {
  statement: "заявление", proposal: "предложение", decision: "решение",
  incident: "происшествие", analysis: "анализ", other: "другое сообщение",
};
const relations: Record<NewsLead["russia_relation"], string> = {
  direct: "прямая", indirect: "косвенная", uncertain: "требует уточнения",
};

function sourceUrl(value: string | null): string | null {
  try {
    const url = new URL(value ?? "");
    return ["https:", "http:"].includes(url.protocol) ? url.href : null;
  } catch { return null; }
}

function publisherCountry(code: string | null): string | null {
  if (!code || !/^[A-Z]{2}$/.test(code)) return null;
  return new Intl.DisplayNames(["ru"], { type: "region" }).of(code) ?? code;
}

function NewsRow({ lead }: { lead: NewsLead }) {
  const href = sourceUrl(lead.url);
  const title = lead.title_ru?.trim() || lead.title_original;
  const country = publisherCountry(lead.publisher_country_code);
  return <article className="min-w-0 border-b border-line py-5 last:border-b-0">
    <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-5 text-fg/75">
      <span className="font-medium text-fg">{lead.publisher_name || "Источник не указан"}</span>
      <span aria-hidden="true">·</span>
      <time dateTime={lead.published_at}>{newsDateTime(lead.published_at)}</time>
    </div>
    <h4 tabIndex={-1} className="max-w-[65ch] break-words rounded-sm text-lg font-medium leading-relaxed text-ru-white focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent sm:text-xl">{title}</h4>
    <div className="mt-2 flex flex-wrap items-start gap-x-6 gap-y-1 text-sm">
      {href && <a href={href} target="_blank" rel="noopener noreferrer"
        className="inline-flex min-h-11 items-center gap-1.5 rounded-sm text-accent underline-offset-4 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
        Открыть статью <ArrowUpRight aria-hidden="true" size={15} />
      </a>}
      <details className="group min-w-0 flex-1 basis-40">
        <summary className="flex min-h-11 w-fit cursor-pointer list-none items-center gap-1.5 rounded-sm text-fg/75 hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
          О публикации <ChevronDown aria-hidden="true" size={14} className="transition-transform group-open:rotate-180 motion-reduce:transition-none" />
        </summary>
        <div className="my-2 space-y-2 break-words rounded-lg bg-panel2/60 p-4 text-sm leading-6 text-fg/80">
          {lead.title_ru?.trim() && title !== lead.title_original && <p><span className="block text-xs text-fg/65">Заголовок в оригинале</span>{lead.title_original}</p>}
          <p>Тема: {topics[lead.topic.toLowerCase()] ?? "не определена"}</p>
          <p>Тип: {events[lead.event_type.toLowerCase()] ?? "не определён"}</p>
          <p>Связь с Россией: {relations[lead.russia_relation]}</p>
          {country && <p>Издатель: {country}. Это страна СМИ, а не обязательно место события.</p>}
          <p>Добавлено в обзор: {newsDateTime(lead.collected_at)}</p>
          {!href && <p>Ссылка на оригинал недоступна.</p>}
        </div>
      </details>
    </div>
  </article>;
}

export default function NewsReadingList({ leads, initialCount = 4 }: { leads: NewsLead[]; initialCount?: number }) {
  const listId = useId();
  const [language, setLanguage] = useState<"ru" | "original">("ru");
  const [expanded, setExpanded] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const revealFrom = useRef<number | null>(null);
  const translated = leads.filter((lead) => Boolean(lead.title_ru?.trim()));
  const originals = leads.filter((lead) => !lead.title_ru?.trim());
  const activeLanguage = language === "original" && originals.length > 0 ? "original" : "ru";
  const selected = activeLanguage === "ru" ? translated : originals;
  const visible = expanded ? selected : selected.slice(0, initialCount);
  const remaining = selected.length - visible.length;
  useEffect(() => {
    if (originals.length === 0 && language === "original") {
      setLanguage("ru");
      setExpanded(false);
    }
  }, [originals.length, language]);
  useEffect(() => {
    if (expanded && revealFrom.current !== null) {
      listRef.current?.querySelectorAll("h4")[revealFrom.current]?.focus();
      revealFrom.current = null;
    }
  }, [expanded]);
  return <div className="min-w-0">
    {originals.length > 0 && <div className="mt-4 flex flex-wrap gap-2" aria-label="Язык публикаций">
      {([['ru', 'На русском', translated.length], ['original', 'На языке источника', originals.length]] as const).filter(([, , count]) => count > 0).map(([value, label, count]) =>
        <button key={value} type="button" aria-pressed={activeLanguage === value} aria-controls={listId}
          onClick={() => { setLanguage(translated.length === 0 && language === "original" ? "ru" : value); setExpanded(false); }}
          className={`min-h-11 rounded-full border px-3.5 text-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${activeLanguage === value ? "border-accent/50 bg-accent/10 text-ru-white" : "border-line text-fg/75 hover:border-accent/40 hover:text-fg"}`}>
          {label} <span className="ml-1 tabular-nums opacity-70">({count})</span>
        </button>)}
    </div>}
    <div id={listId} ref={listRef}>
      {selected.length === 0 && <p className="py-5 text-sm leading-6 text-fg/75">Русского перевода пока нет. Можно прочитать публикации на языке источника.</p>}
      {activeLanguage === "original" && <p className="mt-3 text-sm leading-6 text-fg/75">Эти публикации пока не переведены на русский.</p>}
      {visible.map((lead) => <NewsRow key={lead.article_id} lead={lead} />)}
    </div>
    {remaining > 0 && <button type="button" aria-controls={listId} onClick={() => { revealFrom.current = visible.length; setExpanded(true); }}
      className="mt-2 inline-flex min-h-11 items-center gap-2 rounded-lg border border-line px-4 text-sm text-accent hover:border-accent/50 hover:bg-panel2/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
      Показать ещё {remaining} <ArrowDown aria-hidden="true" size={15} />
    </button>}
  </div>;
}
