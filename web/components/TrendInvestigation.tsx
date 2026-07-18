"use client";

import type { KeyboardEvent } from "react";
import { ArrowUpRight, CircleAlert, ShieldAlert } from "lucide-react";
import Plot from "./Plot";
import type { RadarCoverage, RadarEvidencePage, RadarMethodology, RadarTimeline, RadarTrend } from "@/lib/types";
import { safeHttpUrl } from "@/lib/urls";

export type RadarView = "propagation" | "evidence" | "coverage" | "method";
const VIEWS: Array<{ id: RadarView; label: string }> = [
  { id: "propagation", label: "распространение" }, { id: "evidence", label: "доказательства" },
  { id: "coverage", label: "покрытие" }, { id: "method", label: "метод" },
];

const date = (value: string | null) => value ? new Intl.DateTimeFormat("ru-RU", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" }).format(new Date(value)) : "не установлено";
const pct = (value: number | null) => value == null ? "—" : `${Math.round(value * 100)}%`;

export default function TrendInvestigation({ trend, timeline, evidence, coverage, methodology, view, onViewChange }: {
  trend: RadarTrend; timeline: RadarTimeline; evidence: RadarEvidencePage; coverage: RadarCoverage;
  methodology: RadarMethodology; view: RadarView; onViewChange: (view: RadarView) => void;
}) {
  const contradictions = evidence.items.filter((item) => item.role === "contradiction");
  const support = evidence.items.filter((item) => item.role !== "contradiction");
  const relevantCoverage = coverage.countries.filter((item) => trend.country_waves.some((wave) => wave.country_code === item.country_code));
  const onTabKey = (event: KeyboardEvent<HTMLButtonElement>, current: number) => {
    if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? VIEWS.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + VIEWS.length) % VIEWS.length;
    document.getElementById(`radar-view-${VIEWS[next].id}`)?.focus();
  };
  const chartData = [{
    x: trend.country_waves.map((wave) => wave.t0_effective),
    y: trend.country_waves.map((_, index) => index + 1),
    text: trend.country_waves.map((wave) => wave.country_code),
    mode: "lines+markers+text", textposition: "top center", line: { color: "#d94f43", width: 2 }, marker: { color: "#fbbf24", size: 8 },
  }];

  return (
    <article className="space-y-4">
      <div role="tablist" aria-label="Представление расследования" className="flex flex-wrap gap-1 border-y border-line py-2">
        {VIEWS.map((item, index) => <button key={item.id} id={`radar-view-${item.id}`} type="button" role="tab" aria-selected={view === item.id} tabIndex={view === item.id ? 0 : -1} onKeyDown={(event) => onTabKey(event, index)} onClick={() => onViewChange(item.id)} className={`min-h-11 rounded-sm px-3 text-[11px] uppercase tracking-[0.08em] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:min-h-0 sm:py-2 ${view === item.id ? "bg-panel2 text-ru-white" : "text-dim hover:text-fg"}`}>{item.label}</button>)}
      </div>

      <section className="card p-5"><h2 className="display text-[23px]">01 · Что меняется?</h2><p className="mt-3 max-w-4xl text-sm leading-6 text-dim">{trend.thesis}</p><p className="tnum mt-2 text-[11px] uppercase tracking-wide text-dim">направление {trend.direction} · состояние {trend.state} · уверенность {pct(trend.confidence)}</p></section>

      <section className="card p-5"><h2 className="display text-[23px]">02 · Где началось и куда распространяется?</h2><p className="mt-3 text-sm text-dim">Эффективный T0: <time className="tnum text-fg" dateTime={trend.t0_effective ?? undefined}>{date(trend.t0_effective)}</time>. Волны сохраняют собственные локальные T0 и состояние.</p>{view === "propagation" && trend.country_waves.length > 1 && <Plot data={chartData} layout={{ height: 220, margin: { t: 30, b: 35, l: 30, r: 20 }, paper_bgcolor: "transparent", plot_bgcolor: "transparent", xaxis: { color: "#74808f", gridcolor: "#1e2836" }, yaxis: { visible: false }, showlegend: false }} className="mt-3 w-full" />}<ol className="mt-4 grid gap-2 sm:grid-cols-2">{trend.country_waves.map((wave, index) => <li key={wave.public_id} className="border-l border-line pl-3 text-xs"><span className="tnum text-ru-red">{String(index + 1).padStart(2, "0")}</span> <strong className="ml-2 text-fg">{wave.country_code}</strong><span className="text-dim"> · {wave.contour} · {wave.state} · T0 {date(wave.t0_effective)}</span></li>)}</ol></section>

      <section className="card p-5"><h2 className="display text-[23px]">03 · Что произошло в медиаконтуре?</h2><p className="mt-3 text-sm text-dim">Медиаконтур: <strong className="text-fg">{trend.contours.media.state}</strong> · связь контуров {trend.contours.media.status}. Он измеряет сдвиг внимания, тона и тезисов в публикациях.</p></section>
      <section className="card p-5"><h2 className="display text-[23px]">04 · Что произошло в контуре действий?</h2><p className="mt-3 text-sm text-dim">Контур действий: <strong className="text-fg">{trend.contours.action.state}</strong> · связь контуров {trend.contours.action.status}. Медийная классификация не заменяет авторитетное подтверждение действия.</p></section>

      <section className="card p-5"><h2 className="display text-[23px]">05 · Почему система в это верит?</h2>{support.length ? <ul className="mt-3 divide-y divide-line">{support.map((item) => { const href = safeHttpUrl(item.url); return <li key={item.public_id} className="py-3 text-sm first:pt-0 last:pb-0">{href ? <a href={href} target="_blank" rel="noopener noreferrer" className="inline-flex min-h-11 items-center gap-1 font-semibold text-fg hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:min-h-0">{item.title || "Материал без заголовка"}<ArrowUpRight size={12} aria-hidden="true" /></a> : <span className="font-semibold">{item.title || "Материал без заголовка"}</span>}<p className="mt-1 text-xs text-dim">{item.role} · вклад {item.contribution ?? "—"} · {item.why_included}</p></li>; })}</ul> : <p className="mt-3 text-sm text-dim">Сохранённых прямых доказательств нет.</p>}</section>

      <section className="card p-5"><h2 className="display text-[23px]">06 · Какие данные противоречат тренду?</h2>{contradictions.length ? <ul className="mt-3 space-y-2">{contradictions.map((item) => { const href = safeHttpUrl(item.url); return <li key={item.public_id} className="flex gap-2 border-l-2 border-cooling pl-3 text-sm"><CircleAlert size={14} className="mt-0.5 shrink-0 text-cooling" aria-hidden="true" />{href ? <a href={href} target="_blank" rel="noopener noreferrer" className="font-semibold hover:text-accent">{item.title}</a> : <span className="font-semibold">{item.title}</span>}</li>; })}</ul> : <p className="mt-3 text-sm text-dim">Сохранённых противоречий нет.</p>}</section>

      <section className="card p-5"><h2 className="display text-[23px]">07 · Достаточно ли покрытие?</h2><div className="mt-3 grid gap-2 sm:grid-cols-2">{relevantCoverage.length ? relevantCoverage.map((item) => <div key={item.country_code} className={`border-l-2 px-3 py-2 text-xs ${item.state === "critical" ? "border-ru-red bg-ru-red/5" : item.state === "degraded" ? "border-cooling bg-cooling/5" : "border-ally bg-ally/5"}`}><strong>{item.country_code} · {item.state === "critical" ? "критический пробел покрытия" : item.state === "degraded" ? "покрытие ослаблено" : "покрытие устойчиво"}</strong><p className="tnum mt-1 text-dim">доверие {pct(item.coverage_confidence)}</p>{item.blind_spots.length > 0 && <p className="mt-1 text-dim">слепые зоны: {item.blind_spots.map(String).join(", ")}</p>}</div>) : <p className="text-sm text-dim">Страновая оценка покрытия не опубликована.</p>}</div><p className="mt-3 flex gap-2 text-[11px] leading-5 text-dim"><ShieldAlert size={14} className="mt-0.5 shrink-0" aria-hidden="true" />{coverage.coverage_source}</p></section>

      <section className="card p-5"><h2 className="display text-[23px]">08 · Как рассчитаны baseline, T0, уверенность и состояние?</h2><dl className="mt-4 grid gap-px overflow-hidden rounded-md border border-line bg-line text-xs sm:grid-cols-4"><div className="bg-panel2 p-3"><dt className="text-dim">Baseline</dt><dd className="tnum mt-1">{methodology.baseline.window_days} дней</dd></div><div className="bg-panel2 p-3"><dt className="text-dim">Ускорение</dt><dd className="tnum mt-1">{methodology.baseline.acceleration_days} дней</dd></div><div className="bg-panel2 p-3"><dt className="text-dim">T0 авто</dt><dd className="tnum mt-1">{date(trend.t0_auto)}</dd></div><div className="bg-panel2 p-3"><dt className="text-dim">T0 эффективный</dt><dd className="tnum mt-1">{date(trend.t0_effective)}</dd></div></dl><p className="mt-3 text-xs leading-5 text-dim">Версия {methodology.detector_version}. Факторы уверенности: {methodology.confidence_factors.join(", ")}.</p><p className="mt-2 text-xs leading-5 text-cooling">{methodology.coverage_hard_gate}</p><ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-dim">{methodology.limitations.map((item) => <li key={item}>{item}</li>)}</ul></section>
    </article>
  );
}
