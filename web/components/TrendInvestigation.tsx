"use client";

import { useId, type KeyboardEvent } from "react";
import { ArrowUpRight, CircleAlert, LoaderCircle, ShieldAlert } from "lucide-react";
import Plot from "./Plot";
import type {
  RadarCoverage,
  RadarContour,
  RadarEvidenceItem,
  RadarEvidencePage,
  RadarMethodology,
  RadarTimeline,
  RadarTimelineItem,
  RadarTrend,
  RadarTrendState,
} from "@/lib/types";
import { safeHttpUrl } from "@/lib/urls";

export type RadarView = "propagation" | "evidence" | "coverage" | "method";

const VIEWS: Array<{ id: RadarView; label: string }> = [
  { id: "propagation", label: "распространение" },
  { id: "evidence", label: "доказательства" },
  { id: "coverage", label: "покрытие" },
  { id: "method", label: "метод" },
];

const STATE_RANK: Record<RadarTrendState, number> = {
  rejected: 0,
  candidate: 1,
  emerging: 2,
  confirmed: 3,
  cooling: 2,
  resolved: 1,
};

const RADAR_STATES = new Set<RadarTrendState>(["candidate", "emerging", "confirmed", "cooling", "resolved", "rejected"]);
const RADAR_CONTOURS = new Set<RadarContour>(["media", "action"]);

type ValidatedTimelineRow =
  | { category: "state"; at: string | null; state: RadarTrendState | null; contour: RadarContour | null }
  | { category: "t0_revision"; at: string | null; revisionKind: "automatic" | "analyst" | null }
  | { category: "unknown"; at: string | null; rawKind: string; state: RadarTrendState | null };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isRadarTrendState(value: unknown): value is RadarTrendState {
  return typeof value === "string" && RADAR_STATES.has(value as RadarTrendState);
}

function isRadarContour(value: unknown): value is RadarContour {
  return typeof value === "string" && RADAR_CONTOURS.has(value as RadarContour);
}

function validDate(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" && Number.isFinite(Date.parse(value)) ? value : null;
}

function validateTimelineRow(item: RadarTimelineItem): ValidatedTimelineRow {
  const raw = isRecord(item) ? item as unknown as Record<string, unknown> : {};
  const kind = typeof raw.kind === "string" ? raw.kind : "";
  const at = validDate(raw.at);
  if (kind === "state") {
    return {
      category: "state",
      at,
      state: isRadarTrendState(raw.state) ? raw.state : null,
      contour: isRadarContour(raw.contour) ? raw.contour : null,
    };
  }
  if (kind === "t0_revision") {
    const evidence = isRecord(raw.evidence) ? raw.evidence : {};
    const revisionKind = evidence.revision_kind;
    return {
      category: "t0_revision",
      at,
      revisionKind: revisionKind === "automatic" || revisionKind === "analyst" ? revisionKind : null,
    };
  }
  return {
    category: "unknown",
    at,
    rawKind: kind || "тип не указан",
    state: isRadarTrendState(raw.state) ? raw.state : null,
  };
}

function timelineLabel(item: ValidatedTimelineRow): string {
  if (item.category === "state") return `изменение состояния · ${item.state ?? "состояние не распознано"} · ${item.contour ?? "без контура"}`;
  if (item.category === "t0_revision") {
    if (item.revisionKind === "automatic") return "автоматическая ревизия T0 · без контура";
    if (item.revisionKind === "analyst") return "аналитическая ревизия T0 · без контура";
    return "ревизия T0 · тип не указан · без контура";
  }
  return `неизвестное событие · ${item.rawKind}${item.state ? ` · ${item.state}` : ""}`;
}

const date = (value: string | null) => value
  && Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat("ru-RU", {
    day: "numeric", month: "short", year: "numeric", timeZone: "UTC",
  }).format(new Date(value))
  : "не установлено";

const pct = (value: number | null) => value == null ? "—" : `${Math.round(value * 100)}%`;

function EvidenceRow({ item }: { item: RadarEvidenceItem }) {
  const href = safeHttpUrl(item.url);
  const title = item.title || "Материал без заголовка";
  return (
    <li className="py-3 text-sm first:pt-0 last:pb-0">
      {href ? (
        <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center gap-1 font-semibold text-fg hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:min-h-0">
          {title}<ArrowUpRight size={12} aria-hidden="true" />
        </a>
      ) : <span className="font-semibold">{title}</span>}
      <p className="mt-1 text-xs text-dim">{item.role} · вклад {item.contribution ?? "—"} · {item.why_included}</p>
    </li>
  );
}

export default function TrendInvestigation({
  trend,
  timeline,
  evidence,
  coverage,
  methodology,
  view,
  onViewChange,
  onLoadMoreEvidence,
  evidenceLoadingMore = false,
  evidenceLoadError = false,
}: {
  trend: RadarTrend;
  timeline: RadarTimeline;
  evidence: RadarEvidencePage;
  coverage: RadarCoverage;
  methodology: RadarMethodology;
  view: RadarView;
  onViewChange: (view: RadarView) => void;
  onLoadMoreEvidence?: () => void;
  evidenceLoadingMore?: boolean;
  evidenceLoadError?: boolean;
}) {
  const tabsId = useId();
  const contradictions = evidence.items.filter((item) => item.role === "contradiction");
  const support = evidence.items.filter((item) => item.role !== "contradiction");
  const evidenceComplete = evidence.next_cursor === null;
  const relevantCoverage = coverage.countries.filter((item) => (
    trend.country_waves.some((wave) => wave.country_code === item.country_code)
  ));
  const timelineRows = timeline.items.map(validateTimelineRow);
  const timelinePoints = timelineRows.filter((item): item is Extract<ValidatedTimelineRow, { category: "state" }> & { at: string; state: RadarTrendState } => (
    item.category === "state" && item.at !== null && item.state !== null
  ));
  const timelineData = [{
    x: timelinePoints.map((item) => item.at),
    y: timelinePoints.map((item) => STATE_RANK[item.state]),
    text: timelinePoints.map((item) => timelineLabel(item)),
    mode: "lines+markers",
    line: { color: "#d94f43", width: 2 },
    marker: { color: "#fbbf24", size: 8 },
    hovertemplate: "%{x}<br>%{text}<extra></extra>",
  }];

  const onTabKey = (event: KeyboardEvent<HTMLButtonElement>, current: number) => {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home"
      ? 0
      : event.key === "End"
        ? VIEWS.length - 1
        : (current + (event.key === "ArrowRight" ? 1 : -1) + VIEWS.length) % VIEWS.length;
    document.getElementById(`${tabsId}-radar-tab-${VIEWS[next].id}`)?.focus();
  };

  return (
    <article className="space-y-4">
      <div role="tablist" aria-label="Представление расследования" className="flex flex-wrap gap-1 border-y border-line py-2">
        {VIEWS.map((item, index) => (
          <button
            key={item.id}
            id={`${tabsId}-radar-tab-${item.id}`}
            type="button"
            role="tab"
            aria-selected={view === item.id}
            aria-controls={`${tabsId}-radar-panel-${item.id}`}
            tabIndex={view === item.id ? 0 : -1}
            onKeyDown={(event) => onTabKey(event, index)}
            onClick={() => onViewChange(item.id)}
            className={`min-h-11 rounded-sm px-3 text-[11px] uppercase tracking-[0.08em] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:min-h-0 sm:py-2 ${view === item.id ? "bg-panel2 text-ru-white" : "text-dim hover:text-fg"}`}
          >
            {item.label}
          </button>
        ))}
      </div>

      {view === "propagation" && <div id={`${tabsId}-radar-panel-propagation`} role="tabpanel" aria-labelledby={`${tabsId}-radar-tab-propagation`} tabIndex={0} className="space-y-4">
        <section className="card p-5">
          <h2 className="display text-[23px]">01 · Что меняется?</h2>
          <p className="mt-3 max-w-4xl text-sm leading-6 text-dim">{trend.thesis}</p>
          <p className="tnum mt-2 text-[11px] uppercase tracking-wide text-dim">направление {trend.direction} · состояние {trend.state} · уверенность {pct(trend.confidence)}</p>
        </section>

        <section className="card p-5">
          <h2 className="display text-[23px]">02 · Где началось и куда распространяется?</h2>
          <p className="mt-3 text-sm text-dim">Эффективный T0: <time className="tnum text-fg" dateTime={trend.t0_effective ?? undefined}>{date(trend.t0_effective)}</time>. Волны сохраняют собственные локальные T0 и состояние.</p>
          {timelinePoints.length > 0 && <Plot data={timelineData} layout={{ height: 220, margin: { t: 24, b: 35, l: 30, r: 20 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent", xaxis: { color: "#74808f", gridcolor: "#1e2836" }, yaxis: { visible: false }, showlegend: false }} className="mt-3 w-full" />}
          <ol aria-label="Хронология тренда" className="mt-4 divide-y divide-line border-y border-line text-xs">
            {timelineRows.map((item, index) => (
              <li key={`${item.category}-${item.at}-${index}`} className="grid gap-1 py-2 sm:grid-cols-[8rem_1fr]">
                <time className="tnum text-dim" dateTime={item.at ?? undefined}>{date(item.at)}</time>
                <span>{timelineLabel(item)}</span>
              </li>
            ))}
          </ol>
          <ol className="mt-4 grid gap-2 sm:grid-cols-2">
            {trend.country_waves.map((wave, index) => (
              <li key={wave.public_id} className="border-l border-line pl-3 text-xs">
                <span className="tnum text-ru-red">{String(index + 1).padStart(2, "0")}</span>{" "}
                <strong className="ml-2 text-fg">{wave.country_code}</strong>
                <span className="text-dim"> · {wave.contour} · {wave.state} · T0 {date(wave.t0_effective)}</span>
              </li>
            ))}
          </ol>
        </section>

        <section className="card p-5"><h2 className="display text-[23px]">03 · Что произошло в медиаконтуре?</h2><p className="mt-3 text-sm text-dim">Медиаконтур: <strong className="text-fg">{trend.contours.media.state}</strong> · связь контуров {trend.contours.media.status}. Он измеряет сдвиг внимания, тона и тезисов в публикациях.</p></section>
        <section className="card p-5"><h2 className="display text-[23px]">04 · Что произошло в контуре действий?</h2><p className="mt-3 text-sm text-dim">Контур действий: <strong className="text-fg">{trend.contours.action.state}</strong> · связь контуров {trend.contours.action.status}. Медийная классификация не заменяет авторитетное подтверждение действия.</p></section>
      </div>}

      {view === "evidence" && <div id={`${tabsId}-radar-panel-evidence`} role="tabpanel" aria-labelledby={`${tabsId}-radar-tab-evidence`} tabIndex={0} className="space-y-4">
        <section className="card p-5">
          <h2 className="display text-[23px]">05 · Почему система в это верит?</h2>
          {support.length ? <ul className="mt-3 divide-y divide-line">{support.map((item) => <EvidenceRow key={item.public_id} item={item} />)}</ul> : <p className="mt-3 text-sm text-dim">{evidenceComplete ? "Среди всех сохранённых доказательств прямых подтверждений нет." : "В загруженных доказательствах прямых подтверждений пока нет."}</p>}
        </section>

        <section className="card p-5">
          <h2 className="display text-[23px]">06 · Какие данные противоречат тренду?</h2>
          {contradictions.length ? (
            <ul className="mt-3 divide-y divide-line">{contradictions.map((item) => <EvidenceRow key={item.public_id} item={item} />)}</ul>
          ) : (
            <p className="mt-3 text-sm text-dim">{evidenceComplete ? "Среди всех сохранённых доказательств противоречий нет." : "В загруженных доказательствах противоречий пока нет."}</p>
          )}
          {!evidenceComplete && onLoadMoreEvidence && (
            <button type="button" onClick={onLoadMoreEvidence} disabled={evidenceLoadingMore} className="mt-5 inline-flex min-h-11 items-center gap-2 rounded-md border border-line px-4 text-[11px] uppercase tracking-wide hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50">
              {evidenceLoadingMore && <LoaderCircle size={13} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />}
              {evidenceLoadingMore ? "загружаем" : "загрузить ещё доказательства"}
            </button>
          )}
          {evidenceLoadError && <p role="alert" className="mt-3 text-xs text-ru-red">Следующую страницу доказательств загрузить не удалось.</p>}
        </section>
      </div>}

      {view === "coverage" && (
        <div id={`${tabsId}-radar-panel-coverage`} role="tabpanel" aria-labelledby={`${tabsId}-radar-tab-coverage`} tabIndex={0}>
          <section className="card p-5">
          <h2 className="display text-[23px]">07 · Достаточно ли покрытие?</h2>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {relevantCoverage.length ? relevantCoverage.map((item) => (
              <div key={item.country_code} className={`border-l-2 px-3 py-2 text-xs ${item.state === "critical" ? "border-ru-red bg-ru-red/5" : item.state === "degraded" ? "border-cooling bg-cooling/5" : "border-ally bg-ally/5"}`}>
                <strong>{item.country_code} · {item.state === "critical" ? "критический пробел покрытия" : item.state === "degraded" ? "покрытие ослаблено" : "покрытие устойчиво"}</strong>
                <p className="tnum mt-1 text-dim">доверие {pct(item.coverage_confidence)}</p>
                {item.blind_spots.length > 0 && <p className="mt-1 text-dim">слепые зоны: {item.blind_spots.map(String).join(", ")}</p>}
              </div>
            )) : <p className="text-sm text-dim">Страновая оценка покрытия не опубликована.</p>}
          </div>
          <p className="mt-3 flex gap-2 text-[11px] leading-5 text-dim"><ShieldAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />{coverage.coverage_source}</p>
          <h3 className="card-title mt-5">Ограничения интерпретации</h3>
          <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-dim">{methodology.limitations.map((item) => <li key={item}>{item}</li>)}</ul>
          </section>
        </div>
      )}

      {view === "method" && (
        <div id={`${tabsId}-radar-panel-method`} role="tabpanel" aria-labelledby={`${tabsId}-radar-tab-method`} tabIndex={0}>
          <section className="card p-5">
          <h2 className="display text-[23px]">08 · Как рассчитаны baseline, T0, уверенность и состояние?</h2>
          <dl className="mt-4 grid gap-px overflow-hidden rounded-md border border-line bg-line text-xs sm:grid-cols-4">
            <div className="bg-panel2 p-3"><dt className="text-dim">Baseline</dt><dd className="tnum mt-1">{methodology.baseline.window_days} дней</dd></div>
            <div className="bg-panel2 p-3"><dt className="text-dim">Ускорение</dt><dd className="tnum mt-1">{methodology.baseline.acceleration_days} дней</dd></div>
            <div className="bg-panel2 p-3"><dt className="text-dim">T0 авто</dt><dd className="tnum mt-1">{date(trend.t0_auto)}</dd></div>
            <div className="bg-panel2 p-3"><dt className="text-dim">T0 эффективный</dt><dd className="tnum mt-1">{date(trend.t0_effective)}</dd></div>
          </dl>
          <p className="mt-3 text-xs leading-5 text-dim">Версия {methodology.detector_version}. Факторы уверенности: {methodology.confidence_factors.join(", ")}.</p>
          <p className="mt-2 text-xs leading-5 text-cooling">{methodology.coverage_hard_gate}</p>
          </section>
        </div>
      )}
    </article>
  );
}
