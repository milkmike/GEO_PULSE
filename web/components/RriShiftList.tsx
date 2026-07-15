"use client";

import type { RriShiftMarker } from "@/lib/rri-shifts";

interface RriShiftListProps {
  markers: RriShiftMarker[];
  onSelect: (marker: RriShiftMarker, trigger: HTMLButtonElement) => void;
  compact?: boolean;
}

const fmt = (value: number) =>
  `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toLocaleString("ru-RU", {
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  })}`;

const fmtDay = (day: string) => new Date(`${day}T12:00:00Z`).toLocaleDateString("ru-RU", {
  day: "numeric",
  month: "short",
});

export default function RriShiftList({ markers, onSelect, compact = false }: RriShiftListProps) {
  if (!markers.length) {
    return <p className={`${compact ? "px-2" : "px-4"} pb-3 text-xs text-dim`}>Заметных сдвигов в выбранном периоде нет.</p>;
  }
  return (
    <ul
      aria-label="Заметные сдвиги RRI"
      className={`flex flex-wrap gap-2 ${compact ? "px-2 pb-2" : "px-4 pb-4"}`}
    >
      {markers.map((marker) => {
        const label = `${fmtDay(marker.day)}: ${fmt(marker.fromScore)} → ${fmt(marker.score)}, изменение между дневными точками RRI ${fmt(marker.dailyDelta)}`;
        return (
          <li key={marker.at}>
            <button
              type="button"
              aria-label={label}
              title={label}
              onClick={(event) => onSelect(marker, event.currentTarget)}
              className="min-h-11 rounded-md border border-line bg-panel2 px-3 py-2 text-left text-[11px] leading-tight text-dim transition-colors hover:border-ru-blue hover:text-ru-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <span className="block font-semibold text-ru-white">{fmtDay(marker.day)} · {fmt(marker.dailyDelta)}</span>
              <span>{fmt(marker.fromScore)} → {fmt(marker.score)}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
