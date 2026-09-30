"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { LoaderCircle } from "lucide-react";
import { api } from "@/lib/api";
import type { AgendaCoverage, AgendasResponse } from "@/lib/types";
import { AgendaCard } from "./AgendaPanel";

const FRESH_WINDOW_MS = 72 * 60 * 60 * 1000;
const REFRESH_MS = 120_000;
const COVERAGE_STATUS: Record<AgendaCoverage["status"], string | null> = {
  ok: null,
  never_run: "Сбор повесток ещё не запускался.",
  budget_exhausted: "Лимит бюджета достигнут. Поиск новых связей приостановлен.",
  error: "Последний проход завершился с ошибкой. Ниже — ранее собранные свежие публикации, если они доступны.",
  disabled: "Поиск новых повесток отключён.",
  running: "Идёт обновление повесток.",
};

export default function AgendaHighlights() {
  const titleId = useId();
  const [payload, setPayload] = useState<AgendasResponse | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const activeRequest = useRef<AbortController | null>(null);

  const refresh = useCallback(() => {
    // The clock advances even when a slow or failed request leaves cached data.
    setNow(Date.now());
    if (activeRequest.current) return;
    const controller = new AbortController();
    activeRequest.current = controller;
    setLoading(true);
    api.agendas({ limit: 6 }, controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) { setPayload(value); setError(false); }
      })
      .catch(() => { if (!controller.signal.aborted) setError(true); })
      .finally(() => {
        if (activeRequest.current === controller) {
          activeRequest.current = null;
          if (!controller.signal.aborted) { setLoading(false); setNow(Date.now()); }
        }
      });
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, REFRESH_MS);
    return () => {
      window.clearInterval(timer);
      activeRequest.current?.abort();
      activeRequest.current = null;
    };
  }, [refresh]);

  const items = (payload?.items ?? []).filter((item) => {
    const collectedAt = item.last_seen ? new Date(item.last_seen).getTime() : NaN;
    return Number.isFinite(collectedAt) && collectedAt >= now - FRESH_WINDOW_MS && collectedAt <= now;
  }).slice(0, 6);
  const coverageStatus = payload ? COVERAGE_STATUS[payload.coverage.status] : null;

  return (
    <section aria-labelledby={titleId} className="card overflow-hidden p-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h2 id={titleId} className="card-title text-dim">Свежие новостные повестки</h2>
        <Link href="/stories" className="min-h-11 content-center text-xs text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-accent">Все повестки и архив сюжетов</Link>
      </div>
      <p className="mt-2 text-xs leading-5 text-dim">Публикации собраны за последние 72 часа среди подключённых источников. Повестки могут включать более ранние материалы.</p>
      {coverageStatus && <p role="status" className="mt-3 border-l-2 border-ru-blue/70 pl-3 text-xs leading-5 text-dim">{coverageStatus}</p>}
      {loading && <p role="status" className="mt-4 flex items-center gap-2 text-xs text-dim"><LoaderCircle aria-hidden="true" size={14} className="animate-spin motion-reduce:animate-none" />{payload ? "Обновляем свежие повестки…" : "Загружаем свежие повестки…"}</p>}
      {error && <div role="alert" className="mt-4 border-l-2 border-ru-red/50 pl-3 text-sm text-dim">
        <p>Не удалось загрузить свежие повестки.</p>
        <button type="button" disabled={loading} onClick={refresh} className="min-h-11 text-xs text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-accent disabled:opacity-50">Повторить загрузку</button>
      </div>}
      {payload && items.length === 0 && <p className="mt-4 border-y border-line py-5 text-sm leading-6 text-dim">Среди загруженных повесток нет публикаций, собранных за последние 72 часа. Это не означает отсутствия событий.</p>}
      {items.length > 0 && <div className="mt-4 grid items-start gap-3 lg:grid-cols-2">{items.map((item) => <AgendaCard key={item.id} item={item} />)}</div>}
    </section>
  );
}
