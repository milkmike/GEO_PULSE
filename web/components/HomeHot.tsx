"use client";

import Link from "next/link";
import { useId, useState } from "react";
import Markdown from "./Markdown";
import { fmt, fmtDate, LEVEL_COLOR } from "@/lib/format";
import type { Brief, CountrySummary, Signal } from "@/lib/types";

const BRIEF_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000;
const SIGNAL_WINDOW_MS = 72 * 60 * 60 * 1000;
const MOVERS_PER_SIDE = 4;
/** Fewest own articles (14-day index window) behind a media reading worth calling a move. */
const MIN_MOVER_ARTICLES = 3;
const SIGNALS_SHOWN = 6;
const SEVERITY_RANK: Record<Signal["severity"], number> = { critical: 0, warning: 1, info: 2 };
const SEVERITY_DOT: Record<Signal["severity"], string> = {
  critical: "bg-hostile",
  warning: "bg-cooling",
  info: "bg-dim",
};

/** The «Главное» section of the world brief, or its first section when headings differ. */
export function briefHighlights(content: string): string {
  const sections = content.split(/^(?=## )/m).filter((part) => part.trim());
  const main = sections.find((part) => /^## Главное/i.test(part));
  return (main ?? sections[0] ?? content).trim();
}

/** Newest copy of each repeated signal, most severe first. */
export function hotSignals(signals: Signal[], now: number): Signal[] {
  const seen = new Set<string>();
  const unique: Signal[] = [];
  const newestFirst = [...signals].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  for (const signal of newestFirst) {
    const created = Date.parse(signal.created_at);
    if (!signal.active || !Number.isFinite(created) || now - created > SIGNAL_WINDOW_MS) continue;
    const key = `${signal.country_code ?? ""}|${signal.type}|${signal.title}`;
    if (seen.has(key)) continue;
    seen.add(key);
    unique.push(signal);
  }
  return unique
    .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]
      || Date.parse(b.created_at) - Date.parse(a.created_at))
    .slice(0, SIGNALS_SHOWN);
}

/**
 * True when the media layer of the index rests on too little material for its
 * weekly move to mean anything: one or two own articles, or a GDELT reading
 * with no measured volume. Countries without any media layer are not thin —
 * their moves come from the structural layer.
 */
export function thinMediaEvidence(country: CountrySummary): boolean {
  if (country.article_count != null) return country.article_count < MIN_MOVER_ARTICLES;
  return country.media != null && !country.gdelt_volume;
}

export function weeklyMovers(countries: CountrySummary[]) {
  const moved = countries.filter((c) => c.delta_7d != null && Math.abs(c.delta_7d) >= 1 && !thinMediaEvidence(c));
  return {
    cooled: moved.filter((c) => (c.delta_7d ?? 0) < 0)
      .sort((a, b) => (a.delta_7d ?? 0) - (b.delta_7d ?? 0)).slice(0, MOVERS_PER_SIDE),
    warmed: moved.filter((c) => (c.delta_7d ?? 0) > 0)
      .sort((a, b) => (b.delta_7d ?? 0) - (a.delta_7d ?? 0)).slice(0, MOVERS_PER_SIDE),
  };
}

function CardHeader({ id, title, href, more }: { id: string; title: string; href?: string; more?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3">
      <h3 id={id} className="text-[15px] font-semibold text-ru-white">{title}</h3>
      {href && more && (
        <Link href={href} className="shrink-0 text-xs text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent">
          {more} <span aria-hidden="true">→</span>
        </Link>
      )}
    </div>
  );
}

function BriefCard({ brief, now }: { brief: Brief; now: number }) {
  const titleId = useId();
  const [full, setFull] = useState(false);
  const highlights = briefHighlights(brief.content);
  const hasMore = highlights.length < brief.content.trim().length;
  return (
    <section aria-labelledby={titleId} className="card flex min-w-0 flex-col gap-3 p-4">
      <CardHeader id={titleId} title="Главное за сутки" />
      <div className="text-sm leading-6 text-fg">
        <Markdown text={full ? brief.content : highlights.replace(/^## [^\n]*\n?/, "")} citations={brief.citations ?? brief.meta?.citations} />
      </div>
      <div className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2 text-xs text-dim">
        <span title={brief.model ?? undefined}>
          Написано ИИ · {fmtDate(brief.created_at)}
          {now - Date.parse(brief.created_at) > 24 * 60 * 60 * 1000 && " · сводка старше суток"}
        </span>
        {hasMore && (
          <button type="button" onClick={() => setFull((value) => !value)} className="text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent">
            {full ? "Свернуть" : "Вся сводка"}
          </button>
        )}
      </div>
    </section>
  );
}

function MoverRow({ country }: { country: CountrySummary }) {
  const delta = country.delta_7d ?? 0;
  return (
    <li>
      <Link href={`/country/${country.code}`} className="flex min-h-9 items-center gap-2 rounded-md px-1.5 text-sm hover:bg-panel2 focus-visible:outline-2 focus-visible:outline-accent">
        <span aria-hidden="true" className="w-5 shrink-0 text-center">{country.flag}</span>
        <span className="min-w-0 flex-1 truncate text-fg">{country.name}</span>
        <span className="tnum w-11 shrink-0 text-right text-xs" style={{ color: LEVEL_COLOR[country.level] }}>{fmt(country.score)}</span>
        <span className={`tnum w-16 shrink-0 whitespace-nowrap text-right text-sm font-semibold ${delta > 0 ? "text-partner" : "text-hostile"}`}>
          <span aria-hidden="true">{delta > 0 ? "▲ " : "▼ "}</span>{fmt(delta)}
        </span>
      </Link>
    </li>
  );
}

function MoversCard({ cooled, warmed }: ReturnType<typeof weeklyMovers>) {
  const titleId = useId();
  if (!cooled.length && !warmed.length) return null;
  return (
    <section aria-labelledby={titleId} className="card flex min-w-0 flex-col gap-2 p-4">
      <CardHeader id={titleId} title="Сильнее всего за неделю" />
      <p className="text-xs text-dim">
        Изменение индекса за 7 дней; справа от названия текущий индекс.
        Страны, где о России почти не пишут (меньше {MIN_MOVER_ARTICLES} статей за две недели), не показываем.
      </p>
      {cooled.length > 0 && <>
        <h4 className="mt-1 text-xs font-medium text-dim">Похолодели</h4>
        <ul>{cooled.map((c) => <MoverRow key={c.code} country={c} />)}</ul>
      </>}
      {warmed.length > 0 && <>
        <h4 className="mt-1 text-xs font-medium text-dim">Потеплели</h4>
        <ul>{warmed.map((c) => <MoverRow key={c.code} country={c} />)}</ul>
      </>}
    </section>
  );
}

function SignalsCard({ signals, detailEnabled }: { signals: Signal[]; detailEnabled: boolean }) {
  const titleId = useId();
  if (!signals.length) return null;
  return (
    <section aria-labelledby={titleId} className="card flex min-w-0 flex-col gap-2 p-4">
      <CardHeader id={titleId} title="Свежие сигналы" href="/signals" more="все" />
      <p className="text-xs text-dim">Резкие изменения за трое суток, повторы убраны.</p>
      <ul className="divide-y divide-line">
        {signals.map((signal) => {
          const href = detailEnabled ? `/signals/${signal.id}`
            : signal.country_code ? `/country/${signal.country_code}` : "/signals";
          return (
            <li key={signal.id}>
              <Link href={href} className="flex items-start gap-2.5 rounded-md px-1.5 py-2 text-sm hover:bg-panel2 focus-visible:outline-2 focus-visible:outline-accent">
                <span aria-hidden="true" className={`mt-[7px] h-2 w-2 shrink-0 rounded-full ${SEVERITY_DOT[signal.severity]}`} />
                <span className="min-w-0 flex-1 leading-snug text-fg">{signal.title}</span>
                <time dateTime={signal.created_at} className="tnum shrink-0 text-xs text-dim">{fmtDate(signal.created_at)}</time>
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/** «Горячее»: what deserves attention first — the day's brief, weekly movers, fresh signals. */
export default function HomeHot({
  brief,
  countries,
  signals,
  signalDetail = false,
  now = Date.now(),
}: {
  brief: Brief | null;
  countries: CountrySummary[];
  signals: Signal[];
  signalDetail?: boolean;
  now?: number;
}) {
  const titleId = useId();
  const freshBrief = brief && now - Date.parse(brief.created_at) <= BRIEF_MAX_AGE_MS ? brief : null;
  const fresh = hotSignals(signals, now);
  const movers = weeklyMovers(countries);
  const hasMovers = movers.cooled.length > 0 || movers.warmed.length > 0;
  if (!freshBrief && !fresh.length && !hasMovers) return null;
  return (
    <section aria-labelledby={titleId} className="mt-10">
      <div className="mb-3">
        <h2 id={titleId} className="display text-[26px] leading-tight">Горячее</h2>
        <p className="mt-1 text-sm text-dim">На что смотреть в первую очередь.</p>
      </div>
      <div className="grid items-start gap-3 lg:grid-cols-3">
        {freshBrief && <BriefCard brief={freshBrief} now={now} />}
        <MoversCard cooled={movers.cooled} warmed={movers.warmed} />
        <SignalsCard signals={fresh} detailEnabled={signalDetail} />
      </div>
    </section>
  );
}
