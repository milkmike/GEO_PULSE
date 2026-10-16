"use client";

import { use, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowUpRight,
  CircleAlert,
  FileSearch,
  Gauge,
  LoaderCircle,
  Radio,
  UsersRound,
} from "lucide-react";
import PageShell from "@/components/ui/PageShell";
import StoryTimeline from "@/components/StoryTimeline";
import EarlyWarningPanel, { isPriorityRadarTrend } from "@/components/EarlyWarningPanel";
import { useFeatureFlags } from "@/components/FeatureFlagsProvider";
import { api } from "@/lib/api";
import { countryNameRu, pluralRu, severityRu, storySummaryRu } from "@/lib/format";
import type {
  RadarTrend,
  StoryArticleEvidence,
  StoryDetailResponse,
  StoryLifecycle,
} from "@/lib/types";
import { safeHttpUrl } from "@/lib/urls";

const RADAR_LIMIT = 3;
const RADAR_MAX_PAGES = 3;

const LIFECYCLE: Record<StoryLifecycle, string> = {
  emerging: "зарождается",
  developing: "развивается",
  escalating: "обостряется",
  cooling: "затухает",
  resolved: "завершён",
};

const GROUPING_REASON: Record<string, string> = {
  cross_country: "межстрановое покрытие",
  active_lifecycle: "сюжет продолжает развиваться",
  shared_entities: "совпадают ключевые сущности",
  shared_event: "публикации описывают одно событие",
  source_diversity: "сообщение подтверждено разными источниками",
  topic_overlap: "совпадает предмет публикаций",
  temporal_proximity: "публикации вышли в одном временном окне",
};

function groupingReason(reason: string): string {
  return GROUPING_REASON[reason] ?? "дополнительный признак группировки";
}

function isAbort(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
}

function signed(value: number): string {
  return new Intl.NumberFormat("ru-RU", {
    signDisplay: "always",
    minimumFractionDigits: 1,
    maximumFractionDigits: 1,
  }).format(value).replace("-", "−");
}

function compactDate(value: string | null): string {
  if (!value) return "дата не указана";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "дата не указана";
  return new Intl.DateTimeFormat("ru-RU", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "Europe/Moscow",
  }).format(date);
}

function entityHref(id: string, label: string): string {
  const params = new URLSearchParams({ entity_id: id, entity_label: label });
  return `/search?${params.toString()}`;
}

export default function StoryDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { searchNavigation, signalDetail, earlyWarningRadar } = useFeatureFlags();
  const { id: rawId } = use(params);
  const storyId = /^\d+$/.test(rawId) && Number(rawId) > 0 ? Number(rawId) : null;
  const [story, setStory] = useState<StoryDetailResponse | null>(null);
  const [articles, setArticles] = useState<StoryArticleEvidence[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(storyId !== null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [radarTrends, setRadarTrends] = useState<RadarTrend[]>([]);
  const loadMoreController = useRef<AbortController | null>(null);
  const loadedStoryId = story?.id ?? null;

  useEffect(() => {
    if (storyId === null) {
      setLoading(false);
      setError("Некорректный идентификатор сюжета.");
      return;
    }
    const controller = new AbortController();
    loadMoreController.current?.abort();
    loadMoreController.current = null;
    setLoadingMore(false);
    setLoading(true);
    setError(null);
    setStory(null);
    setArticles([]);
    setNextCursor(null);
    api.story(storyId, null, 25, controller.signal)
      .then((payload) => {
        if (controller.signal.aborted) return;
        setStory(payload);
        setArticles(payload.articles);
        setNextCursor(payload.articles_next_cursor);
      })
      .catch((reason: unknown) => {
        if (!isAbort(reason)) setError("Не удалось загрузить досье сюжета.");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => {
      controller.abort();
      loadMoreController.current?.abort();
      loadMoreController.current = null;
    };
  }, [reload, storyId]);

  // Priority radar trends linked to this story, paged like EarlyWarningPanel.
  // Without any, the trends section is omitted instead of printing an empty state.
  useEffect(() => {
    setRadarTrends([]);
    if (!earlyWarningRadar || loadedStoryId === null) return;
    const controller = new AbortController();
    (async () => {
      const found = new Map<string, RadarTrend>();
      let cursor: string | null = null;
      for (let page = 0; page < RADAR_MAX_PAGES; page += 1) {
        const payload = await api.radar({ storyId: loadedStoryId, limit: 12 }, cursor, controller.signal);
        for (const trend of payload.items) {
          if (isPriorityRadarTrend(trend)) found.set(trend.public_id, trend);
        }
        if (found.size >= RADAR_LIMIT || payload.next_cursor === null) break;
        cursor = payload.next_cursor;
      }
      if (!controller.signal.aborted) setRadarTrends([...found.values()]);
    })().catch(() => {}); // supplementary section: stays hidden on failure
    return () => controller.abort();
  }, [earlyWarningRadar, loadedStoryId]);

  async function loadMore() {
    if (!storyId || !nextCursor || loadingMore) return;
    const controller = new AbortController();
    loadMoreController.current?.abort();
    loadMoreController.current = controller;
    setLoadingMore(true);
    setError(null);
    try {
      const cursor = nextCursor;
      const payload = await api.story(storyId, cursor, 25, controller.signal);
      if (controller.signal.aborted) return;
      setArticles((current) => {
        const ids = new Set(current.map((article) => article.article_id));
        return [...current, ...payload.articles.filter((article) => !ids.has(article.article_id))];
      });
      setNextCursor(payload.articles_next_cursor);
    } catch (reason: unknown) {
      if (!isAbort(reason)) setError("Следующую страницу источников не удалось загрузить.");
    } finally {
      if (loadMoreController.current === controller) {
        loadMoreController.current = null;
        setLoadingMore(false);
      }
    }
  }

  if (loading) {
    return (
      <PageShell active="/stories" width="read">
        <div role="status" className="py-20 text-center text-dim">
          <LoaderCircle aria-hidden="true" size={24} className="mx-auto mb-3 animate-spin motion-reduce:animate-none" />
          Собираем доказательное досье…
        </div>
      </PageShell>
    );
  }

  if (error && !story) {
    return (
      <PageShell active="/stories" width="read">
        <div role="alert" className="mx-auto mt-16 max-w-xl border-y border-accent/40 py-10 text-center">
          <CircleAlert aria-hidden="true" size={22} className="mx-auto mb-3 text-accent" />
          <p className="text-sm text-dim">{error}</p>
          {storyId && (
            <button type="button" onClick={() => setReload((value) => value + 1)} className="mt-4 min-h-11 text-accent underline underline-offset-4">
              повторить
            </button>
          )}
        </div>
      </PageShell>
    );
  }

  if (!story) return null;
  const rriShifts = story.rri_shifts?.length
    ? story.rri_shifts
    : story.latest_rri_shift
      ? [story.latest_rri_shift]
      : [];

  return (
    <PageShell active="/stories" width="read">

      <header className="reveal reveal-1 border-b border-line pb-8 pt-10">
        <div className="flex flex-wrap items-center gap-2 text-xs text-dim">
          <Link href="/stories" className="min-h-11 py-3 text-accent hover:text-fg sm:min-h-0 sm:py-0">сюжеты</Link>
          <span aria-hidden="true">/</span>
          <span className="text-dim">досье {String(story.id).padStart(4, "0")}</span>
          <span className="rounded-full border border-line px-2 py-0.5 text-fg">{LIFECYCLE[story.lifecycle]}</span>
        </div>
        <h1 className="display mt-3 max-w-5xl text-h1 leading-[1.02] sm:text-h1">{story.title_ru}</h1>
        {story.summary && <p className="mt-5 max-w-3xl text-base leading-7 text-dim">{storySummaryRu(story.summary)}</p>}
        <div className="mt-5 flex flex-wrap gap-x-5 gap-y-2 border-l border-accent/70 pl-4 text-xs text-dim">
          <span><b className="tnum text-fg">{story.article_count}</b> {pluralRu(story.article_count, "публикация", "публикации", "публикаций")}</span>
          <span><b className="tnum text-fg">{story.source_count}</b> {pluralRu(story.source_count, "источник", "источника", "источников")}</span>
          <span><b className="tnum text-fg">{story.country_count}</b> {pluralRu(story.country_count, "страна", "страны", "стран")}</span>
          <span>{compactDate(story.first_seen)} — {compactDate(story.last_seen)}</span>
        </div>
      </header>

      {story.redirected_from_story_id && (
        <p className="mt-4 rounded-md border border-line bg-panel2 px-3 py-2 text-xs text-dim">
          Стабильный адрес сохранён: сюжет {story.redirected_from_story_id} объединён с досье {story.id}.
        </p>
      )}

      <div className="reveal reveal-2 mt-6 grid gap-3 lg:grid-cols-12">
        <section className="card p-4 lg:col-span-8" aria-labelledby="story-timeline-title">
          <p className="section-num">Хронология</p>
          <h2 id="story-timeline-title" className="display mb-6 mt-1 text-h2">Как развивался сюжет</h2>
          <StoryTimeline events={story.events} articles={articles} />
        </section>

        <aside className="space-y-3 lg:col-span-4">
          <section className="card p-4" aria-labelledby="story-countries-title">
            <div className="flex items-center gap-2 text-dim"><UsersRound aria-hidden="true" size={14} /><p className="card-title">страновые срезы</p></div>
            <h2 id="story-countries-title" className="sr-only">Страновые срезы</h2>
            <div className="mt-3 divide-y divide-line">
              {story.countries.map((country) => {
                const url = safeHttpUrl(country.primary_url);
                return (
                  <div key={country.country_code} className="py-3 first:pt-0 last:pb-0">
                    <div className="flex items-baseline justify-between gap-3 text-sm">
                      <Link href={`/country/${country.country_code}`} className="font-semibold hover:text-accent">{country.country_name}</Link>
                      <span className="text-xs text-dim">тон {country.media_tone == null ? "—" : signed(country.media_tone)}</span>
                    </div>
                    <p className="mt-1 text-xs text-dim">{country.article_count} {pluralRu(country.article_count, "публикация", "публикации", "публикаций")} · {country.source_count} {pluralRu(country.source_count, "источник", "источника", "источников")}</p>
                    {url && (
                      <a href={url} target="_blank" rel="noopener noreferrer" aria-label={`${country.country_name} · первоисточник`} className="mt-1 inline-flex min-h-11 items-center gap-1 text-xs text-accent sm:min-h-0">
                        первоисточник <ArrowUpRight aria-hidden="true" size={11} />
                      </a>
                    )}
                  </div>
                );
              })}
            </div>
          </section>

          <section className="card p-4" aria-labelledby="story-entities-title">
            <p className="card-title text-dim">сущности</p>
            <h2 id="story-entities-title" className="sr-only">Сущности сюжета</h2>
            {story.entities.length ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {story.entities.map((entity) => (
                  searchNavigation ? (
                    <Link key={entity.entity_id} href={entityHref(entity.entity_id, entity.canonical_name)} className="inline-flex min-h-11 items-center rounded-full border border-line px-3 text-xs hover:border-accent hover:text-accent sm:min-h-0 sm:py-1.5">
                      {entity.canonical_name} <span className="ml-1 text-dim">×{entity.mentions}</span>
                    </Link>
                  ) : (
                    <span key={entity.entity_id} title="Поиск новостей пока отключён" className="inline-flex min-h-11 items-center rounded-full border border-line px-3 text-xs sm:min-h-0 sm:py-1.5">
                      {entity.canonical_name} <span className="ml-1 text-dim">×{entity.mentions}</span>
                    </span>
                  )
                ))}
              </div>
            ) : <p className="mt-2 text-xs text-dim">Распознанных сущностей пока нет.</p>}
          </section>
        </aside>
      </div>

      <div className="reveal reveal-3 mt-3 grid gap-3 lg:grid-cols-2">
        <section className="card p-4" aria-labelledby="story-evidence-title">
          <div className="flex items-center gap-2 text-dim"><FileSearch aria-hidden="true" size={14} /><p className="card-title">почему материалы объединены</p></div>
          <h2 id="story-evidence-title" className="sr-only">Доказательства группировки</h2>
          <p className="mt-3 text-sm text-fg">Достоверность группировки · <span className="tnum font-semibold">{Math.round(story.clustering_confidence * 100)}%</span></p>
          <ul className="mt-3 space-y-2 text-xs text-dim">
            {story.why_included.length ? story.why_included.map((reason) => <li key={reason}>— {groupingReason(reason)}</li>) : <li>— доказательства объединения не опубликованы</li>}
          </ul>
          {Object.keys(story.evidence).length > 0 && (
            <details className="mt-4 border-t border-line pt-3 text-xs text-dim">
              <summary className="min-h-11 cursor-pointer py-3 text-accent sm:min-h-0 sm:py-0">технический след группировки</summary>
              <pre className="mt-3 max-h-64 overflow-auto whitespace-pre-wrap break-words rounded bg-bg/60 p-3 text-xs leading-4">{JSON.stringify(story.evidence, null, 2)}</pre>
            </details>
          )}
        </section>

        <section className="card p-4" aria-labelledby="story-context-title">
          <div className="flex items-center gap-2 text-dim"><Gauge aria-hidden="true" size={14} /><p className="card-title">контекст и ограничения</p></div>
          <h2 id="story-context-title" className="sr-only">Контекст и ограничения</h2>
          {rriShifts.length ? (
            <div className="mt-3 rounded-md border border-accent/30 bg-bg/50 p-3">
              <p className="text-sm font-semibold">Временной контекст RRI · значимые сдвиги рядом с сюжетом</p>
              <ul className="mt-2 space-y-2">
                {rriShifts.map((shift) => (
                  <li key={`${shift.country_code}-${shift.at}`} className="border-t border-line pt-2 first:border-0 first:pt-0">
                    <p className="text-xs font-semibold">{shift.country_name} · <span className="tnum">{signed(shift.delta_24h)}</span></p>
                    <p className="mt-1 text-xs leading-5 text-dim">{shift.limitation}</p>
                  </li>
                ))}
              </ul>
            </div>
          ) : <p className="mt-3 text-xs text-dim">Сопоставимого изменения RRI в окне сюжета нет.</p>}
          <ul className="mt-4 space-y-2 text-xs leading-5 text-dim">
            <li>Алгоритмическая группировка может объединить похожие, но независимые события.</li>
            <li>Неполное покрытие источников может менять страновой тон и объём сюжета.</li>
            <li>Временное совпадение индекса и публикаций само по себе не устанавливает причину.</li>
          </ul>
        </section>
      </div>

      {earlyWarningRadar && radarTrends.length > 0 && (
        <div className="mt-3">
          <EarlyWarningPanel trends={radarTrends} filters={{ storyId: story.id }} title="Аналитические тренды" limit={RADAR_LIMIT} />
        </div>
      )}

      <section className="mt-3 card p-4" aria-labelledby="story-signals-title">
        <div className="flex items-baseline gap-2"><Radio aria-hidden="true" size={14} className="text-cooling" /><h2 id="story-signals-title" className="card-title">связанные сигналы · {story.linked_signal_count}</h2></div>
        {story.linked_signals.length ? (
          <ul className="mt-3 grid gap-2 md:grid-cols-2">
            {story.linked_signals.map((signal) => (
              <li key={signal.id} className="rounded-md border border-line bg-panel2/50 p-3">
                {signalDetail ? (
                  <Link href={`/signals/${signal.id}`} className="inline-flex min-h-11 items-center text-sm font-semibold hover:text-accent sm:min-h-0">{signal.title}</Link>
                ) : (
                  <span className="inline-flex min-h-11 items-center text-sm font-semibold sm:min-h-0" title="Детали сигнала будут доступны после включения раздела">
                    {signal.title}
                  </span>
                )}
                <p className="mt-1 text-xs text-dim">{severityRu(signal.severity)} · достоверность {Math.round(signal.confidence * 100)}% · {signal.relation === "explicit_story_evidence" ? "прямая связь" : "общие публикации"}</p>
              </li>
            ))}
          </ul>
        ) : <p className="mt-2 text-xs text-dim">Доказательно связанных сигналов пока нет.</p>}
      </section>

      <section className="mt-3 card p-4" aria-labelledby="story-sources-title" aria-live="polite">
        <div className="flex items-baseline justify-between gap-3">
          <h2 id="story-sources-title" className="card-title">источники сюжета</h2>
          <span className="text-xs text-dim">показано {articles.length} из {story.article_count}</span>
        </div>
        <ol className="mt-3 divide-y divide-line">
          {articles.map((article) => {
            const url = safeHttpUrl(article.url);
            return (
              <li key={article.article_id} className="py-3 first:pt-0 last:pb-0">
                <p className="text-sm text-fg">{article.title || "Материал без заголовка"}</p>
                <p className="mt-1 text-xs text-dim">{article.source} · {countryNameRu(article.country_code)} · достоверность связи {Math.round(article.membership_confidence * 100)}%</p>
                {url && <a href={url} target="_blank" rel="noopener noreferrer" aria-label={`Открыть источник: ${article.title ?? article.source}`} className="mt-1 inline-flex min-h-11 items-center gap-1 text-xs text-accent sm:min-h-0">открыть <ArrowUpRight aria-hidden="true" size={11} /></a>}
              </li>
            );
          })}
        </ol>
        {articles.length === 0 && <p className="mt-3 text-xs text-dim">Ссылки на публикации пока не сохранены.</p>}
        {nextCursor && (
          <button type="button" onClick={loadMore} disabled={loadingMore} className="mt-5 inline-flex min-h-11 items-center gap-2 rounded-md border border-line px-4 text-xs hover:border-accent disabled:opacity-50">
            {loadingMore && <LoaderCircle aria-hidden="true" size={13} className="animate-spin motion-reduce:animate-none" />}
            {loadingMore ? "загружаем" : "ещё источники"}
          </button>
        )}
        {error && <p role="alert" className="mt-3 text-xs text-hostile">{error}</p>}
      </section>
    </PageShell>
  );
}
