"use client";

import {
  ArrowDown,
  LoaderCircle,
  Search as SearchIcon,
  SlidersHorizontal,
  X,
} from "lucide-react";
import { FormEvent, KeyboardEvent, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import SearchResults from "@/components/SearchResults";
import SiteHeader from "@/components/SiteHeader";
import { api } from "@/lib/api";
import type {
  ArticleSearchRequest,
  EntitySuggestion,
  SearchArticle,
  SearchSort,
} from "@/lib/types";

const API_FILTERS = [
  "q",
  "country",
  "topic",
  "entity_id",
  "from",
  "to",
  "tier",
  "language",
] as const;

const URL_STATE_KEYS = [
  ...API_FILTERS,
  "sort",
  "range",
] as const;

type Draft = Record<(typeof URL_STATE_KEYS)[number], string>;

function defaultRangeStart(): string {
  const date = new Date();
  date.setUTCDate(date.getUTCDate() - 90);
  return date.toISOString().slice(0, 10);
}

function draftFromParams(params: URLSearchParams): Draft {
  const allHistory = params.get("range") === "all";
  return {
    q: params.get("q") ?? "",
    country: params.get("country") ?? "",
    topic: params.get("topic") ?? "",
    entity_id: params.get("entity_id") ?? "",
    from: allHistory ? "" : params.get("from") ?? "",
    to: allHistory ? "" : params.get("to") ?? "",
    tier: params.get("tier") ?? "",
    language: params.get("language") ?? "",
    sort: params.get("sort") === "newest" ? "newest" : "relevance",
    range: allHistory ? "all" : "",
  };
}

function requestFromParams(params: URLSearchParams): ArticleSearchRequest {
  const request: ArticleSearchRequest = {};
  const allHistory = params.get("range") === "all";
  for (const key of API_FILTERS) {
    const value = params.get(key)?.trim();
    if (!value) continue;
    if (allHistory && (key === "from" || key === "to")) continue;
    request[key] = value;
  }
  request.sort = params.get("sort") === "newest" ? "newest" : "relevance";
  return request;
}

function hasSearchIntent(params: URLSearchParams): boolean {
  const request = requestFromParams(params);
  return API_FILTERS.some((key) => Boolean(request[key]?.trim()));
}

function SearchPageContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const paramsKey = searchParams.toString();
  const inputRef = useRef<HTMLInputElement>(null);
  const loadMoreControllerRef = useRef<AbortController | null>(null);
  const [draft, setDraft] = useState<Draft>(() =>
    draftFromParams(new URLSearchParams(paramsKey)),
  );
  const [entityQuery, setEntityQuery] = useState("");
  const [selectedEntityLabel, setSelectedEntityLabel] = useState("");
  const [suggestions, setSuggestions] = useState<EntitySuggestion[]>([]);
  const [suggestionsLoading, setSuggestionsLoading] = useState(false);
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [activeSuggestion, setActiveSuggestion] = useState(-1);
  const [items, setItems] = useState<SearchArticle[]>([]);
  const [candidateCount, setCandidateCount] = useState(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);

  const currentParams = useMemo(() => new URLSearchParams(paramsKey), [paramsKey]);
  const currentRequest = useMemo(() => requestFromParams(currentParams), [currentParams]);
  const needsDefaultRange = useMemo(
    () =>
      !currentParams.has("from") &&
      !currentParams.has("to") &&
      currentParams.get("range") !== "all",
    [currentParams],
  );
  const shouldSearch = useMemo(
    () => hasSearchIntent(currentParams) && !needsDefaultRange,
    [currentParams, needsDefaultRange],
  );
  const activeParamsKeyRef = useRef(paramsKey);
  activeParamsKeyRef.current = paramsKey;

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const nextParams = new URLSearchParams(paramsKey);
    setDraft(draftFromParams(nextParams));
    setEntityQuery("");
    setSelectedEntityLabel("");
    setSuggestions([]);
    setSuggestionsOpen(false);
    setActiveSuggestion(-1);
  }, [paramsKey]);

  useEffect(() => {
    if (!needsDefaultRange) return;
    const canonical = new URLSearchParams(paramsKey);
    canonical.delete("range");
    canonical.set("from", defaultRangeStart());
    router.replace(`/search?${canonical.toString()}`);
  }, [needsDefaultRange, paramsKey, router]);

  useEffect(() => {
    loadMoreControllerRef.current?.abort();
    loadMoreControllerRef.current = null;
    setLoadingMore(false);
    return () => {
      loadMoreControllerRef.current?.abort();
      loadMoreControllerRef.current = null;
    };
  }, [paramsKey]);

  useEffect(() => {
    const query = entityQuery.trim();
    if (query.length < 2 || query === selectedEntityLabel) {
      setSuggestions([]);
      setSuggestionsOpen(false);
      setSuggestionsLoading(false);
      return;
    }
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSuggestionsLoading(true);
      api.entitySuggestions(query, controller.signal)
        .then((response) => {
          setSuggestions(response.items);
          setSuggestionsOpen(response.items.length > 0);
          setActiveSuggestion(-1);
        })
        .catch((reason: unknown) => {
          if (!(reason instanceof DOMException && reason.name === "AbortError")) {
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
  }, [entityQuery, selectedEntityLabel]);

  useEffect(() => {
    if (!shouldSearch) {
      setItems([]);
      setCandidateCount(0);
      setNextCursor(null);
      setLoading(false);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    setItems([]);
    setCandidateCount(0);
    setNextCursor(null);
    api.searchArticles(currentRequest, null, controller.signal)
      .then((response) => {
        if (controller.signal.aborted || activeParamsKeyRef.current !== paramsKey) return;
        setItems(response.items);
        setCandidateCount(response.candidate_count);
        setNextCursor(response.next_cursor);
      })
      .catch((reason: unknown) => {
        if (!(reason instanceof DOMException && reason.name === "AbortError")) {
          setError("Не удалось получить результаты. Проверьте соединение и повторите поиск.");
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [currentRequest, paramsKey, reload, shouldSearch]);

  function updateDraft(key: keyof Draft, value: string) {
    setDraft((current) => ({ ...current, [key]: value }));
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const query = draft.q.trim();
    if (query.length === 1) {
      setError("Поисковый запрос должен содержать не менее двух символов.");
      return;
    }
    if (draft.from && draft.to && draft.from > draft.to) {
      setError("Начальная дата не может быть позже конечной.");
      return;
    }
    const params = new URLSearchParams();
    for (const key of URL_STATE_KEYS) {
      const value = draft[key].trim();
      if (value) params.set(key, key === "country" ? value.toUpperCase() : value);
    }
    router.push(`/search?${params.toString()}`);
  }

  function chooseEntity(entity: EntitySuggestion) {
    updateDraft("entity_id", entity.id);
    setSelectedEntityLabel(entity.label);
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
      setActiveSuggestion((current) =>
        !suggestionsOpen || current < 0 ? 0 : (current + 1) % suggestions.length,
      );
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setSuggestionsOpen(true);
      setActiveSuggestion((current) =>
        !suggestionsOpen || current < 0
          ? suggestions.length - 1
          : current === 0
            ? suggestions.length - 1
            : current - 1,
      );
    } else if (event.key === "Enter" && suggestionsOpen && activeSuggestion >= 0) {
      event.preventDefault();
      chooseEntity(suggestions[activeSuggestion]);
    }
  }

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    loadMoreControllerRef.current?.abort();
    const controller = new AbortController();
    const requestKey = paramsKey;
    loadMoreControllerRef.current = controller;
    setLoadingMore(true);
    setError(null);
    try {
      const response = await api.searchArticles(
        currentRequest,
        nextCursor,
        controller.signal,
      );
      if (controller.signal.aborted || activeParamsKeyRef.current !== requestKey) return;
      setItems((previous) => {
        const known = new Set(previous.map((item) => item.article_id));
        return [
          ...previous,
          ...response.items.filter((item) => !known.has(item.article_id)),
        ];
      });
      setCandidateCount(response.candidate_count);
      setNextCursor(response.next_cursor);
    } catch (reason: unknown) {
      if (controller.signal.aborted || activeParamsKeyRef.current !== requestKey) return;
      setError("Следующую страницу не удалось загрузить. Уже найденные материалы сохранены.");
    } finally {
      if (
        loadMoreControllerRef.current === controller &&
        activeParamsKeyRef.current === requestKey
      ) {
        loadMoreControllerRef.current = null;
        setLoadingMore(false);
      }
    }
  }

  return (
    <main className="mx-auto max-w-[1120px] px-3 pb-16">
      <SiteHeader active="/search" />

      <header className="reveal reveal-1 grid gap-5 pb-7 pt-9 md:grid-cols-[minmax(0,1fr)_18rem] md:items-end">
        <div>
          <p className="section-num">АРХИВ / 01</p>
          <h1 className="display mt-2 max-w-3xl text-[38px] leading-[1.03] sm:text-[48px]">
            Поиск по новостным доказательствам
          </h1>
        </div>
        <p className="border-l border-ru-red/70 pl-4 text-[12px] leading-5 text-dim">
          Поиск охватывает только собранные и проиндексированные материалы. Каждое
          совпадение ведёт к сохранённой первичной ссылке, если она безопасна и доступна.
        </p>
      </header>

      <form
        onSubmit={submit}
        role="search"
        className="reveal reveal-2 card relative overflow-visible px-4 py-5 sm:px-6"
      >
        <span className="tricolor absolute inset-x-0 top-0 !h-[2px]" aria-hidden="true" />
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="min-w-0 flex-1">
            <span className="card-title mb-2 block">Слова в заголовке, тексте или сущностях</span>
            <span className="relative block">
              <SearchIcon
                aria-hidden="true"
                size={18}
                className="absolute left-0 top-1/2 -translate-y-1/2 text-dim"
              />
              <input
                ref={inputRef}
                autoFocus
                type="search"
                value={draft.q}
                minLength={2}
                maxLength={200}
                onChange={(event) => updateDraft("q", event.target.value)}
                placeholder="например, Путин"
                className="min-h-11 w-full border-b border-line bg-transparent py-2 pl-7 pr-2 text-[18px] text-ru-white outline-none transition-colors placeholder:text-dim/70 focus:border-accent"
              />
            </span>
          </label>
          <button
            type="submit"
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-md bg-ru-white px-5 py-2 text-[12px] font-semibold uppercase tracking-[0.08em] text-bg transition-colors hover:bg-white focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
          >
            Найти материалы
          </button>
        </div>

        <div className="mt-6 border-t border-line pt-5">
          <p className="card-title mb-3 flex items-center gap-2">
            <SlidersHorizontal aria-hidden="true" size={12} /> точная линза
          </p>
          <div className="grid gap-x-4 gap-y-4 sm:grid-cols-2 lg:grid-cols-4">
            <label>
              <span className="mb-1 block text-[10px] uppercase tracking-[0.08em] text-dim">Страна источника</span>
              <input
                value={draft.country}
                maxLength={2}
                onChange={(event) => updateDraft("country", event.target.value)}
                placeholder="ES"
                className="min-h-11 w-full rounded-md border border-line bg-panel2 px-2.5 py-2 text-base uppercase text-fg outline-none focus:border-accent sm:min-h-0 sm:text-xs"
              />
            </label>
            <label>
              <span className="mb-1 block text-[10px] uppercase tracking-[0.08em] text-dim">Тема</span>
              <input
                value={draft.topic}
                onChange={(event) => updateDraft("topic", event.target.value)}
                placeholder="diplomacy"
                className="min-h-11 w-full rounded-md border border-line bg-panel2 px-2.5 py-2 text-base text-fg outline-none focus:border-accent sm:min-h-0 sm:text-xs"
              />
            </label>
            <div className="relative sm:col-span-2">
              <label htmlFor="entity-suggest" className="mb-1 block text-[10px] uppercase tracking-[0.08em] text-dim">
                Персона, организация, место или событие
              </label>
              <div className="relative">
                <input
                  id="entity-suggest"
                  role="combobox"
                  aria-autocomplete="list"
                  aria-expanded={suggestionsOpen}
                  aria-controls="entity-suggestions"
                  aria-activedescendant={activeSuggestion >= 0 ? `entity-option-${activeSuggestion}` : undefined}
                  value={entityQuery}
                  onChange={(event) => {
                    setEntityQuery(event.target.value);
                    setSelectedEntityLabel("");
                    updateDraft("entity_id", "");
                  }}
                  onFocus={() => setSuggestionsOpen(suggestions.length > 0)}
                  onBlur={() => {
                    setSuggestionsOpen(false);
                    setActiveSuggestion(-1);
                  }}
                  onKeyDown={entityKeyDown}
                  placeholder="начните вводить: Владимир Путин"
                  className="min-h-11 w-full rounded-md border border-line bg-panel2 px-2.5 py-2 pr-9 text-base text-fg outline-none focus:border-accent sm:min-h-0 sm:text-xs"
                />
                {suggestionsLoading && (
                  <LoaderCircle
                    aria-label="Ищем сущности"
                    size={15}
                    className="absolute right-3 top-1/2 -translate-y-1/2 animate-spin text-dim motion-reduce:animate-none"
                  />
                )}
              </div>
              {suggestionsOpen && (
                <div
                  id="entity-suggestions"
                  role="listbox"
                  aria-label="Подсказки сущностей"
                  className="absolute inset-x-0 top-full z-30 mt-1 overflow-hidden rounded-md border border-line bg-panel shadow-2xl"
                >
                  {suggestions.map((entity, index) => (
                    <button
                      key={entity.id}
                      id={`entity-option-${index}`}
                      type="button"
                      role="option"
                      tabIndex={-1}
                      aria-selected={index === activeSuggestion}
                      onPointerDown={(event) => event.preventDefault()}
                      onClick={() => chooseEntity(entity)}
                      className={`flex min-h-11 w-full items-center justify-between gap-4 border-b border-line px-3 py-2 text-left text-base last:border-b-0 sm:text-xs ${
                        index === activeSuggestion ? "bg-ru-blue/15 text-ru-white" : "text-fg hover:bg-white/5"
                      }`}
                    >
                      <span>{entity.label}</span>
                      <span className="tnum text-[9px] uppercase text-dim">{entity.kind}</span>
                    </button>
                  ))}
                </div>
              )}
              {draft.entity_id && (
                <button
                  type="button"
                  onClick={() => {
                    updateDraft("entity_id", "");
                    setSelectedEntityLabel("");
                    setEntityQuery("");
                  }}
                  className="mt-1 inline-flex min-h-11 items-center gap-1 text-[10px] text-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:min-h-0"
                >
                  {selectedEntityLabel || `сущность ${draft.entity_id.slice(0, 8)}…`}
                  <X aria-hidden="true" size={11} />
                </button>
              )}
            </div>
            <label>
              <span className="mb-1 block text-[10px] uppercase tracking-[0.08em] text-dim">От даты</span>
              <input
                type="date"
                value={draft.from}
                onChange={(event) => {
                  updateDraft("from", event.target.value);
                  updateDraft("range", "");
                }}
                className="min-h-11 w-full rounded-md border border-line bg-panel2 px-2.5 py-2 text-base text-fg outline-none focus:border-accent sm:min-h-0 sm:text-xs"
              />
            </label>
            <label>
              <span className="mb-1 block text-[10px] uppercase tracking-[0.08em] text-dim">До даты</span>
              <input
                type="date"
                value={draft.to}
                onChange={(event) => {
                  updateDraft("to", event.target.value);
                  updateDraft("range", "");
                }}
                className="min-h-11 w-full rounded-md border border-line bg-panel2 px-2.5 py-2 text-base text-fg outline-none focus:border-accent sm:min-h-0 sm:text-xs"
              />
            </label>
            <label>
              <span className="mb-1 block text-[10px] uppercase tracking-[0.08em] text-dim">Тип источника</span>
              <select
                value={draft.tier}
                onChange={(event) => updateDraft("tier", event.target.value)}
                className="min-h-11 w-full rounded-md border border-line bg-panel2 px-2.5 py-2 text-base text-fg outline-none focus:border-accent sm:min-h-0 sm:text-xs"
              >
                <option value="">Все типы</option>
                <option value="official">Официальный</option>
                <option value="mainstream">Мейнстрим</option>
                <option value="independent">Независимый</option>
                <option value="social">Социальные медиа</option>
                <option value="domestic_opposition">Внутренняя оппозиция</option>
                <option value="western_proxy">Западный прокси</option>
                <option value="analytics">Аналитика</option>
              </select>
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label>
                <span className="mb-1 block text-[10px] uppercase tracking-[0.08em] text-dim">Язык</span>
                <input
                  value={draft.language}
                  maxLength={8}
                  onChange={(event) => updateDraft("language", event.target.value)}
                  placeholder="es"
                  className="min-h-11 w-full rounded-md border border-line bg-panel2 px-2.5 py-2 text-base text-fg outline-none focus:border-accent sm:min-h-0 sm:text-xs"
                />
              </label>
              <label>
                <span className="mb-1 block text-[10px] uppercase tracking-[0.08em] text-dim">Порядок</span>
                <select
                  value={draft.sort}
                  onChange={(event) => updateDraft("sort", event.target.value as SearchSort)}
                  className="min-h-11 w-full rounded-md border border-line bg-panel2 px-2 py-2 text-base text-fg outline-none focus:border-accent sm:min-h-0 sm:text-xs"
                >
                  <option value="relevance">точность</option>
                  <option value="newest">сначала новые</option>
                </select>
              </label>
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              setDraft((current) => ({
                ...current,
                country: "",
                topic: "",
                entity_id: "",
                from: "",
                to: "",
                tier: "",
                language: "",
                sort: "relevance",
                range: "all",
              }));
              setEntityQuery("");
              setSelectedEntityLabel("");
            }}
            aria-pressed={draft.range === "all"}
            className="mt-2 inline-flex min-h-11 items-center text-[10px] uppercase tracking-[0.08em] text-dim underline decoration-line underline-offset-4 hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:min-h-0"
          >
            Искать по всей истории без фильтров
          </button>
        </div>
      </form>

      <section className="reveal reveal-3 mt-9" aria-live="polite" aria-busy={loading || loadingMore}>
        {!shouldSearch && (
          <div className="grid gap-5 border-y border-line py-8 sm:grid-cols-[9rem_1fr]">
            <p className="card-title">как начать</p>
            <p className="lead !text-[17px]">
              Введите имя, событие или фразу. Можно сузить архив страной источника —
              например, найти упоминания Путина именно в испанских медиа.
            </p>
          </div>
        )}

        {loading && (
          <div role="status" className="border-y border-line py-10 text-center text-dim">
            <LoaderCircle
              aria-hidden="true"
              className="mx-auto mb-3 animate-spin motion-reduce:animate-none"
              size={22}
            />
            <span className="tnum text-[10px] uppercase tracking-[0.12em]">Сверяем индекс и доказательства…</span>
          </div>
        )}

        {error && (
          <div role="alert" className="card border-ru-red/40 px-4 py-4 text-[13px] text-[#e5aaa5]">
            <p>{error}</p>
            {shouldSearch && !loadingMore && (
              <button
                type="button"
                onClick={() => setReload((value) => value + 1)}
                className="mt-2 inline-flex min-h-11 items-center text-[11px] text-ru-white underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:min-h-0"
              >
                Повторить запрос
              </button>
            )}
          </div>
        )}

        {!loading && !error && shouldSearch && items.length === 0 && (
          <div className="border-y border-line py-10 text-center">
            <p className="display text-[25px]">Совпадений в индексе нет</p>
            <p className="mx-auto mt-2 max-w-xl text-[12px] leading-5 text-dim">
              Попробуйте убрать отдельные фильтры, расширить период или проверить другое
              написание имени. Это пустой результат по собранному архиву, а не по всему медиаполю.
            </p>
          </div>
        )}

        {!loading && items.length > 0 && (
          <>
            <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
              <p className="card-title">найденные материалы</p>
              <p className="tnum text-[10px] uppercase tracking-[0.08em] text-dim">
                показано {items.length} · кандидатов {candidateCount}
              </p>
            </div>
            <SearchResults items={items} />
            {nextCursor && (
              <div className="border-b border-line py-7 text-center">
                <button
                  type="button"
                  onClick={loadMore}
                  disabled={loadingMore}
                  className="inline-flex min-h-11 items-center gap-2 text-[11px] uppercase tracking-[0.09em] text-accent disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
                >
                  {loadingMore ? (
                    <LoaderCircle aria-hidden="true" size={14} className="animate-spin motion-reduce:animate-none" />
                  ) : (
                    <ArrowDown aria-hidden="true" size={14} />
                  )}
                  {loadingMore ? "Загружаем без перестановки…" : "Показать следующую страницу"}
                </button>
              </div>
            )}
          </>
        )}
      </section>
    </main>
  );
}

export default function SearchPage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto max-w-[1120px] px-3 pb-16">
          <SiteHeader active="/search" />
          <div role="status" className="py-16 text-center text-dim">Готовим архив…</div>
        </main>
      }
    >
      <SearchPageContent />
    </Suspense>
  );
}
