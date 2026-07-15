"use client";

import {
  FormEvent,
  KeyboardEvent,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Filter, LoaderCircle, Network, Search, X } from "lucide-react";
import SiteHeader from "@/components/SiteHeader";
import StoryCard from "@/components/StoryCard";
import { api } from "@/lib/api";
import type {
  EntitySuggestion,
  Meta,
  StoryCoverage,
  StoriesRequest,
  StoryLifecycle,
  StoryListItem,
} from "@/lib/types";

type Period = "7d" | "30d" | "90d" | "all";
type Draft = {
  country: string;
  topic: string;
  lifecycle: "" | StoryLifecycle;
  entity_id: string;
  entity_label: string;
  period: Period;
};

const PERIOD_DAYS: Record<Exclude<Period, "all">, number> = {
  "7d": 7,
  "30d": 30,
  "90d": 90,
};

const LIFECYCLES: { value: StoryLifecycle; label: string }[] = [
  { value: "emerging", label: "зарождается" },
  { value: "developing", label: "развивается" },
  { value: "escalating", label: "обостряется" },
  { value: "cooling", label: "затухает" },
  { value: "resolved", label: "завершён" },
];

function periodFromParams(params: URLSearchParams): Period {
  const value = params.get("period");
  return value === "7d" || value === "90d" || value === "all" ? value : "30d";
}

function draftFromParams(params: URLSearchParams): Draft {
  const lifecycle = params.get("lifecycle") ?? "";
  return {
    country: params.get("country") ?? "",
    topic: params.get("topic") ?? "",
    lifecycle: LIFECYCLES.some((item) => item.value === lifecycle)
      ? lifecycle as StoryLifecycle
      : "",
    entity_id: params.get("entity_id") ?? "",
    entity_label: params.get("entity_label") ?? "",
    period: periodFromParams(params),
  };
}

function periodWindow(period: Period): Pick<StoriesRequest, "date_from" | "date_to"> {
  if (period === "all") return {};
  const now = new Date();
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  start.setUTCDate(start.getUTCDate() - PERIOD_DAYS[period]);
  const end = new Date(Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    now.getUTCDate(),
    23,
    59,
    59,
    999,
  ));
  return { date_from: start.toISOString(), date_to: end.toISOString() };
}

function requestFromParams(params: URLSearchParams): StoriesRequest {
  const request: StoriesRequest = { limit: 20, ...periodWindow(periodFromParams(params)) };
  const country = params.get("country")?.trim();
  const topic = params.get("topic")?.trim();
  const lifecycle = params.get("lifecycle")?.trim();
  const entityId = params.get("entity_id")?.trim();
  if (country) request.country = country.toUpperCase();
  if (topic) request.topic = topic;
  if (LIFECYCLES.some((item) => item.value === lifecycle)) {
    request.lifecycle = lifecycle as StoryLifecycle;
  }
  if (entityId) request.entity_id = entityId;
  return request;
}

function isAbort(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
}

function periodLabel(period: Period): string {
  if (period === "all") return "всё доступное время";
  return `последние ${PERIOD_DAYS[period]} дней`;
}

function shortDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: "UTC",
  }).format(date);
}

function StoriesPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const paramsKey = searchParams.toString();
  const params = useMemo(() => new URLSearchParams(paramsKey), [paramsKey]);
  const needsCanonicalPeriod = !params.has("period");
  const request = useMemo(() => requestFromParams(params), [params]);
  const [draft, setDraft] = useState<Draft>(() => draftFromParams(params));
  const [meta, setMeta] = useState<Meta | null>(null);
  const [stories, setStories] = useState<StoryListItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const [entityQuery, setEntityQuery] = useState(draft.entity_label);
  const [suggestions, setSuggestions] = useState<EntitySuggestion[]>([]);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [activeSuggestion, setActiveSuggestion] = useState(-1);
  const [coverage, setCoverage] = useState<StoryCoverage | null>(null);
  const loadMoreController = useRef<AbortController | null>(null);
  const activeParamsKey = useRef(paramsKey);
  activeParamsKey.current = paramsKey;

  useEffect(() => {
    api.meta().then(setMeta).catch(() => setMeta(null));
  }, []);

  useEffect(() => {
    const nextDraft = draftFromParams(new URLSearchParams(paramsKey));
    setDraft(nextDraft);
    setEntityQuery(nextDraft.entity_label);
    setSuggestions([]);
    setSuggestionsOpen(false);
    setActiveSuggestion(-1);
  }, [paramsKey]);

  useEffect(() => {
    if (!needsCanonicalPeriod) return;
    const canonical = new URLSearchParams(paramsKey);
    canonical.set("period", "30d");
    router.replace(`/stories?${canonical.toString()}`);
  }, [needsCanonicalPeriod, paramsKey, router]);

  useEffect(() => {
    loadMoreController.current?.abort();
    loadMoreController.current = null;
    setLoadingMore(false);
    return () => loadMoreController.current?.abort();
  }, [paramsKey]);

  useEffect(() => {
    const query = entityQuery.trim();
    if (query.length < 2 || query === draft.entity_label) {
      setSuggestions([]);
      setSuggestionsOpen(false);
      setActiveSuggestion(-1);
      setSuggestionsLoading(false);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSuggestionsLoading(true);
      api.entitySuggestions(query, controller.signal)
        .then((payload) => {
          if (controller.signal.aborted) return;
          setSuggestions(payload.items);
          setSuggestionsOpen(payload.items.length > 0);
          setActiveSuggestion(-1);
        })
        .catch((reason: unknown) => {
          if (!isAbort(reason)) {
            setSuggestions([]);
            setSuggestionsOpen(false);
          }
        })
        .finally(() => {
          if (!controller.signal.aborted) setSuggestionsLoading(false);
        });
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [draft.entity_label, entityQuery]);

  useEffect(() => {
    if (needsCanonicalPeriod) return;
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setStories([]);
    setNextCursor(null);
    setCoverage(null);
    api.stories(request, null, controller.signal)
      .then((payload) => {
        if (controller.signal.aborted || activeParamsKey.current !== paramsKey) return;
        setStories(payload.stories);
        setNextCursor(payload.next_cursor);
        setCoverage(payload.coverage ?? null);
      })
      .catch((reason: unknown) => {
        if (!isAbort(reason)) {
          setError("Не удалось загрузить сюжеты. Уже собранные новости остаются доступны через поиск.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [needsCanonicalPeriod, paramsKey, reload, request]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const next = new URLSearchParams();
    if (draft.country) next.set("country", draft.country.toUpperCase());
    if (draft.topic) next.set("topic", draft.topic);
    if (draft.lifecycle) next.set("lifecycle", draft.lifecycle);
    if (draft.entity_id) {
      next.set("entity_id", draft.entity_id);
      if (draft.entity_label) next.set("entity_label", draft.entity_label);
    }
    next.set("period", draft.period);
    router.push(`/stories?${next.toString()}`);
  }

  function chooseEntity(entity: EntitySuggestion) {
    setDraft((current) => ({
      ...current,
      entity_id: entity.id,
      entity_label: entity.label,
    }));
    setEntityQuery(entity.label);
    setSuggestionsOpen(false);
    setActiveSuggestion(-1);
  }

  function entityKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Escape") {
      setSuggestionsOpen(false);
      setActiveSuggestion(-1);
      return;
    }
    if (!suggestions.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSuggestionsOpen(true);
      setActiveSuggestion((current) => (
        !suggestionsOpen || current < 0 ? 0 : (current + 1) % suggestions.length
      ));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSuggestionsOpen(true);
      setActiveSuggestion((current) => (
        !suggestionsOpen || current < 0
          ? suggestions.length - 1
          : current === 0
            ? suggestions.length - 1
            : current - 1
      ));
    } else if (event.key === "Enter" && suggestionsOpen && activeSuggestion >= 0) {
      event.preventDefault();
      chooseEntity(suggestions[activeSuggestion]);
    }
  }

  async function more() {
    if (!nextCursor || loadingMore) return;
    loadMoreController.current?.abort();
    const controller = new AbortController();
    const requestKey = paramsKey;
    loadMoreController.current = controller;
    setLoadingMore(true);
    setError(null);
    try {
      const payload = await api.stories(request, nextCursor, controller.signal);
      if (controller.signal.aborted || activeParamsKey.current !== requestKey) return;
      setStories((current) => {
        const ids = new Set(current.map((story) => story.id));
        return [...current, ...payload.stories.filter((story) => !ids.has(story.id))];
      });
      setNextCursor(payload.next_cursor);
    } catch (reason: unknown) {
      if (!isAbort(reason) && activeParamsKey.current === requestKey) {
        setError("Следующую страницу не удалось загрузить. Уже показанные сюжеты сохранены.");
      }
    } finally {
      if (loadMoreController.current === controller && activeParamsKey.current === requestKey) {
        loadMoreController.current = null;
        setLoadingMore(false);
      }
    }
  }

  const selectedPeriodLabel = periodLabel(periodFromParams(params));
  const indexedFrom = shortDate(coverage?.available_from);
  const indexedTo = shortDate(coverage?.available_to);

  return (
    <main className="mx-auto max-w-[1240px] px-3 pb-16">
      <SiteHeader active="/stories" />

      <header className="reveal reveal-1 grid gap-6 pb-8 pt-10 lg:grid-cols-[1fr_22rem] lg:items-end">
        <div>
          <p className="section-num">СЮЖЕТЫ / 02</p>
          <h1 className="display mt-2 max-w-4xl text-[40px] leading-[0.98] sm:text-[54px]">
            Одна история — несколько стран
          </h1>
        </div>
        <p className="border-l border-ru-blue/70 pl-4 text-[12px] leading-5 text-dim">
          Мы соединяем публикации только по доказуемому сходству событий, сущностей и времени.
          Сдвиги RRI показаны как соседний контекст, а не как установленная причина.
        </p>
      </header>

      <form onSubmit={submit} className="reveal reveal-2 card relative overflow-visible px-4 py-4 sm:px-5">
        <span className="tricolor absolute inset-x-0 top-0 !h-[2px]" aria-hidden="true" />
        <div className="mb-3 flex items-center gap-2 text-dim">
          <Filter aria-hidden="true" size={13} />
          <span className="card-title">линза сюжетов</span>
        </div>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <label>
            <span className="mb-1 block text-[10px] uppercase tracking-[0.08em] text-dim">Страна</span>
            <select
              aria-label="Страна"
              value={draft.country}
              onChange={(event) => setDraft((current) => ({ ...current, country: event.target.value }))}
              className="min-h-11 w-full rounded-md border border-line bg-panel2 px-2.5 text-base text-fg outline-none focus:border-accent sm:text-xs"
            >
              <option value="">Все страны</option>
              {draft.country && !meta?.countries.some((country) => country.code === draft.country) && (
                <option value={draft.country}>{draft.country}</option>
              )}
              {meta?.countries.map((country) => (
                <option key={country.code} value={country.code}>{country.name}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="mb-1 block text-[10px] uppercase tracking-[0.08em] text-dim">Тема</span>
            <select
              aria-label="Тема"
              value={draft.topic}
              onChange={(event) => setDraft((current) => ({ ...current, topic: event.target.value }))}
              className="min-h-11 w-full rounded-md border border-line bg-panel2 px-2.5 text-base text-fg outline-none focus:border-accent sm:text-xs"
            >
              <option value="">Все темы</option>
              {draft.topic && !meta?.topics[draft.topic] && <option value={draft.topic}>{draft.topic}</option>}
              {meta && Object.entries(meta.topics).map(([key, label]) => (
                <option key={key} value={key}>{label}</option>
              ))}
            </select>
          </label>
          <label>
            <span className="mb-1 block text-[10px] uppercase tracking-[0.08em] text-dim">Стадия</span>
            <select
              aria-label="Стадия"
              value={draft.lifecycle}
              onChange={(event) => setDraft((current) => ({
                ...current,
                lifecycle: event.target.value as Draft["lifecycle"],
              }))}
              className="min-h-11 w-full rounded-md border border-line bg-panel2 px-2.5 text-base text-fg outline-none focus:border-accent sm:text-xs"
            >
              <option value="">Все стадии</option>
              {LIFECYCLES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          <label>
            <span className="mb-1 block text-[10px] uppercase tracking-[0.08em] text-dim">Период</span>
            <select
              aria-label="Период"
              value={draft.period}
              onChange={(event) => setDraft((current) => ({ ...current, period: event.target.value as Period }))}
              className="min-h-11 w-full rounded-md border border-line bg-panel2 px-2.5 text-base text-fg outline-none focus:border-accent sm:text-xs"
            >
              <option value="7d">7 дней</option>
              <option value="30d">30 дней</option>
              <option value="90d">90 дней</option>
              <option value="all">вся история</option>
            </select>
          </label>
          <div className="relative">
            <label htmlFor="story-entity" className="mb-1 block text-[10px] uppercase tracking-[0.08em] text-dim">
              Сущность
            </label>
            <div className="relative">
              <Search aria-hidden="true" size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-dim" />
              <input
                id="story-entity"
                role="combobox"
                aria-label="Сущность"
                aria-autocomplete="list"
                aria-expanded={suggestionsOpen}
                aria-controls="story-entity-options"
                aria-activedescendant={activeSuggestion >= 0 ? `story-entity-option-${activeSuggestion}` : undefined}
                value={entityQuery}
                onChange={(event) => {
                  setEntityQuery(event.target.value);
                  setDraft((current) => ({ ...current, entity_id: "", entity_label: "" }));
                  setActiveSuggestion(-1);
                }}
                onFocus={() => setSuggestionsOpen(suggestions.length > 0)}
                onBlur={() => {
                  setSuggestionsOpen(false);
                  setActiveSuggestion(-1);
                }}
                onKeyDown={entityKeyDown}
                placeholder="Путин, Мадрид…"
                className="min-h-11 w-full rounded-md border border-line bg-panel2 pl-8 pr-8 text-base text-fg outline-none focus:border-accent sm:text-xs"
              />
              {suggestionsLoading && <LoaderCircle aria-label="Ищем сущности" size={13} className="absolute right-2.5 top-1/2 -translate-y-1/2 animate-spin motion-reduce:animate-none" />}
              {draft.entity_id && (
                <button
                  type="button"
                  aria-label="Убрать сущность"
                  onClick={() => {
                    setDraft((current) => ({ ...current, entity_id: "", entity_label: "" }));
                    setEntityQuery("");
                  }}
                  className="absolute right-2 top-1/2 min-h-11 -translate-y-1/2 px-1 text-dim sm:min-h-0"
                >
                  <X aria-hidden="true" size={13} />
                </button>
              )}
            </div>
            {suggestionsOpen && (
              <div id="story-entity-options" role="listbox" className="absolute inset-x-0 top-full z-30 mt-1 overflow-hidden rounded-md border border-line bg-panel shadow-2xl">
                {suggestions.map((entity, index) => (
                  <button
                    key={entity.id}
                    id={`story-entity-option-${index}`}
                    type="button"
                    role="option"
                    tabIndex={-1}
                    aria-selected={index === activeSuggestion}
                    onPointerDown={(event) => event.preventDefault()}
                    onClick={() => chooseEntity(entity)}
                    className={`flex min-h-11 w-full items-center justify-between border-b border-line px-3 text-left text-sm last:border-0 ${
                      index === activeSuggestion ? "bg-ru-blue/15 text-ru-white" : "hover:bg-white/5"
                    }`}
                  >
                    <span>{entity.label}</span><span className="tnum text-[9px] uppercase text-dim">{entity.kind}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3">
          <button
            type="button"
            onClick={() => {
              setDraft({ country: "", topic: "", lifecycle: "", entity_id: "", entity_label: "", period: "30d" });
              setEntityQuery("");
            }}
            className="min-h-11 text-[11px] text-dim underline underline-offset-4 hover:text-fg sm:min-h-0"
          >
            сбросить фильтры
          </button>
          <button type="submit" className="inline-flex min-h-11 items-center gap-2 rounded-md bg-ru-white px-5 text-[11px] font-semibold uppercase tracking-[0.08em] text-bg hover:bg-white focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent">
            <Network aria-hidden="true" size={14} /> показать сюжеты
          </button>
        </div>
      </form>

      <section className="reveal reveal-3 mt-8" aria-live="polite" aria-busy={loading || loadingMore}>
        {loading && (
          <div role="status" className="border-y border-line py-12 text-center text-dim">
            <LoaderCircle aria-hidden="true" className="mx-auto mb-3 animate-spin motion-reduce:animate-none" size={22} />
            <span className="tnum text-[10px] uppercase tracking-[0.12em]">Собираем межстрановые связи…</span>
          </div>
        )}
        {error && (
          <div role="alert" className="card border-ru-red/40 px-4 py-4 text-[13px] text-[#e5aaa5]">
            <p>{error}</p>
            <button type="button" onClick={() => setReload((value) => value + 1)} className="mt-2 min-h-11 text-ru-white underline underline-offset-4 sm:min-h-0">
              повторить
            </button>
          </div>
        )}
        {!loading && !error && stories.length === 0 && !needsCanonicalPeriod && (
          <div className="border-y border-line py-12 text-center">
            <p className="display text-[26px]">По этой линзе сюжетов пока нет</p>
            <p className="mx-auto mt-2 max-w-xl text-[12px] leading-5 text-dim">
              Выбранный период: {selectedPeriodLabel}. Уберите один из фильтров или расширьте период.
              Сюжет появляется только после подтверждения связи материалами как минимум из двух стран.
            </p>
            {indexedFrom && indexedTo && (
              <p className="tnum mt-2 text-[10px] uppercase tracking-[0.08em] text-dim">
                Проиндексированное покрытие: {indexedFrom}–{indexedTo}
              </p>
            )}
          </div>
        )}
        {!loading && stories.length > 0 && (
          <>
            <div className="mb-3 flex items-baseline justify-between gap-3">
              <p className="card-title">доказательные досье</p>
              <p className="tnum text-[10px] text-dim">показано {stories.length}</p>
            </div>
            <div className="grid gap-3 lg:grid-cols-2">
              {stories.map((story) => <StoryCard key={story.id} story={story} />)}
            </div>
            {nextCursor && (
              <div className="border-b border-line py-8 text-center">
                <button
                  type="button"
                  onClick={more}
                  disabled={loadingMore}
                  className="inline-flex min-h-11 items-center gap-2 rounded-md border border-line px-5 text-[11px] uppercase tracking-[0.08em] text-fg hover:border-ru-blue disabled:opacity-50"
                >
                  {loadingMore && <LoaderCircle aria-hidden="true" size={14} className="animate-spin motion-reduce:animate-none" />}
                  {loadingMore ? "загружаем" : "следующую страницу"}
                </button>
              </div>
            )}
          </>
        )}
      </section>
    </main>
  );
}

export default function StoriesPage() {
  return (
    <Suspense fallback={<div className="p-8 text-dim">Загрузка сюжетов…</div>}>
      <StoriesPageContent />
    </Suspense>
  );
}
