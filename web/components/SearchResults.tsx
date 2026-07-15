"use client";

import Link from "next/link";
import { ArrowUpRight, BookOpenText, ChevronDown } from "lucide-react";
import { Fragment, useState } from "react";
import type { SearchArticle, SearchScoreComponents } from "@/lib/types";

const SCORE_LABELS: [keyof SearchScoreComponents, string][] = [
  ["lexical", "текст"],
  ["entity", "сущность"],
  ["topic", "тема"],
  ["freshness", "свежесть"],
  ["trust", "источник"],
  ["story", "сюжет"],
  ["vector", "семантика"],
];

function safeArticleUrl(value: string | null): string | null {
  if (
    !value ||
    value.includes("\\") ||
    [...value].some((character) => /\s/.test(character) || character.charCodeAt(0) < 32)
  ) {
    return null;
  }
  try {
    const parsed = new URL(value);
    if (
      !["http:", "https:"].includes(parsed.protocol) ||
      !parsed.hostname ||
      parsed.username ||
      parsed.password
    ) {
      return null;
    }
    return parsed.href;
  } catch {
    return null;
  }
}

function countryName(code: string): string {
  if (!code) return "страна не указана";
  try {
    return new Intl.DisplayNames(["ru"], { type: "region" }).of(code) ?? code;
  } catch {
    return code;
  }
}

function articleDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "дата не указана";
  return date.toLocaleDateString("ru-RU", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

function HighlightedEvidence({ text }: { text: string }) {
  const pieces = text.split(/(«[^»]+»)/g).filter(Boolean);
  return (
    <span>
      {pieces.map((piece, index) => {
        const highlighted = piece.startsWith("«") && piece.endsWith("»");
        return highlighted ? (
          <mark
            key={`${piece}-${index}`}
            className="rounded-sm bg-ru-blue/20 px-0.5 text-ru-white"
          >
            {piece.slice(1, -1)}
          </mark>
        ) : (
          <Fragment key={`${piece}-${index}`}>{piece}</Fragment>
        );
      })}
    </span>
  );
}

function ResultCard({ item, index }: { item: SearchArticle; index: number }) {
  const [explanationOpen, setExplanationOpen] = useState(false);
  const sourceUrl = safeArticleUrl(item.url);
  const textEvidence = item.evidence.find(
    (evidence) => evidence.type === "text_span" && evidence.text,
  )?.text;
  const evidenceText = textEvidence || item.summary || "Текстовый фрагмент недоступен.";
  const explanationId = `search-explanation-${item.article_id}`;

  return (
    <article className="group relative border-b border-line py-7 last:border-b-0 sm:grid sm:grid-cols-[3.5rem_minmax(0,1fr)_10.5rem] sm:gap-5">
      <div className="tnum mb-3 text-[11px] tracking-[0.18em] text-dim sm:mb-0">
        {String(index + 1).padStart(2, "0")}
        <span className="mt-2 hidden h-px w-8 bg-ru-red/70 sm:block" aria-hidden="true" />
      </div>

      <div className="min-w-0">
        <h2 className="display text-[22px] leading-[1.18] sm:text-[25px]">
          {item.title || "Без заголовка"}
        </h2>
        <p className="tnum mt-2 text-[10px] uppercase tracking-[0.09em] text-dim">
          {item.source.name || "источник не указан"} · {countryName(item.source.country)} ·{" "}
          {articleDate(item.published_at)}
          {item.language ? ` · ${item.language}` : ""}
        </p>

        <blockquote className="mt-5 border-l-2 border-ru-blue/60 pl-4 text-[14px] leading-6 text-[#b9c0ca]">
          <span className="card-title mb-1 block !text-[9px]">фрагмент совпадения</span>
          <HighlightedEvidence text={evidenceText} />
        </blockquote>

        {(item.matched_entities.length > 0 || item.topics.length > 0) && (
          <div className="mt-4 flex flex-wrap gap-1.5" aria-label="Метки материала">
            {item.matched_entities.map((entity) => (
              <span
                key={entity.id}
                className="rounded-full border border-ru-blue/25 bg-ru-blue/8 px-2 py-0.5 text-[10px] text-[#a9c4f1]"
              >
                {entity.name || entity.mention_text || "сущность"}
              </span>
            ))}
            {item.topics.map((topic) => (
              <span
                key={topic}
                className="rounded-full border border-line px-2 py-0.5 text-[10px] text-dim"
              >
                {topic}
              </span>
            ))}
          </div>
        )}

        <div className="mt-5 flex flex-wrap items-center gap-x-4 gap-y-2">
          {sourceUrl ? (
            <a
              href={sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex min-h-11 items-center gap-1 text-[12px] font-medium text-accent underline decoration-accent/30 underline-offset-4 transition-colors hover:text-ru-white focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent sm:min-h-0"
            >
              Открыть источник <ArrowUpRight aria-hidden="true" size={13} />
            </a>
          ) : (
            <span className="text-[11px] text-dim">Ссылка на источник недоступна</span>
          )}
          {item.story && (
            <Link
              href={`/stories/${item.story.id}`}
              className="inline-flex min-h-11 items-center gap-1 text-[12px] text-[#c4c9d1] transition-colors hover:text-ru-white focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent sm:min-h-0"
            >
              <BookOpenText aria-hidden="true" size={13} />
              {item.story.title || `Сюжет № ${item.story.id}`}
            </Link>
          )}
        </div>

        <button
          type="button"
          aria-expanded={explanationOpen}
          aria-controls={explanationId}
          onClick={() => setExplanationOpen((open) => !open)}
          className="mt-5 inline-flex min-h-11 items-center gap-1.5 text-[11px] uppercase tracking-[0.08em] text-dim transition-colors hover:text-ru-white focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent sm:min-h-0"
        >
          Почему найдено
          <ChevronDown
            aria-hidden="true"
            size={13}
            className={`transition-transform motion-reduce:transition-none ${
              explanationOpen ? "rotate-180" : ""
            }`}
          />
        </button>
        {explanationOpen && (
          <div
            id={explanationId}
            className="mt-3 border-t border-line pt-3 text-[12px] leading-5 text-[#b9c0ca]"
          >
            <p>{item.why_included}</p>
            <p className="tnum mt-1 text-[10px] uppercase tracking-[0.08em] text-dim">
              Релевантность {Math.round(item.relevance_score * 100)}% · достоверность{" "}
              {Math.round(item.confidence * 100)}%
            </p>
          </div>
        )}
      </div>

      <aside className="mt-5 border-t border-line pt-4 sm:mt-0 sm:border-l sm:border-t-0 sm:pl-5 sm:pt-0">
        <p className="card-title !text-[9px]">состав совпадения</p>
        <div className="mt-3 space-y-2.5">
          {SCORE_LABELS.map(([key, label]) => {
            const value = item.scores[key];
            if (value == null) return null;
            return (
              <div key={key}>
                <div className="tnum flex justify-between text-[9px] uppercase tracking-[0.05em] text-dim">
                  <span>{label}</span>
                  <span>{Math.round(value * 100)}</span>
                </div>
                <div className="mt-1 h-px overflow-hidden bg-line" aria-hidden="true">
                  <span
                    className="block h-full bg-ru-white/60"
                    style={{ width: `${Math.max(0, Math.min(value, 1)) * 100}%` }}
                  />
                </div>
              </div>
            );
          })}
        </div>
        {(item.action_level != null || item.sentiment != null) && (
          <p className="tnum mt-5 text-[9px] uppercase leading-5 tracking-[0.06em] text-dim">
            {item.action_level != null ? `действие ${item.action_level}` : ""}
            {item.action_level != null && item.sentiment != null ? " · " : ""}
            {item.sentiment != null ? `тон ${item.sentiment > 0 ? "+" : ""}${item.sentiment.toFixed(1)}` : ""}
          </p>
        )}
      </aside>
    </article>
  );
}

export default function SearchResults({ items }: { items: SearchArticle[] }) {
  return (
    <section aria-label="Результаты поиска" className="border-t border-line">
      {items.map((item, index) => (
        <ResultCard key={item.article_id} item={item} index={index} />
      ))}
    </section>
  );
}
