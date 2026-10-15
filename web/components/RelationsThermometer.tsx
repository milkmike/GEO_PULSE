"use client";

import Link from "next/link";
import { useId } from "react";
import { fmt, LEVEL_COLOR, pluralRu } from "@/lib/format";
import type { CountrySummary, Level } from "@/lib/types";

/** RRI levels from warmest to coldest; ranges mirror LEVELS in src/engine/ru_index.py. */
export const THERMOMETER_BANDS: { level: Level; label: string; range: string }[] = [
  { level: "ally", label: "Союзники", range: "от +45" },
  { level: "partner", label: "Партнёры", range: "+15…+45" },
  { level: "neutral", label: "Нейтралитет", range: "−15…+15" },
  { level: "cooling", label: "Охлаждение", range: "−40…−15" },
  { level: "tension", label: "Напряжённость", range: "−70…−40" },
  { level: "hostile", label: "Враждебность", range: "ниже −70" },
];

function WeeklyDelta({ value }: { value: number | null }) {
  if (value == null || Math.abs(value) < 1) return null;
  const up = value > 0;
  return (
    <span
      className={`tnum shrink-0 text-xs ${up ? "text-partner" : "text-hostile"}`}
      title={`За неделю: ${fmt(value)}`}
    >
      <span aria-hidden="true">{up ? "▲" : "▼"}</span>
      <span className="sr-only">за неделю </span>
      {fmt(value)}
    </span>
  );
}

/** Vertical «thermometer»: every country placed in its RRI level band, warmest on top. */
export default function RelationsThermometer({
  countries,
  highlighted,
  onHighlight,
}: {
  countries: CountrySummary[];
  highlighted?: string | null;
  onHighlight?: (code: string | null) => void;
}) {
  const titleId = useId();
  const bands = THERMOMETER_BANDS.map((band) => ({
    ...band,
    countries: countries.filter((c) => c.level === band.level).sort((a, b) => b.score - a.score),
  })).filter((band) => band.countries.length > 0);

  return (
    <section aria-labelledby={titleId} className="card flex min-h-0 flex-col">
      <header className="border-b border-line px-4 pb-3 pt-3">
        <h2 id={titleId} className="text-[15px] font-semibold text-ru-white">Термометр отношений</h2>
        <p className="mt-0.5 text-xs text-dim">
          {countries.length > 0
            ? `${countries.length} ${pluralRu(countries.length, "страна", "страны", "стран")} по индексу: сверху самые дружественные`
            : "Загружаем индекс стран…"}
        </p>
      </header>
      {/* `relative` keeps the rows' sr-only labels inside the scroll area. */}
      <div className="relative min-h-0 flex-1 overflow-y-auto px-3 py-2 max-lg:max-h-[480px]">
        {bands.map((band, index) => (
          <div key={band.level} className="flex gap-3">
            <span
              aria-hidden="true"
              className={`w-1.5 shrink-0 ${index === 0 ? "rounded-t-full" : ""} ${index === bands.length - 1 ? "rounded-b-full" : ""}`}
              style={{ background: LEVEL_COLOR[band.level] }}
            />
            <div className="min-w-0 flex-1 py-2">
              <h3 className="flex items-baseline justify-between gap-2 px-1.5 text-xs">
                <span className="font-semibold" style={{ color: LEVEL_COLOR[band.level] }}>
                  {band.label} · {band.countries.length}
                </span>
                <span className="tnum text-dim">{band.range}</span>
              </h3>
              <ul className="mt-1">
                {band.countries.map((c) => (
                  <li key={c.code}>
                    <Link
                      href={`/country/${c.code}`}
                      onMouseEnter={() => onHighlight?.(c.code)}
                      onMouseLeave={() => onHighlight?.(null)}
                      onFocus={() => onHighlight?.(c.code)}
                      onBlur={() => onHighlight?.(null)}
                      className={`flex min-h-9 items-center gap-2 rounded-md px-1.5 text-sm transition-colors hover:bg-panel2 focus-visible:outline-2 focus-visible:outline-accent ${
                        highlighted === c.code ? "bg-panel2" : ""
                      }`}
                    >
                      <span aria-hidden="true" className="w-5 shrink-0 text-center">{c.flag}</span>
                      <span className="min-w-0 flex-1 truncate text-fg">{c.name}</span>
                      <WeeklyDelta value={c.delta_7d} />
                      <span className="tnum w-12 shrink-0 text-right font-semibold" style={{ color: LEVEL_COLOR[c.level] }}>
                        {fmt(c.score)}
                      </span>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
