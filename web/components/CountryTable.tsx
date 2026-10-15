"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { fmt, LEVEL_COLOR, LEVEL_RU, pluralRu } from "@/lib/format";
import type { CountrySummary } from "@/lib/types";

/** Rows shown before the "show all" button. */
const PAGE_SIZE = 25;

const SORTS = {
  score: "По индексу",
  cooling: "Сильнее похолодели за неделю",
  warming: "Сильнее потеплели за неделю",
  name: "По алфавиту",
} as const;

type SortKey = keyof typeof SORTS;

const IDS = {
  search: "country-table-search",
  region: "country-table-region",
  sort: "country-table-sort",
  table: "country-table",
  more: "country-table-more",
} as const;

const FOCUS =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent";
// text-base on phones keeps iOS from zooming into focused form fields
const FIELD =
  `min-h-10 rounded-md border border-line bg-panel px-3 text-base text-fg sm:text-sm ${FOCUS}`;

/** Case- and ё-insensitive form for name search. */
const normalize = (s: string) => s.toLocaleLowerCase("ru").replace(/ё/g, "е").trim();

const byName = (a: CountrySummary, b: CountrySummary) => a.name.localeCompare(b.name, "ru");

/** Weekly delta order; countries without a weekly delta always go last. */
const byWeek = (dir: 1 | -1) => (a: CountrySummary, b: CountrySummary) => {
  const x = a.delta_7d;
  const y = b.delta_7d;
  if (x == null || y == null) return (x == null ? 1 : 0) - (y == null ? 1 : 0) || byName(a, b);
  return dir * (x - y) || byName(a, b);
};

const COMPARE: Record<SortKey, (a: CountrySummary, b: CountrySummary) => number> = {
  score: (a, b) => b.score - a.score || byName(a, b),
  cooling: byWeek(1),
  warming: byWeek(-1),
  name: byName,
};

function Delta({ value }: { value: number | null }) {
  if (value == null) {
    return (
      <span className="text-dim">
        <span aria-hidden="true">—</span>
        <span className="sr-only">нет данных</span>
      </span>
    );
  }
  const tone = value > 0 ? "text-partner" : value < 0 ? "text-hostile" : "text-dim";
  return <span className={tone}>{fmt(value)}</span>;
}

export default function CountryTable({
  countries,
  regions,
}: {
  /** all tracked countries, any order */
  countries: CountrySummary[];
  /** region code → Russian name (api.meta); the region select is hidden without it */
  regions?: Record<string, string>;
}) {
  const [query, setQuery] = useState("");
  const [region, setRegion] = useState("");
  const [sort, setSort] = useState<SortKey>("score");
  const [expanded, setExpanded] = useState(false);

  const regionEntries = Object.entries(regions ?? {});

  const rows = useMemo(() => {
    const needle = normalize(query);
    return countries
      .filter((c) => !region || c.region === region)
      .filter((c) => !needle || normalize(c.name).includes(needle))
      .sort(COMPARE[sort]);
  }, [countries, query, region, sort]);

  const total = rows.length;
  const visible = expanded ? rows : rows.slice(0, PAGE_SIZE);
  const hasMore = !expanded && total > PAGE_SIZE;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-2">
        <label htmlFor={IDS.search} className="sr-only">Найти страну</label>
        <input
          id={IDS.search}
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Найти страну"
          autoComplete="off"
          className={`${FIELD} w-full placeholder:text-dim sm:w-auto sm:min-w-56 sm:flex-1`}
        />

        {regionEntries.length > 0 && (
          <>
            <label htmlFor={IDS.region} className="sr-only">Регион</label>
            <select
              id={IDS.region}
              value={region}
              onChange={(e) => setRegion(e.target.value)}
              className={`${FIELD} min-w-0 flex-1 sm:flex-none`}
            >
              <option value="">Все регионы</option>
              {regionEntries.map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </select>
          </>
        )}

        <label htmlFor={IDS.sort} className="sr-only">Сортировка</label>
        <select
          id={IDS.sort}
          value={sort}
          onChange={(e) => setSort(e.target.value as SortKey)}
          className={`${FIELD} min-w-0 flex-1 sm:flex-none`}
        >
          {(Object.keys(SORTS) as SortKey[]).map((key) => (
            <option key={key} value={key}>{SORTS[key]}</option>
          ))}
        </select>
      </div>

      <p className="text-xs text-dim" aria-live="polite">
        {total} {pluralRu(total, "страна", "страны", "стран")}
      </p>

      <div className="relative overflow-x-auto">
        {total === 0 ? (
          <p className="py-6 text-center text-sm text-dim">Ничего не найдено</p>
        ) : (
          <table id={IDS.table} aria-label="Все страны" className="w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-line text-xs text-dim">
                <th scope="col" className="px-2 py-2 text-left font-normal">Страна</th>
                <th scope="col" className="px-2 py-2 text-left font-normal">Уровень</th>
                <th scope="col" className="px-2 py-2 text-right font-normal">Индекс</th>
                <th scope="col" className="hidden px-2 py-2 text-right font-normal sm:table-cell">За сутки</th>
                <th scope="col" className="px-2 py-2 text-right font-normal">За неделю</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {visible.map((c) => {
                const color = LEVEL_COLOR[c.level];
                return (
                  <tr key={c.code} className="transition-colors hover:bg-panel2">
                    <th scope="row" className="px-2 text-left font-normal">
                      <Link
                        href={`/country/${c.code}`}
                        className={`flex min-h-10 items-center gap-2 rounded-sm py-1 text-fg hover:text-ru-white ${FOCUS}`}
                      >
                        <span aria-hidden="true" className="w-6 flex-none text-center text-base">{c.flag}</span>
                        <span className="leading-tight">{c.name}</span>
                      </Link>
                    </th>
                    <td className="px-2 py-1">
                      <span
                        className="inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-xs"
                        style={{ color, borderColor: color, background: `${color}14` }}
                      >
                        {LEVEL_RU[c.level]}
                      </span>
                    </td>
                    <td className="tnum px-2 py-1 text-right font-semibold" style={{ color }}>
                      {fmt(c.score)}
                    </td>
                    <td className="tnum hidden px-2 py-1 text-right sm:table-cell">
                      <Delta value={c.delta_24h} />
                    </td>
                    <td className="tnum px-2 py-1 text-right">
                      <Delta value={c.delta_7d} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {hasMore && (
        <button
          id={IDS.more}
          type="button"
          aria-controls={IDS.table}
          onClick={() => setExpanded(true)}
          className={`min-h-10 w-full rounded-md border border-line bg-panel px-3 text-sm text-fg transition-colors hover:border-ru-blue hover:text-ru-white ${FOCUS}`}
        >
          Показать все {total} {pluralRu(total, "страну", "страны", "стран")}
        </button>
      )}
    </div>
  );
}
