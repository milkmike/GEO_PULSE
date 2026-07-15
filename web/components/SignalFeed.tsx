"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import { eventTypeRu, fmtDate, SIGNAL_RU, sourceTierRu } from "@/lib/format";
import type { SignalListItem } from "@/lib/types";

const SEV_BORDER: Record<string, string> = {
  info: "border-l-line",
  warning: "border-l-cooling",
  critical: "border-l-hostile",
};

const SEVERITY_RU: Record<string, string> = {
  info: "наблюдение",
  warning: "внимание",
  critical: "критический",
};

const FACT_LABELS: Record<string, string> = {
  delta_24h: "сдвиг за 24 часа",
  score: "значение",
  articles_24h: "статей за 24 часа",
  baseline_daily: "база в день",
  tone: "тон",
  mean_90d: "среднее за 90 дней",
  z_score: "z-оценка",
  share: "доля",
  baseline_share: "базовая доля",
  change_1d_pct: "изменение за день",
  target_count: "целей",
  action_level: "уровень действия",
  event_type: "тип события",
  articles: "статьи",
  tiers: "тиры",
  event_key: "новостной повод",
  loud_articles: "публикаций вне официальных СМИ",
  hours_silent: "часов без официальной реакции",
  avg_sentiment: "средний тон",
  max_action_level: "максимальный уровень действия",
  ratio: "во сколько раз выше базы",
  std: "обычное отклонение",
  volume: "публикаций в день",
  level: "уровень RRI",
  sentiment: "тональность",
  currency: "валюта",
  rate_to_rub: "курс к рублю",
  countries: "страны",
  media_preceded: "медиа-сигнал был раньше",
  delta: "новых санкционных целей",
  lists_count: "санкционных программ",
  last_change: "последнее обновление",
  official_or_mainstream_sources_available: "доступны официальные или крупные СМИ",
};

const SIGNED_FACTS = new Set(["delta_24h", "tone", "avg_sentiment", "sentiment", "change_1d_pct", "z_score"]);

function factValue(key: string, value: unknown): string | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    const formatted = value.toLocaleString("ru-RU", { maximumFractionDigits: 2 });
    const signed = SIGNED_FACTS.has(key) && value > 0 ? `+${formatted}` : formatted;
    if (key === "share" || key === "baseline_share") {
      return `${(value * 100).toLocaleString("ru-RU", { maximumFractionDigits: 2 })}%`;
    }
    if (key === "change_1d_pct") return `${signed}%`;
    if (key === "ratio") return `×${formatted}`;
    return signed;
  }
  if (typeof value === "boolean") return value ? "да" : "нет";
  if (typeof value === "string" && value.trim() && value.length <= 80) {
    if (key === "event_type") return eventTypeRu(value);
    if (key === "tier") return sourceTierRu(value);
    return value;
  }
  if (Array.isArray(value) && value.length > 0 && (key === "tiers" || value.length <= 4)) {
    const items = value.filter((item) => ["string", "number", "boolean"].includes(typeof item));
    if (items.length === value.length) {
      if (key === "tiers") return items.map((item) => sourceTierRu(String(item))).join(", ");
      return items.join(", ");
    }
  }
  return null;
}

function signalFacts(signal: SignalListItem): Array<{ key: string; label: string; value: string }> {
  if (!signal.payload) return [];
  return Object.entries(signal.payload)
    .map(([key, raw]) => ({
      key,
      label: FACT_LABELS[key] ?? `параметр «${key}»`,
      value: factValue(key, raw),
    }))
    .filter((item): item is { key: string; label: string; value: string } => item.value != null)
    .slice(0, 3);
}

function SignalCard({
  signal,
  detailEnabled,
  showCountry,
}: {
  signal: SignalListItem;
  detailEnabled: boolean;
  showCountry: boolean;
}) {
  const facts = signalFacts(signal);
  const className = `block rounded-r-md border-l-2 bg-panel2 px-3 py-3 transition-colors hover:bg-panel ${SEV_BORDER[signal.severity] ?? "border-l-line"}`;
  const content = (
    <>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] uppercase tracking-wide text-dim">
        {showCountry && signal.country_name && <span className="text-ru-white">{signal.country_name}</span>}
        <span>{SEVERITY_RU[signal.severity] ?? signal.severity}</span>
        <span>уверенность {Math.round(signal.confidence * 100)}%</span>
      </div>
      <div className="mt-1 text-[13px] font-semibold leading-snug">{signal.title}</div>
      {signal.description && <div className="mt-1 text-xs leading-snug text-dim">{signal.description}</div>}
      {facts.length > 0 && (
        <ul className="mt-2 grid gap-1 text-[11px] sm:grid-cols-3">
          {facts.map((fact) => (
            <li key={fact.key} className="rounded border border-line/70 px-2 py-1">
              <span className="text-dim">{fact.label}</span>{" "}
              <strong className="tnum text-ru-white">{fact.value}</strong>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2 text-[11px] text-dim">
        {SIGNAL_RU[signal.type] ?? signal.type.replaceAll("_", " ")} · {fmtDate(signal.created_at)}
        {typeof signal.active === "boolean" && (
          <span className={`ml-2 ${signal.active ? "text-ally" : "text-dim"}`}>
            {signal.active ? "● активен" : "истёк"}
          </span>
        )}
      </div>
    </>
  );

  return detailEnabled ? (
    <Link href={`/signals/${signal.id}`} className={className} aria-label={`Открыть сигнал: ${signal.title}`}>
      {content}
    </Link>
  ) : (
    <article className={className} aria-label={signal.title}>{content}</article>
  );
}

const containerVariants = { show: { transition: { staggerChildren: 0.035 } } };
const itemVariants = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0 },
};

export default function SignalFeed({
  signals,
  emptyText = "Активных сигналов нет",
  detailEnabled = false,
  showCountry = true,
}: {
  signals: SignalListItem[];
  emptyText?: string;
  detailEnabled?: boolean;
  showCountry?: boolean;
}) {
  const reduce = useReducedMotion();
  if (!signals.length) return <div className="px-4 py-3 text-xs text-dim">{emptyText}</div>;
  if (reduce) {
    return (
      <div className="space-y-2 px-3 pb-3">
        {signals.map((signal) => <SignalCard key={signal.id} signal={signal} detailEnabled={detailEnabled} showCountry={showCountry} />)}
      </div>
    );
  }
  return (
    <motion.div
      key={`${signals.length}-${signals[0]?.id ?? ""}`}
      className="space-y-2 px-3 pb-3"
      variants={containerVariants}
      initial="hidden"
      animate="show"
    >
      {signals.map((signal) => (
        <motion.div key={signal.id} variants={itemVariants}>
          <SignalCard signal={signal} detailEnabled={detailEnabled} showCountry={showCountry} />
        </motion.div>
      ))}
    </motion.div>
  );
}
