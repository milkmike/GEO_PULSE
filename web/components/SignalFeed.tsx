"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import { eventTypeRu, fmtDate, SIGNAL_RU, sourceTierRu } from "@/lib/format";
import type { SignalArticleReference, SignalEvidencePreview, SignalListItem } from "@/lib/types";
import { safeHttpUrl } from "@/lib/urls";

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

function ArticlePreviewRow({ article }: { article: SignalArticleReference }) {
  const title = article.title?.trim() || `Публикация #${article.id}`;
  const source = article.source_name?.trim() || "Источник не сохранён";
  const href = safeHttpUrl(article.url);
  const titleNode = <span className="line-clamp-2 font-medium leading-snug text-ru-white">{title}</span>;

  return (
    <li className="min-w-0 py-1.5 first:pt-0 last:pb-0">
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="block min-h-11 rounded-sm py-1.5 transition-colors hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <span className="sr-only">Открыть первоисточник: </span>
          {titleNode}
          <span className="sr-only"> — {source}; откроется в новой вкладке</span>
        </a>
      ) : (
        <div className="min-h-11 py-1.5">
          {titleNode}
          <p className="mt-0.5 text-[10px] leading-tight text-dim">Ссылка на первоисточник не сохранена.</p>
        </div>
      )}
      <div className="flex min-w-0 items-center gap-1 overflow-hidden whitespace-nowrap text-[10px] text-dim">
        <span className="truncate">{source}</span>
        {article.published_at && (
          <>
            <span aria-hidden="true">·</span>
            <time className="shrink-0" dateTime={article.published_at}>{fmtDate(article.published_at)}</time>
          </>
        )}
      </div>
    </li>
  );
}

function EvidencePreview({ preview }: { preview: SignalEvidencePreview | null | undefined }) {
  const kind = preview?.kind ?? "unavailable";
  const isEvidence = kind === "evidence";
  const articles = preview?.articles.slice(0, 2) ?? [];

  return (
    <section className="mt-3 border-t border-line/70 pt-2.5" aria-label={isEvidence ? "Источники сигнала" : "Контекст сигнала"}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h4 className="text-[10px] font-semibold uppercase tracking-wide text-ru-white">
          {isEvidence ? "На чём основан сигнал" : "Публикации в окне сигнала"}
        </h4>
        {preview && kind !== "unavailable" && articles.length > 0 && (
          <span className="text-[10px] text-dim">
            {articles.length} из {preview.total} {isEvidence ? "сохранённых публикаций" : "релевантных публикаций"}
          </span>
        )}
      </div>

      {kind === "context" && (
        <p className="mt-1 text-[10px] leading-snug text-dim">
          Контекст для проверки; причинная связь не установлена.
        </p>
      )}

      {kind === "context" && articles.length === 0 ? (
        <p className="mt-2 text-[11px] leading-snug text-dim">За 72 часа до сигнала релевантные публикации не найдены.</p>
      ) : kind === "unavailable" ? (
        <p className="mt-2 text-[11px] leading-snug text-dim">Публикации для этого сигнала не найдены или не сохранились.</p>
      ) : (
        <ul className="mt-2 divide-y divide-line/70 border-y border-line/70 text-[11px]">
          {articles.map((article) => <ArticlePreviewRow key={article.id} article={article} />)}
        </ul>
      )}
    </section>
  );
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
  const titleId = `signal-${signal.id}-title`;
  const className = `rounded-r-md border-l-2 bg-panel2 px-3 py-3 ${SEV_BORDER[signal.severity] ?? "border-l-line"}`;

  return (
    <article className={className} aria-labelledby={titleId}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[10px] uppercase tracking-wide text-dim">
        {showCountry && signal.country_name && <span className="text-ru-white">{signal.country_name}</span>}
        <span>{SEVERITY_RU[signal.severity] ?? signal.severity}</span>
        <span>уверенность {Math.round(signal.confidence * 100)}%</span>
      </div>
      <h3 id={titleId} className="mt-1 text-[13px] font-semibold leading-snug">{signal.title}</h3>
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
      <EvidencePreview preview={signal.evidence_preview} />
      {detailEnabled && (
        <Link
          href={`/signals/${signal.id}`}
          className="mt-3 inline-flex min-h-11 w-full items-center justify-between rounded-sm border border-line px-3 py-2 text-[10px] font-semibold uppercase tracking-wide text-accent transition-colors hover:border-accent hover:text-ru-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:w-auto"
        >
          Открыть разбор сигнала <span aria-hidden="true">→</span>
        </Link>
      )}
    </article>
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
