import type { Level } from "./types";

export const LEVEL_RU: Record<Level, string> = {
  ally: "союзник",
  partner: "партнёр",
  neutral: "нейтралитет",
  cooling: "охлаждение",
  tension: "напряжение",
  hostile: "враждебность",
};

export const LEVEL_COLOR: Record<Level, string> = {
  ally: "#10b981",
  partner: "#34d399",
  neutral: "#9ca3af",
  cooling: "#fbbf24",
  tension: "#f97316",
  hostile: "#ef4444",
};

export const SIGNAL_RU: Record<string, string> = {
  tier_convergence: "конвергенция тиров",
  official_silence: "официальные молчат",
  velocity_spike: "информационный шторм",
  tone_shift: "сдвиг тона",
  volume_surge: "всплеск внимания",
  index_shift: "скачок индекса",
  fx_move: "валютный сдвиг",
};

export const fmt = (v: number | null | undefined, digits = 1): string =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}`;

export const fmtDate = (iso: string): string =>
  new Date(iso).toLocaleString("ru-RU", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });

export const fmtDay = (iso: string): string =>
  new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });

/** Plotly colorscale for scores −100..+100 (red → gray → green). */
export const SCORE_COLORSCALE: [number, string][] = [
  [0, "#7f1d1d"], [0.15, "#ef4444"], [0.3, "#f97316"],
  [0.425, "#4b5563"], [0.5, "#6b7280"], [0.575, "#4b5563"],
  [0.7, "#34d399"], [0.85, "#10b981"], [1, "#047857"],
];
