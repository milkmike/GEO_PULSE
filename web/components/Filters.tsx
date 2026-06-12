"use client";

import { motion, useReducedMotion } from "motion/react";
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
  const reduce = useReducedMotion();
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
        {LEVELS.map((lvl) => {
          const active = value.level === lvl;
          return (
            <motion.button
              key={lvl}
              onClick={() => onChange({ ...value, level: active ? null : lvl })}
              className="relative rounded-full border px-2.5 py-0.5 text-[11px] transition-colors"
              style={{
                borderColor: active ? LEVEL_COLOR[lvl] : "#1f2937",
                color: active ? LEVEL_COLOR[lvl] : "#6b7280",
                background: active ? `${LEVEL_COLOR[lvl]}1a` : "transparent",
              }}
              whileTap={reduce ? undefined : { scale: 0.92 }}
              whileHover={reduce ? undefined : { scale: 1.04 }}
            >
              {active && (
                <motion.span
                  layoutId="levelChipActive"
                  className="absolute inset-0 -z-10 rounded-full"
                  style={{ background: "rgba(255,255,255,0.06)" }}
                  transition={{ type: "spring", stiffness: 500, damping: 35 }}
                />
              )}
              {LEVEL_RU[lvl]}
            </motion.button>
          );
        })}
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
        <motion.button
          onClick={() => onChange({ region: null, level: null, topic: null })}
          className="text-[11px] text-dim underline hover:text-gray-300"
          whileTap={reduce ? undefined : { scale: 0.95 }}
        >
          сбросить
        </motion.button>
      )}
    </div>
  );
}
