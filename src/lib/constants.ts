// ── GeoPulse Constants ─────────────────────────────────
// Single source of truth. Import from here, not from api.ts or local consts.

// ── Countries ──────────────────────────────────────────

export const COUNTRY_CODES = ["KZ", "AM", "UZ", "KG", "TJ", "TM", "AZ", "GE", "MD", "BY"] as const;
export type CountryCode = (typeof COUNTRY_CODES)[number];

export const COUNTRY_FLAGS: Record<string, string> = {
  KZ: "🇰🇿", AM: "🇦🇲", UZ: "🇺🇿", KG: "🇰🇬", TJ: "🇹🇯",
  TM: "🇹🇲", AZ: "🇦🇿", GE: "🇬🇪", MD: "🇲🇩", BY: "🇧🇾",
};

export const COUNTRY_NAMES: Record<string, string> = {
  KZ: "Казахстан", AM: "Армения", UZ: "Узбекистан", KG: "Кыргызстан",
  TJ: "Таджикистан", TM: "Туркменистан", AZ: "Азербайджан",
  GE: "Грузия", MD: "Молдова", BY: "Беларусь",
};

// ── Tiers (source classification) ──────────────────────

export const TIER_LABELS: Record<string, string> = {
  official: "Официальные",
  mainstream: "Мейнстрим",
  independent: "Независимые",
  domestic_opposition: "Оппозиция",
  analytics: "Аналитика",
  western_proxy: "Западные прокси",
  social: "Соцсети",
  state: "Государственные",
  opposition: "Оппозиция",
};

export const TIER_LABELS_SHORT: Record<string, string> = {
  official: "Офиц.",
  mainstream: "Мейнстрим",
  analytics: "Аналитика",
  independent: "Независ.",
  domestic_opposition: "Оппозиция",
  western_proxy: "Западные",
  social: "Соцсети",
  state: "Гос.",
  opposition: "Оппоз.",
};

/** Hex colors for charts (Recharts, Plotly) */
export const TIER_CHART_COLORS: Record<string, string> = {
  official: "#3b82f6",
  mainstream: "#a78bfa",
  analytics: "#22d3ee",
  independent: "#34d399",
  domestic_opposition: "#f97316",
  western_proxy: "#ef4444",
  social: "#fbbf24",
  state: "#3b82f6",
  opposition: "#f97316",
};

/** Tailwind classes for badges/pills */
export const TIER_BADGE_CLASSES: Record<string, string> = {
  official: "bg-red-500/20 text-red-400 border-red-500/30",
  mainstream: "bg-blue-500/20 text-blue-400 border-blue-500/30",
  independent: "bg-green-500/20 text-green-400 border-green-500/30",
  domestic_opposition: "bg-yellow-500/20 text-yellow-400 border-yellow-500/30",
  analytics: "bg-purple-500/20 text-purple-400 border-purple-500/30",
  western_proxy: "bg-gray-500/20 text-gray-400 border-gray-500/30",
  social: "bg-amber-500/20 text-amber-400 border-amber-500/30",
  state: "bg-red-500/20 text-red-400 border-red-500/30",
  opposition: "bg-orange-500/20 text-orange-400 border-orange-500/30",
};

// ── Thread / Narrative phases ──────────────────────────

export const PHASE_CONFIG: Record<string, { emoji: string; color: string; label: string }> = {
  emerging:   { emoji: "🌱", color: "#3b82f6", label: "Зарождение" },
  escalating: { emoji: "📈", color: "#f59e0b", label: "Эскалация" },
  peak:       { emoji: "🔥", color: "#ef4444", label: "Пик" },
  cooling:    { emoji: "❄️", color: "#06b6d4", label: "Затухание" },
  resolved:   { emoji: "✅", color: "#22c55e", label: "Завершён" },
};

export const PHASE_ORDER = ["emerging", "escalating", "peak", "cooling", "resolved"] as const;

// ── Sentiment helpers ──────────────────────────────────

export function sentimentColor(s: number): string {
  if (s >= 0.3) return "#22c55e";
  if (s >= 0.1) return "#86efac";
  if (s > -0.1) return "#94a3b8";
  if (s > -0.3) return "#fca5a5";
  return "#ef4444";
}

export function sentimentLabel(s: number): string {
  if (s >= 0.3) return "Позитивный";
  if (s >= 0.1) return "Скорее позитивный";
  if (s > -0.1) return "Нейтральный";
  if (s > -0.3) return "Скорее негативный";
  return "Негативный";
}

export function sentimentEmoji(s: number): string {
  if (s >= 0.3) return "🟢";
  if (s >= 0.1) return "🟡";
  if (s > -0.1) return "⚪";
  if (s > -0.3) return "🟠";
  return "🔴";
}

// ── Formatting helpers ─────────────────────────────────

export function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return "—";
  const d = new Date(dateStr);
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short", year: "numeric" });
}

export function formatDateShort(dateStr: string | null | undefined): string {
  if (!dateStr) return "—";
  const d = new Date(dateStr);
  return d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" });
}

export function formatNumber(n: number): string {
  return new Intl.NumberFormat("ru-RU").format(n);
}

export function temperatureColor(t: number): string {
  if (t >= 75) return "#ef4444";
  if (t >= 55) return "#f97316";
  if (t >= 40) return "#eab308";
  if (t >= 25) return "#22c55e";
  return "#3b82f6";
}
