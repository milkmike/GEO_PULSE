"use client";

import Link from "next/link";
import Plot from "./Plot";
import type { SignalArticleReference, SignalDetail } from "@/lib/types";
import { eventTypeRu, SIGNAL_RU, sourceTierRu } from "@/lib/format";
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

const FIELD_LABEL: Record<string, string> = {
  type: "Тип базового значения",
  status: "Статус сохранения",
  event_key: "Ключ события",
  distinct_tiers: "Число разных тиров",
  tiers: "Тиры источников",
  article_count: "Число публикаций",
  articles: "Число публикаций",
  average_sentiment: "Средняя тональность",
  avg_sentiment: "Средняя тональность",
  maximum_action_level: "Максимальный уровень действия",
  max_action_level: "Максимальный уровень действия",
  loud_articles: "Публикации вне официальных источников",
  quiet_articles: "Публикации официальных источников",
  hours_silent: "Часов без официальной реакции",
  articles_24h: "Публикаций за 24 часа",
  ratio: "Отношение к базовой линии",
  daily_average: "Среднее число публикаций в день",
  baseline_daily: "Среднее число публикаций в день",
  comparison_days: "Дней в базовой линии",
  tone: "Текущий тон",
  z_score: "Z-оценка",
  mean: "Среднее значение",
  mean_90d: "Среднее за 90 дней",
  standard_deviation: "Стандартное отклонение",
  std: "Обычное отклонение",
  sample_days: "Дней в выборке",
  excluded_recent_days: "Исключено последних дней",
  share: "Доля повестки",
  baseline_share: "Базовая доля повестки",
  daily_volume: "Публикаций в день",
  volume: "Публикаций в день",
  score: "Значение RRI",
  delta: "Изменение",
  delta_24h: "Изменение RRI",
  level: "Уровень RRI",
  comparison_hours: "Интервал сравнения, часов",
  time: "Время точки",
  action_level: "Уровень действия",
  event_type: "Тип события",
  tier: "Тип источника",
  sentiment: "Тональность",
  reprint_count: "Число перепечаток",
  currency: "Валюта",
  change_1d_percent: "Изменение курса за день, %",
  change_1d_pct: "Изменение курса за день, %",
  rate_to_rub: "Курс к рублю",
  media_preceded: "Медиасигнал появился раньше",
  preceding_media_signal_count: "Число предшествующих медиасигналов",
  media_lookback_hours: "Глубина поиска медиасигналов, часов",
  countries: "Страны",
  day: "Дата снимка",
  new_targets: "Новых санкционных целей",
  target_count: "Всего санкционных целей",
  lists_count: "Санкционных программ",
  last_change: "Последнее обновление",
  previous_target_count: "Целей в предыдущем снимке",
  minimum_distinct_tiers: "Минимум разных тиров",
  minimum_loud_articles: "Минимум публикаций вне официальных источников",
  maximum_quiet_articles: "Максимум публикаций официальных источников",
  minimum_silence_hours: "Минимум часов без официальной реакции",
  minimum_articles_24h: "Минимум публикаций за 24 часа",
  minimum_baseline_ratio: "Минимальное отношение к базовой линии",
  absolute_z_score_min: "Минимальная абсолютная z-оценка",
  standard_deviation_floor: "Нижняя граница стандартного отклонения",
  minimum_share_ratio: "Минимальный рост доли повестки",
  minimum_daily_volume: "Минимальный дневной объём",
  absolute_delta_min: "Минимальный абсолютный сдвиг",
  absolute_delta_sanity_max: "Максимальный допустимый сдвиг",
  minimum_action_level: "Минимальный уровень действия",
  absolute_daily_change_percent_min: "Минимальное дневное изменение курса, %",
  minimum_new_targets: "Минимум новых санкционных целей",
  enabled: "Включено",
  official_or_mainstream_sources_available: "Доступны официальные или крупные СМИ",
};

const VALUE_LABEL: Record<string, string> = {
  not_applicable: "сравнение не требуется",
  active_source_availability: "доступность активных источников",
  rolling_daily_average: "скользящее среднее по дням",
  historical_tone_distribution: "историческое распределение тона",
  historical_coverage_share: "историческая доля повестки",
  rri_point: "Сохранённая точка RRI",
  previous_fx_rate: "предыдущий курс валюты",
  previous_sanctions_snapshot: "предыдущий снимок санкционного реестра",
  available: "сохранено",
  missing: "не найдено",
  not_persisted: "не сохранялось",
  reconstructed_from_signal_payload: "Восстановлено из payload сигнала",
  persisted_trigger_window: "сохранённое окно срабатывания",
  exact: "точно сохранено",
  unknown: "неизвестно",
  active: "активен",
  expired: "истёк",
  ally: "союзник",
  partner: "партнёр",
  neutral: "нейтральный",
  cooling: "охлаждение",
  tension: "напряжение",
  hostile: "враждебный",
};

const readableKey = (key: string) => FIELD_LABEL[key] ?? `Параметр доказательства «${key}»`;

function readableValue(value: string): string {
  return VALUE_LABEL[value] ?? value;
}

function displayValue(value: unknown, key?: string): string {
  if (value === null || value === undefined || value === "") return "не сохранено";
  if (typeof value === "boolean") return value ? "да" : "нет";
  if (typeof value === "number") return value.toLocaleString("ru-RU", { maximumFractionDigits: 3 });
  if (typeof value === "string") {
    if (key === "event_type") return eventTypeRu(value);
    if (key === "tier") return sourceTierRu(value);
    return readableValue(value);
  }
  if (Array.isArray(value)) {
    const itemKey = key === "tiers" ? "tier" : undefined;
    return value.map((item) => displayValue(item, itemKey)).join(", ");
  }
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
              <strong className="tnum text-right text-ru-white">{displayValue(item, key)}</strong>
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

const stateLabel = (status: SignalDetail["state"]["status"]) =>
  VALUE_LABEL[status] ?? `статус не распознан (${status})`;

function ArticleReferenceRow({ article }: { article: SignalArticleReference }) {
  const href = safeHttpUrl(article.url);
  const title = article.title || `Публикация #${article.id}`;

  return (
    <li className="rounded border border-line bg-panel2 px-3 py-3 text-sm">
      {href ? (
        <a href={href} target="_blank" rel="noopener noreferrer" className="font-semibold hover:text-accent">
          {title}
        </a>
      ) : <span className="font-semibold">{title}</span>}
      {!href && (
        <div className="mt-1 text-xs text-cooling">Ссылка на первоисточник не сохранена.</div>
      )}
      <div className="mt-1 text-xs text-dim">
        {article.source_name || "Источник не сохранён"}
        {article.published_at && ` · ${fmtTime(article.published_at)}`}
        {article.country_code && ` · ${article.country_code}`}
      </div>
    </li>
  );
}

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
            {detail.state.active ? "● " : ""}{stateLabel(detail.state.status)}
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
        <section role="region" aria-label="Состояние сигнала" className="mt-4 rounded-lg border border-line bg-panel2 p-3">
          <dl className="grid gap-2 text-xs sm:grid-cols-3">
            <div>
              <dt className="text-dim">Создан</dt>
              <dd className="mt-1 text-ru-white">
                {detail.state.created_at
                  ? <time dateTime={detail.state.created_at}>{fmtTime(detail.state.created_at)}</time>
                  : "не сохранено"}
              </dd>
            </div>
            <div>
              <dt className="text-dim">Действует до</dt>
              <dd className="mt-1 text-ru-white">
                {detail.state.expires_at
                  ? <time dateTime={detail.state.expires_at}>{fmtTime(detail.state.expires_at)}</time>
                  : "не сохранено"}
              </dd>
            </div>
            <div>
              <dt className="text-dim">Текущий статус</dt>
              <dd className="mt-1 text-ru-white">{stateLabel(detail.state.status)}</dd>
            </div>
          </dl>
        </section>
      </header>

      <section aria-labelledby="signal-rule-heading" className="card p-5">
        <h2 id="signal-rule-heading" className="card-title">Как сработал детектор</h2>
        <p className="mt-2 text-sm leading-relaxed text-dim">
          {detail.rule.description || "Описание исторического правила не сохранено."}
        </p>
        <div className="mt-2 text-xs text-dim">
          Детектор {SIGNAL_RU[detail.rule.detector] ?? `неизвестный (${detail.rule.detector})`} · версия {detail.rule.version}
        </div>
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
              : Object.entries(detail.rule.current_rule_reference).map(([key, value]) => `${readableKey(key)}: ${displayValue(value, key)}`).join(" · ")}</p>
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
            {detail.values.window.basis
              ? `Основание: ${detail.values.window.basis === "not_persisted" ? "окно детектора не сохранялось" : readableValue(detail.values.window.basis)}`
              : ""}
            {detail.values.window.status
              ? ` · Статус: ${detail.values.window.status === "unknown" ? "статус окна неизвестен" : readableValue(detail.values.window.status)}`
              : ""}
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

      {(detail.articles.length > 0 || !detail.context_preview) && (
        <section aria-labelledby="signal-articles-heading" className="card p-5">
          <h2 id="signal-articles-heading" className="card-title">Публикации-доказательства</h2>
          {detail.articles.length === 0 ? <p className="mt-2 text-sm text-dim">Ссылки на публикации не сохранены.</p> : (
            <ul className="mt-3 space-y-2">
              {detail.articles.map((article) => (
                <ArticleReferenceRow key={article.id} article={article} />
              ))}
            </ul>
          )}
          {detail.articles_page.truncated && (
            <p className="mt-3 text-xs text-cooling">
              Показана {detail.articles_page.returned} из {detail.articles_page.total} сохранённых публикаций.
            </p>
          )}
        </section>
      )}

      {detail.context_preview && (
        <section aria-labelledby="signal-context-heading" className="card border-cooling/50 bg-cooling/5 p-5">
          <h2 id="signal-context-heading" className="card-title">Публикации в окне сигнала</h2>
          {detail.context_preview.window_start && detail.context_preview.window_end && (
            <p className="mt-2 text-xs text-dim">
              Окно контекста: {fmtTime(detail.context_preview.window_start)} — {fmtTime(detail.context_preview.window_end)}
            </p>
          )}
          {detail.context_preview.kind === "unavailable" ? (
            <p className="mt-2 text-sm text-dim">Новостной контекст для этого сигнала недоступен.</p>
          ) : detail.context_preview.articles.length === 0 ? (
            <p className="mt-2 text-sm text-dim">В сохранённом 72-часовом окне релевантные публикации не найдены.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {detail.context_preview.articles.map((article) => (
                <ArticleReferenceRow key={article.id} article={article} />
              ))}
            </ul>
          )}
          {detail.context_preview.kind === "context" && (
            <>
              <p className="mt-3 text-xs text-cooling">
                Показано {detail.context_preview.articles.length} из {detail.context_preview.total}
              </p>
              <p className="mt-2 text-xs text-dim">
                Отобраны по уровню события, выраженности тона, числу перепечаток и времени публикации.
              </p>
              <p className="mt-2 text-sm leading-relaxed text-dim">
                Публикации отобраны в 72-часовом окне наблюдаемого периода как возможный контекст. Они не доказывают причину сдвига.
              </p>
            </>
          )}
        </section>
      )}

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
            {detail.limitations.map((limitation) => (
              <li key={limitation}>
                {LIMITATION_LABEL[limitation]
                  ?? (/\s/u.test(limitation) ? limitation : `Ограничение детектора (код: ${limitation})`)}
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}
