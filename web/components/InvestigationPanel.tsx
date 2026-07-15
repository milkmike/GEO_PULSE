"use client";

import Link from "next/link";
import { useEffect, useId, useRef, useState } from "react";
import type { RefObject } from "react";
import { X } from "lucide-react";
import { api } from "@/lib/api";
import type { IndexExplanation } from "@/lib/types";
import { safeHttpUrl } from "@/lib/urls";
import { useFeatureFlags } from "./FeatureFlagsProvider";

interface InvestigationPanelProps {
  open: boolean;
  countryCode: string;
  countryName: string;
  at: string | null;
  triggerRef?: RefObject<HTMLElement | null>;
  fallbackFocusRef?: RefObject<HTMLElement | null>;
  onClose: () => void;
}

const LIMITATION_LABELS: Record<string, string> = {
  contextual_proximity_is_not_causation:
    "Материалы находятся рядом по времени и не доказывают причинность.",
  article_level_inputs_unavailable:
    "Для части периода не сохранены входы на уровне отдельных публикаций.",
};

const fmtSigned = (value: number, digits = 1) =>
  `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toLocaleString("ru-RU", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;

const fmtTime = (value: string) => new Date(value).toLocaleString("ru-RU", {
  day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
});

function ContextLink({ item }: { item: IndexExplanation["context"][number] }) {
  const { storiesNavigation, signalDetail } = useFeatureFlags();
  const label = item.label || `${item.scope} #${item.id}`;
  if (item.scope === "story" && storiesNavigation) {
    return <Link className="font-semibold hover:text-accent" href={`/stories/${item.id}`}>{label}</Link>;
  }
  if (item.scope === "signal" && signalDetail) {
    return <Link className="font-semibold hover:text-accent" href={`/signals/${item.id}`}>{label}</Link>;
  }
  const href = item.scope === "article" ? safeHttpUrl(item.url) : null;
  return href ? (
    <a className="font-semibold hover:text-accent" href={href} target="_blank" rel="noopener noreferrer">
      {label}
    </a>
  ) : <span className="font-semibold">{label}</span>;
}

export default function InvestigationPanel({
  open,
  countryCode,
  countryName,
  at,
  triggerRef,
  fallbackFocusRef,
  onClose,
}: InvestigationPanelProps) {
  const headingId = useId();
  const descriptionId = useId();
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const [explanation, setExplanation] = useState<IndexExplanation | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "ready" | "not-found" | "invalid" | "error">("idle");
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!open || !at) {
      setState("idle");
      setExplanation(null);
      return;
    }
    const controller = new AbortController();
    let current = true;
    setState("loading");
    setExplanation(null);
    api.indexExplanation(countryCode, { at, windowHours: 24 }, controller.signal)
      .then((payload) => {
        if (!current || controller.signal.aborted) return;
        setExplanation(payload);
        setState("ready");
      })
      .catch((error: unknown) => {
        if (!current || controller.signal.aborted) return;
        const message = String(error);
        setState(message.includes("404") ? "not-found" : message.includes("422") ? "invalid" : "error");
      });
    return () => {
      current = false;
      controller.abort();
    };
  }, [open, at, countryCode, attempt]);

  useEffect(() => {
    if (!open) return;
    restoreRef.current = triggerRef?.current
      ?? (document.activeElement instanceof HTMLElement ? document.activeElement : null);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    queueMicrotask(() => closeRef.current?.focus());

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab" || !panelRef.current) return;
      const focusable = [...panelRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), details summary, [tabindex]:not([tabindex="-1"])',
      )].filter((element) => !element.hasAttribute("hidden"));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      const target = restoreRef.current?.isConnected
        ? restoreRef.current
        : fallbackFocusRef?.current;
      queueMicrotask(() => target?.focus());
    };
  }, [open]);

  if (!open || !at) return null;

  const exact = explanation?.exact_changes;
  return (
    <div className="fixed inset-0 z-[1000]" data-testid="investigation-overlay">
      <button
        type="button"
        tabIndex={-1}
        className="absolute inset-0 h-full w-full cursor-default bg-black/60"
        aria-label="Закрыть единое расследование"
        onClick={onClose}
      />
      <section
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={headingId}
        aria-describedby={descriptionId}
        className="absolute inset-x-0 bottom-0 max-h-[88dvh] overflow-y-auto rounded-t-2xl border border-line bg-panel shadow-2xl motion-safe:animate-[sheet-in_.22s_ease-out] md:inset-y-0 md:left-auto md:w-[min(560px,92vw)] md:max-h-none md:rounded-none md:border-y-0 md:border-r-0"
      >
        <header className="sticky top-0 z-10 border-b border-line bg-panel/95 px-5 pb-4 pt-5 backdrop-blur">
          <div className="flex items-start gap-4">
            <div className="min-w-0 flex-1">
              <div className="section-num">Единое расследование</div>
              <h2 id={headingId} className="display mt-1 text-2xl">
                <span className="sr-only">Единое расследование: </span>{countryName} · сдвиг RRI
              </h2>
              <p id={descriptionId} className="mt-1 text-xs leading-relaxed text-dim">
                Точный расчёт, модельная оценка и новостной контекст показаны отдельно.
              </p>
            </div>
            <button
              ref={closeRef}
              type="button"
              onClick={onClose}
              className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full border border-line text-dim transition-colors hover:border-ru-blue hover:text-ru-white focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              aria-label="Закрыть единое расследование"
            >
              <X className="h-5 w-5" aria-hidden="true" />
            </button>
          </div>
        </header>

        <div className="space-y-5 p-5">
          {state === "loading" && <p role="status" className="text-sm text-dim">Собираем сохранённые точки и доказательства…</p>}
          {state === "not-found" && (
            <div role="alert" className="card px-4 py-3 text-sm">
              Для этого окна нет двух сохранённых снимков RRI. Выберите соседний отмеченный сдвиг.
            </div>
          )}
          {state === "invalid" && (
            <div role="alert" className="card px-4 py-3 text-sm">
              Время в ссылке некорректно. Закройте панель и выберите точку на графике снова.
            </div>
          )}
          {state === "error" && (
            <div role="alert" className="card px-4 py-3 text-sm">
              <p>Не удалось загрузить расследование.</p>
              <button type="button" className="mt-2 min-h-11 text-accent underline" onClick={() => setAttempt((value) => value + 1)}>
                Повторить
              </button>
            </div>
          )}

          {explanation && exact && (
            <>
              <div className="flex flex-wrap gap-2 text-xs text-dim">
                <span>{fmtTime(explanation.from_time)} → {fmtTime(explanation.to_time)}</span>
                <span>· {explanation.rri_version}</span>
                {explanation.evidence_completeness === "partial" && (
                  <span className="rounded-full border border-cooling px-2 py-0.5 text-cooling">частичные доказательства</span>
                )}
              </div>

              <section role="region" aria-labelledby={`${headingId}-exact`} className="rounded-lg border border-ally/40 bg-ally/5 p-4">
                <div className="flex items-baseline justify-between gap-4">
                  <h3 id={`${headingId}-exact`} className="card-title">Что изменило расчёт · точно</h3>
                  <strong className="tnum text-xl text-ally">{fmtSigned(exact.total_delta)}</strong>
                </div>
                <p className="mt-1 text-xs text-dim">
                  Точные сохранённые снимки за 24 часа: {fmtSigned(exact.from_value)} → {fmtSigned(exact.to_value)}
                </p>
                <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
                  {[
                    ["Структурный слой", exact.structural_delta],
                    ["Медиа-слой", exact.media_delta],
                    ["Буст событий", exact.boost_delta],
                    ["Точный подытог", exact.exact_subtotal],
                    ["Поправка расчёта", exact.calculation_adjustment_delta],
                    ["Остаток округления", exact.rounding_residual],
                  ].map(([label, value]) => (
                    <div key={label as string} className="rounded bg-panel2 px-3 py-2">
                      <dt className="text-[11px] text-dim">{label}</dt>
                      <dd className="tnum font-semibold">{fmtSigned(value as number)}</dd>
                    </div>
                  ))}
                </dl>
                <details className="mt-3 text-xs text-dim">
                  <summary className="min-h-11 cursor-pointer py-3 text-accent">Входы и версия расчёта</summary>
                  <p>Статей: {exact.input_counts.from_articles ?? "не сохранено"} → {exact.input_counts.to_articles ?? "не сохранено"}</p>
                  <p>Объём GDELT: {exact.input_counts.from_gdelt_volume ?? "не сохранено"} → {exact.input_counts.to_gdelt_volume ?? "не сохранено"}</p>
                  <p>Версия: {exact.rri_version}</p>
                </details>
              </section>

              <section role="region" aria-labelledby={`${headingId}-estimated`} className="rounded-lg border border-cooling/40 bg-cooling/5 p-4">
                <h3 id={`${headingId}-estimated`} className="card-title">Оценка вклада новостных поводов · модельная оценка</h3>
                <p className="mt-1 text-xs leading-relaxed text-dim">
                  Контрфактическая оценка: как изменился бы медиаслой без выбранной группы публикаций. Это не точный причинный вклад.
                </p>
                <div className="mt-3 space-y-2">
                  {explanation.estimated_contributions.length === 0 && <p className="text-sm text-dim">Оценка недоступна для этого интервала.</p>}
                  {explanation.estimated_contributions.map((item, index) => (
                    <article key={`${item.event_key ?? "event"}-${index}`} className="rounded bg-panel2 px-3 py-3 text-sm">
                      <div className="flex items-start justify-between gap-3">
                        <strong>{item.event_key || "Новостной повод"}</strong>
                        {item.status === "estimated" && item.estimated_delta != null
                          ? <span className="tnum text-cooling">{fmtSigned(item.estimated_delta)}</span>
                          : <span className="text-xs text-dim">оценка пропущена</span>}
                      </div>
                      <p className="mt-1 text-xs text-dim">{item.status === "omitted" ? item.reason : item.why_included}</p>
                      {item.confidence != null && <p className="mt-1 text-[11px] text-dim">Уверенность: {Math.round(item.confidence * 100)}%</p>}
                    </article>
                  ))}
                </div>
              </section>

              <section role="region" aria-labelledby={`${headingId}-context`} className="rounded-lg border border-ru-blue/40 bg-ru-blue/5 p-4">
                <h3 id={`${headingId}-context`} className="card-title">Что происходило рядом · контекст, не причина</h3>
                <p className="mt-1 text-xs leading-relaxed text-dim">
                  Временная и тематическая близость помогает исследованию, но не доказывает причинность.
                </p>
                <ul className="mt-3 space-y-2">
                  {explanation.context.length === 0 && <li className="text-sm text-dim">Рядом не найдено индексированных материалов.</li>}
                  {explanation.context.map((item) => (
                    <li key={`${item.scope}-${item.id}`} className="rounded bg-panel2 px-3 py-3 text-sm">
                      <ContextLink item={item} />
                      <div className="mt-1 text-xs leading-relaxed text-dim">{item.why_included}</div>
                      {item.occurred_at && <time className="mt-1 block text-[11px] text-dim" dateTime={item.occurred_at}>{fmtTime(item.occurred_at)}</time>}
                    </li>
                  ))}
                </ul>
              </section>

              {explanation.limitations.length > 0 && (
                <section aria-labelledby={`${headingId}-limitations`} className="border-t border-line pt-4">
                  <h3 id={`${headingId}-limitations`} className="card-title">Ограничения</h3>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed text-dim">
                    {explanation.limitations.map((item) => <li key={item}>{LIMITATION_LABELS[item] ?? item.replaceAll("_", " ")}</li>)}
                  </ul>
                </section>
              )}
            </>
          )}
        </div>
      </section>
    </div>
  );
}
