"use client";

import { LEVEL_COLOR, LEVEL_RU } from "@/lib/format";
import type { Level } from "@/lib/types";

const LEVELS: Level[] = ["ally", "partner", "neutral", "cooling", "tension", "hostile"];

export interface FilterState {
  region: string | null;
  level: Level | null;
  topic: string | null;
}

export default function Filters({
  regions,
  topics,
  value,
  onChange,
}: {
  regions: Record<string, string>;
  topics: Record<string, string>;
  value: FilterState;
  onChange: (next: FilterState) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 px-1 py-2">
      <select
        value={value.region ?? ""}
        onChange={(e) => onChange({ ...value, region: e.target.value || null })}
        className="rounded-md border border-line bg-panel px-2 py-1 text-xs text-gray-300"
      >
        <option value="">Все регионы</option>
        {Object.entries(regions).map(([key, label]) => (
          <option key={key} value={key}>{label}</option>
        ))}
      </select>

      <div className="flex flex-wrap gap-1">
        {LEVELS.map((lvl) => (
          <button
            key={lvl}
            onClick={() => onChange({ ...value, level: value.level === lvl ? null : lvl })}
            className="rounded-full border px-2.5 py-0.5 text-[11px] transition-colors"
            style={{
              borderColor: value.level === lvl ? LEVEL_COLOR[lvl] : "#1f2937",
              color: value.level === lvl ? LEVEL_COLOR[lvl] : "#6b7280",
              background: value.level === lvl ? `${LEVEL_COLOR[lvl]}1a` : "transparent",
            }}
          >
            {LEVEL_RU[lvl]}
          </button>
        ))}
      </div>

      <select
        value={value.topic ?? ""}
        onChange={(e) => onChange({ ...value, topic: e.target.value || null })}
        className="rounded-md border border-line bg-panel px-2 py-1 text-xs text-gray-300"
        title="Тематическая линза: страны, обсуждающие тему"
      >
        <option value="">Линза: все темы</option>
        {Object.entries(topics).map(([key, label]) => (
          <option key={key} value={key}>{label}</option>
        ))}
      </select>

      {(value.region || value.level || value.topic) && (
        <button
          onClick={() => onChange({ region: null, level: null, topic: null })}
          className="text-[11px] text-dim underline hover:text-gray-300"
        >
          сбросить
        </button>
      )}
    </div>
  );
}
