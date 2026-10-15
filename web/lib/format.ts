import type { Level } from "./types";
import { safeHttpUrl as validateHttpUrl } from "./urls";

export const LEVEL_RU: Record<Level, string> = {
  ally: "союзник",
  partner: "партнёр",
  neutral: "нейтралитет",
  cooling: "охлаждение",
  tension: "напряжение",
  hostile: "враждебность",
};

export const levelRu = (code: string): string => LEVEL_RU[code as Level] ?? code;

const LEVEL_TOKEN = /([[«"(])(ally|partner|neutral|cooling|tension|hostile)([\]»")])/g;

/** Replaces raw RRI level codes left in stored copy ("[partner]", «cooling») with Russian labels. */
export function localizeLevelTokens(text: string): string {
  return text.replace(LEVEL_TOKEN, (_match, open: string, level: Level, close: string) =>
    open === "[" ? `(${LEVEL_RU[level]})` : `${open}${LEVEL_RU[level]}${close}`);
}

export const SEVERITY_RU: Record<string, string> = {
  info: "наблюдение",
  warning: "внимание",
  critical: "критический",
};

export const severityRu = (code: string): string => SEVERITY_RU[code] ?? code;

/** Russian plural form: pluralRu(4, "публикация", "публикации", "публикаций") → "публикации". */
export function pluralRu(n: number, one: string, few: string, many: string): string {
  const mod10 = Math.abs(n) % 10;
  const mod100 = Math.abs(n) % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

const REGION_NAMES = (() => {
  try { return new Intl.DisplayNames(["ru"], { type: "region" }); }
  catch { return null; }
})();

/** Country name for an ISO alpha-2 code; falls back to the code itself. */
export function countryNameRu(code: string): string {
  const normalized = code.trim().toUpperCase();
  if (!/^[A-Z]{2}$/.test(normalized)) return code;
  try { return REGION_NAMES?.of(normalized) ?? code; }
  catch { return code; }
}

const LEGACY_STORY_SUMMARY = /^Сюжет объединяет (\d+) публикаций из стран ([A-Z]{2}(?:, [A-Z]{2})*) по событию «([\s\S]+)»\.$/;

/** Rewrites the legacy template summary (codes, broken plural) still stored for older stories. */
export function storySummaryRu(summary: string): string {
  const match = LEGACY_STORY_SUMMARY.exec(summary.trim());
  if (!match) return summary;
  const count = Number(match[1]);
  const countries = match[2].split(", ").map(countryNameRu).join(", ");
  return `Сюжет объединяет ${count} ${pluralRu(count, "публикацию", "публикации", "публикаций")} о событии «${match[3]}». Страны: ${countries}.`;
}

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
  notable_event: "значимое событие",
  sanctions_escalation: "санкционное ужесточение",
};

export const EVENT_TYPE_RU: Record<string, string> = {
  military: "военное",
  diplomatic: "дипломатическое",
  security: "безопасность",
  economic: "экономическое",
  cultural: "культурное",
};

export const SOURCE_TIER_RU: Record<string, string> = {
  official: "официальные источники",
  mainstream: "крупные СМИ",
  independent: "независимые СМИ",
  social: "социальные сети",
  domestic_opposition: "внутренняя оппозиция",
  western_proxy: "западные прокси-источники",
  analytics: "аналитика",
};

export const eventTypeRu = (code: string): string =>
  EVENT_TYPE_RU[code] ?? `неизвестный тип события (код: ${code})`;

export const sourceTierRu = (code: string): string =>
  SOURCE_TIER_RU[code] ?? `неизвестный тип источника (код: ${code})`;

export const fmt = (v: number | null | undefined, digits = 1): string =>
  v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(digits)}`;

export const fmtDate = (iso: string): string =>
  new Date(iso).toLocaleString("ru-RU", {
    day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
  });

export const fmtDay = (iso: string): string =>
  new Date(iso).toLocaleDateString("ru-RU", { day: "numeric", month: "short" });

/** Returns a backend-compatible, absolute public HTTP(S) URL, otherwise null.
 *  Use for third-party URLs (RSS headlines, citations) before putting them in href. */
export function safeHttpUrl(u: string): string | null {
  return validateHttpUrl(u);
}

/** Plotly colorscale for scores −100..+100 (red → gray → green). */
export const SCORE_COLORSCALE: [number, string][] = [
  [0, "#7f1d1d"], [0.15, "#ef4444"], [0.3, "#f97316"],
  [0.425, "#4b5563"], [0.5, "#6b7280"], [0.575, "#4b5563"],
  [0.7, "#34d399"], [0.85, "#10b981"], [1, "#047857"],
];
