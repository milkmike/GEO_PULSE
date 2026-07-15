"use client";

import Link from "next/link";
import Plot from "./Plot";
import type { SignalDetail } from "@/lib/types";
import { safeHttpUrl } from "@/lib/urls";

const SEVERITY_LABEL: Record<string, string> = {
  info: "наблюдение",
  warning: "требует внимания",
  critical: "критический сигнал",
};

const LIMITATION_LABEL: Record<string, string> = {
  contextual_proximity_is_not_causation:
    "Связанные материалы дают контекст, но их близость не доказывает причинность.",
  threshold_not_persisted: "Исторический порог срабатывания не сохранён.",
};

const readableKey = (key: string) => key.replaceAll("_", " ");

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "не сохранено";
  if (typeof value === "boolean") return value ? "да" : "нет";
  if (typeof value === "number") return value.toLocaleString("ru-RU", { maximumFractionDigits: 3 });
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(displayValue).join(", ");
  return "сложное значение сохранено в доказательстве";
}

function EvidenceRecord({
  title,
  value,
  emptyText = "не сохранено",
}: {
  title: string;
  value: Record<string, unknown>;
  emptyText?: string;
}) {
  const entries = Object.entries(value);
  const notApplicable = value.type === "not_applicable";
  return (
    <section role="region" aria-label={title} className="rounded-lg border border-line bg-panel2 p-4">
      <h3 className="text-xs uppercase tracking-wide text-dim">{title}</h3>
      {notApplicable ? (
        <p className="mt-2 text-sm">сравнение не требуется</p>
      ) : entries.length === 0 ? (
        <p className="mt-2 text-sm text-dim">{emptyText}</p>
      ) : (
        <ul className="mt-2 space-y-1 text-sm">
          {entries.map(([key, item]) => (
            <li key={key} className="flex items-start justify-between gap-4 border-b border-dashed border-line py-1 last:border-0">
              <span className="text-dim">{readableKey(key)}</span>
              <strong className="tnum text-right text-ru-white">{displayValue(item)}</strong>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const fmtTime = (value: string) => new Date(value).toLocaleString("ru-RU", {
  day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
});

export default function SignalEvidence({ detail, storiesEnabled }: { detail: SignalDetail; storiesEnabled: boolean }) {
  const chartPoints = detail.chart_points
    .map((point) => ({ time: point.time, score: point.score }))
    .filter((point): point is { time: string; score: number } =>
      typeof point.time === "string"
      && Number.isFinite(Date.parse(point.time))
      && typeof point.score === "number"
      && Number.isFinite(point.score),
    );
  const chartData = [{
    x: chartPoints.map((point) => point.time),
    y: chartPoints.map((point) => point.score),
    type: "scatter",
    mode: "lines+markers",
    name: "RRI",
    line: { color: "#fbbf24", width: 2 },
    marker: { color: "#fbbf24", size: 6 },
  }];
  const windowSaved = detail.values.window.start && detail.values.window.end;

  return (
    <article className="space-y-6">
      <header className="border-b border-line pb-5">
        <div className="flex flex-wrap items-center gap-2 text-[11px] uppercase tracking-wide text-dim">
          <span>{SEVERITY_LABEL[detail.severity] ?? detail.severity}</span>
          <span>уверенность {Math.round(detail.confidence * 100)}%</span>
          <span className={detail.state.active ? "text-ally" : "text-dim"}>
            {detail.state.active ? "● активен" : "истёк"}
          </span>
          {detail.evidence_completeness === "partial" && (
            <span className="rounded-full border border-cooling px-2 py-0.5 text-cooling">частичные доказательства</span>
          )}
        </div>
        <h1 className="display mt-3 text-3xl leading-tight sm:text-4xl">{detail.summary.headline}</h1>
        {detail.summary.what_changed && <p className="lead mt-4">{detail.summary.what_changed}</p>}
        {detail.summary.description && detail.summary.description !== detail.summary.what_changed && (
          <p className="mt-2 text-sm text-dim">{detail.summary.description}</p>
        )}
      </header>

      <section aria-labelledby="signal-rule-heading" className="card p-5">
        <h2 id="signal-rule-heading" className="card-title">Почему сработал сигнал</h2>
        <p className="mt-2 text-sm leading-relaxed text-dim">
          {detail.rule.description || "Описание исторического правила не сохранено."}
        </p>
        <div className="mt-2 text-xs text-dim">Детектор {detail.rule.detector} · версия {detail.rule.version}</div>
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          <EvidenceRecord title="Наблюдаемое значение" value={detail.values.observed} />
          <EvidenceRecord title="Базовое значение" value={detail.values.baseline} />
          <EvidenceRecord title="Порог срабатывания" value={detail.rule.threshold} />
        </div>
        {detail.rule.current_rule_reference != null && (
          <aside className="mt-4 rounded border border-cooling/40 bg-cooling/5 p-3">
            <strong className="text-xs uppercase tracking-wide text-cooling">Текущее правило, не исторический порог</strong>
            <p className="mt-1 text-xs text-dim">{typeof detail.rule.current_rule_reference === "string"
              ? detail.rule.current_rule_reference
              : Object.entries(detail.rule.current_rule_reference).map(([key, value]) => `${readableKey(key)}: ${displayValue(value)}`).join(" · ")}</p>
          </aside>
        )}
      </section>

      <section aria-labelledby="signal-window-heading" className="card p-5">
        <h2 id="signal-window-heading" className="card-title">Окно сравнения</h2>
        {windowSaved ? (
          <p className="mt-2 text-sm">{fmtTime(detail.values.window.start!)} → {fmtTime(detail.values.window.end!)}</p>
        ) : <p className="mt-2 text-sm text-dim">не сохранено</p>}
        {(detail.values.window.basis || detail.values.window.status) && (
          <p className="mt-1 text-xs text-dim">
            {detail.values.window.basis ? readableKey(detail.values.window.basis) : ""}
            {detail.values.window.status ? ` · ${readableKey(detail.values.window.status)}` : ""}
          </p>
        )}
        <div className="mt-4">
          {chartPoints.length >= 2 ? (
            <Plot
              data={chartData}
              layout={{
                height: 230,
                margin: { t: 8, b: 34, l: 40, r: 12 },
                paper_bgcolor: "transparent",
                plot_bgcolor: "transparent",
                xaxis: { color: "#6b7280", gridcolor: "#1f2937" },
                yaxis: { color: "#6b7280", gridcolor: "#1f2937" },
                showlegend: false,
              }}
              className="w-full"
            />
          ) : <p className="text-sm text-dim">График доказательства не сохранён.</p>}
        </div>
      </section>

      <section aria-labelledby="signal-articles-heading" className="card p-5">
        <h2 id="signal-articles-heading" className="card-title">Публикации-доказательства</h2>
        {detail.articles.length === 0 ? <p className="mt-2 text-sm text-dim">Ссылки на публикации не сохранены.</p> : (
          <ul className="mt-3 space-y-2">
            {detail.articles.map((article) => {
              const href = safeHttpUrl(article.url);
              const title = article.title || `Публикация #${article.id}`;
              return (
                <li key={article.id} className="rounded border border-line bg-panel2 px-3 py-3 text-sm">
                  {href ? <a href={href} target="_blank" rel="noopener noreferrer" className="font-semibold hover:text-accent">{title}</a> : <span className="font-semibold">{title}</span>}
                  <div className="mt-1 text-xs text-dim">
                    {article.source_name || "Источник не сохранён"}
                    {article.published_at && ` · ${fmtTime(article.published_at)}`}
                    {article.country_code && ` · ${article.country_code}`}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
        {detail.articles_page.truncated && (
          <p className="mt-3 text-xs text-cooling">
            Показана {detail.articles_page.returned} из {detail.articles_page.total} сохранённых публикаций.
          </p>
        )}
      </section>

      <div className="grid gap-4 md:grid-cols-2">
        <section aria-labelledby="signal-story-heading" className="card p-5">
          <h2 id="signal-story-heading" className="card-title">Связанный сюжет</h2>
          {detail.related_story ? (
            <div className="mt-3 text-sm">
              {storiesEnabled ? (
                <Link href={`/stories/${detail.related_story.id}`} className="font-semibold hover:text-accent">
                  {detail.related_story.title || `Сюжет #${detail.related_story.id}`}
                </Link>
              ) : <strong>{detail.related_story.title || `Сюжет #${detail.related_story.id}`}</strong>}
              {detail.related_story.summary && <p className="mt-1 text-xs leading-relaxed text-dim">{detail.related_story.summary}</p>}
            </div>
          ) : <p className="mt-2 text-sm text-dim">Связанный межстрановой сюжет не найден.</p>}
        </section>

        <section aria-labelledby="signal-countries-heading" className="card p-5">
          <h2 id="signal-countries-heading" className="card-title">Страны</h2>
          <ul className="mt-3 space-y-2 text-sm">
            {detail.countries.map((country) => (
              <li key={country.code}>
                <Link href={`/country/${country.code.toLowerCase()}`} className="font-semibold hover:text-accent">{country.name}</Link>
                {country.article_count != null && <span className="text-dim"> · {country.article_count} публикаций</span>}
              </li>
            ))}
          </ul>
        </section>
      </div>

      <section aria-labelledby="signal-evidence-heading" className="card p-5">
        <h2 id="signal-evidence-heading" className="card-title">Полнота доказательств</h2>
        <dl className="mt-3 grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
          {[
            ["ID доказательств", detail.evidence_truncation.evidence_ids],
            ["Публикации", detail.evidence_truncation.article_ids],
            ["Сюжеты", detail.evidence_truncation.story_ids],
            ["Точки RRI", detail.evidence_truncation.rri_points],
          ].map(([label, count]) => {
            const item = count as { total: number; returned: number; truncated: boolean };
            return <div key={label as string} className="rounded bg-panel2 p-3"><dt className="text-xs text-dim">{label as string}</dt><dd className="tnum mt-1 text-lg">{item.returned}/{item.total}</dd></div>;
          })}
        </dl>
      </section>

      {detail.limitations.length > 0 && (
        <section aria-labelledby="signal-limitations-heading" className="border-t border-line pt-5">
          <h2 id="signal-limitations-heading" className="card-title">Ограничения</h2>
          <ul className="mt-3 list-disc space-y-1 pl-5 text-sm leading-relaxed text-dim">
            {detail.limitations.map((limitation) => <li key={limitation}>{LIMITATION_LABEL[limitation] ?? readableKey(limitation)}</li>)}
          </ul>
        </section>
      )}
    </article>
  );
}
