"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import {
  Thread,
  ThreadDetail,
  ThreadTimelineArticle,
  getThreads,
  getThread,
  formatDate,
} from "@/lib/api";

// ── Constants ──────────────────────────────────────────

const COUNTRY_FLAGS: Record<string, string> = {
  KZ: "🇰🇿", AM: "🇦🇲", UZ: "🇺🇿", KG: "🇰🇬", TJ: "🇹🇯",
  TM: "🇹🇲", AZ: "🇦🇿", GE: "🇬🇪", MD: "🇲🇩", BY: "🇧🇾",
};

const ALL_COUNTRIES = [
  { code: "KZ", name: "Казахстан" },
  { code: "UZ", name: "Узбекистан" },
  { code: "BY", name: "Беларусь" },
  { code: "AZ", name: "Азербайджан" },
  { code: "AM", name: "Армения" },
  { code: "GE", name: "Грузия" },
  { code: "KG", name: "Кыргызстан" },
  { code: "TJ", name: "Таджикистан" },
  { code: "TM", name: "Туркменистан" },
  { code: "MD", name: "Молдова" },
];

const PHASE_CONFIG: Record<string, { emoji: string; color: string; label: string }> = {
  emerging:   { emoji: "🌱", color: "#3b82f6", label: "Зарождение" },
  escalating: { emoji: "📈", color: "#f59e0b", label: "Эскалация" },
  peak:       { emoji: "🔥", color: "#ef4444", label: "Пик" },
  cooling:    { emoji: "❄️", color: "#06b6d4", label: "Затухание" },
  resolved:   { emoji: "✅", color: "#22c55e", label: "Завершён" },
};

const PHASE_ORDER = ["emerging", "escalating", "peak", "cooling", "resolved"];

const STATUS_OPTIONS = [
  { value: "", label: "Все" },
  { value: "developing", label: "🔄 Развивается" },
  { value: "resolved", label: "✅ Завершён" },
  { value: "dormant", label: "💤 Неактивен" },
];

const LIMIT_OPTIONS = [10, 20, 50];

// ── Helpers ────────────────────────────────────────────

function sentimentColor(s: number): string {
  if (s > 0.5) return "#22c55e";
  if (s > -0.5) return "#eab308";
  return "#ef4444";
}

function importanceBadgeColor(score: number): string {
  if (score >= 20) return "#ef4444";
  if (score >= 10) return "#f59e0b";
  return "#3b82f6";
}

function actionIcon(level: number): string {
  return level >= 4 ? "💥" : "⚡";
}

// ── Components ─────────────────────────────────────────

function ArcProgressBar({ phase }: { phase: string }) {
  const activeIdx = PHASE_ORDER.indexOf(phase);
  return (
    <div className="flex gap-1 my-2">
      {PHASE_ORDER.map((p, i) => {
        const cfg = PHASE_CONFIG[p];
        const active = i <= activeIdx;
        return (
          <div
            key={p}
            className="flex-1 h-2 rounded-full transition-all"
            style={{
              backgroundColor: active ? cfg.color : "rgba(255,255,255,0.1)",
            }}
            title={cfg.label}
          />
        );
      })}
    </div>
  );
}

function TimelineSection({ threadId }: { threadId: number }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<ThreadDetail | null>(null);
  const [loading, setLoading] = useState(false);

  const loadTimeline = useCallback(async () => {
    if (detail) { setOpen(!open); return; }
    setLoading(true);
    try {
      const d = await getThread(threadId);
      setDetail(d);
      setOpen(true);
    } catch {
      // ignore
    } finally {
      setLoading(false);
    }
  }, [threadId, detail, open]);

  return (
    <div className="mt-3">
      <button
        onClick={loadTimeline}
        className="text-sm text-blue-400 hover:text-blue-300 transition-colors flex items-center gap-1"
      >
        {loading ? "⏳ Загрузка..." : open ? "📋 Скрыть хронологию" : `📋 Хронология (${detail?.timeline?.length ?? "…"} статей)`}
      </button>
      {open && detail?.timeline && (
        <div className="mt-2 space-y-1 max-h-80 overflow-y-auto">
          {detail.timeline.map((a: ThreadTimelineArticle) => (
            <div
              key={a.article_id}
              className="flex items-start gap-3 text-xs py-1.5 px-2 rounded bg-white/5 hover:bg-white/10 transition-colors"
            >
              <span className="text-muted-foreground whitespace-nowrap">
                {formatDate(a.published_at)}
              </span>
              <a
                href={a.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-blue-400 hover:underline flex-1 min-w-0 truncate"
              >
                {a.title}
              </a>
              <span className="text-muted-foreground whitespace-nowrap">{a.source}</span>
              <span>{actionIcon(a.action_level)} {a.action_level}</span>
              <span style={{ color: sentimentColor(a.sentiment) }}>
                {a.sentiment > 0 ? "+" : ""}{a.sentiment.toFixed(2)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ThreadCard({ thread }: { thread: Thread }) {
  const phase = PHASE_CONFIG[thread.arc_phase] || PHASE_CONFIG.emerging;
  const flag = COUNTRY_FLAGS[thread.country_code] || "🏳️";

  return (
    <div
      className="relative rounded-xl border border-white/10 p-5 transition-all duration-200 hover:-translate-y-1 hover:shadow-xl hover:shadow-black/20"
      style={{
        background: "linear-gradient(135deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.01) 100%)",
        backdropFilter: "blur(12px)",
      }}
    >
      {/* Country badge */}
      <div className="absolute top-4 right-4 text-xs flex items-center gap-1 text-muted-foreground">
        <span>{flag}</span>
        <span>{thread.country_name}</span>
      </div>

      {/* Phase + importance badges */}
      <div className="flex items-center gap-2 mb-2">
        <span
          className="text-xs px-2 py-0.5 rounded-full font-medium"
          style={{ backgroundColor: phase.color + "22", color: phase.color }}
        >
          {phase.emoji} {phase.label}
        </span>
        <span
          className="text-xs px-2 py-0.5 rounded-full font-medium"
          style={{
            backgroundColor: importanceBadgeColor(thread.importance_score) + "22",
            color: importanceBadgeColor(thread.importance_score),
          }}
        >
          ★ {thread.importance_score.toFixed(0)}
        </span>
      </div>

      {/* Title */}
      <Link href={`/threads/${thread.id}`}>
        <h3 className="text-lg font-bold mb-1 hover:text-blue-400 transition-colors cursor-pointer pr-24">
          {thread.title}
        </h3>
      </Link>

      {/* Arc progress */}
      <ArcProgressBar phase={thread.arc_phase} />

      {/* Meta row */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground mt-1">
        <span>{actionIcon(thread.max_action_level)} Уровень {thread.max_action_level}</span>
        <span>📰 {thread.article_count} статей</span>
        <span style={{ color: sentimentColor(thread.avg_sentiment) }}>
          Тональность: {thread.avg_sentiment > 0 ? "+" : ""}{thread.avg_sentiment.toFixed(2)}
        </span>
        <span>{formatDate(thread.first_seen)} — {formatDate(thread.last_seen)}</span>
      </div>

      {/* Narrative */}
      {thread.narrative && (
        <div
          className="mt-3 text-sm text-muted-foreground pl-3 border-l-2"
          style={{ borderColor: phase.color }}
        >
          {thread.narrative.length > 300
            ? thread.narrative.slice(0, 300) + "…"
            : thread.narrative}
        </div>
      )}

      {/* Timeline */}
      <TimelineSection threadId={thread.id} />
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────

export default function ThreadsPage() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedCountries, setSelectedCountries] = useState<string[]>([]);
  const [status, setStatus] = useState("developing");
  const [limit, setLimit] = useState(20);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string | number> = { limit };
      if (status) params.status = status;
      if (selectedCountries.length === 1) params.country = selectedCountries[0];

      const data = await getThreads(params);
      let filtered = data.threads || [];

      // Client-side multi-country filter
      if (selectedCountries.length > 1) {
        filtered = filtered.filter((t) => selectedCountries.includes(t.country_code));
      }

      setThreads(filtered);
    } catch {
      setThreads([]);
    } finally {
      setLoading(false);
    }
  }, [selectedCountries, status, limit]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const toggleCountry = (code: string) => {
    setSelectedCountries((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
    );
  };

  // Metrics
  const totalThreads = threads.length;
  const activeThreads = threads.filter((t) => t.status === "developing").length;
  const totalArticles = threads.reduce((s, t) => s + t.article_count, 0);
  const avgImportance = totalThreads > 0
    ? threads.reduce((s, t) => s + t.importance_score, 0) / totalThreads
    : 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">🧵 Сюжетные нити</h1>
        <p className="text-muted-foreground mt-1">
          Кластеры связанных событий: как развиваются ключевые сюжеты в медиапространстве СНГ
        </p>
      </div>

      {/* Filters */}
      <div className="space-y-3">
        {/* Countries */}
        <div className="flex flex-wrap gap-2">
          {ALL_COUNTRIES.map((c) => (
            <button
              key={c.code}
              onClick={() => toggleCountry(c.code)}
              className={`text-xs px-3 py-1.5 rounded-full border transition-all ${
                selectedCountries.includes(c.code)
                  ? "bg-blue-500/20 border-blue-500/50 text-blue-400"
                  : "border-white/10 text-muted-foreground hover:border-white/30"
              }`}
            >
              {COUNTRY_FLAGS[c.code]} {c.name}
            </button>
          ))}
          {selectedCountries.length > 0 && (
            <button
              onClick={() => setSelectedCountries([])}
              className="text-xs px-3 py-1.5 rounded-full border border-red-500/30 text-red-400 hover:bg-red-500/10 transition-all"
            >
              ✕ Сбросить
            </button>
          )}
        </div>

        {/* Status + Limit */}
        <div className="flex flex-wrap gap-4">
          <div className="flex gap-1">
            {STATUS_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setStatus(opt.value)}
                className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
                  status === opt.value
                    ? "bg-white/10 text-white"
                    : "text-muted-foreground hover:text-white"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-1 text-xs text-muted-foreground">
            Лимит:
            {LIMIT_OPTIONS.map((l) => (
              <button
                key={l}
                onClick={() => setLimit(l)}
                className={`px-2 py-1 rounded transition-all ${
                  limit === l ? "bg-white/10 text-white" : "hover:text-white"
                }`}
              >
                {l}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { icon: "🧵", label: "Сюжетов", value: totalThreads },
          { icon: "🔄", label: "Активных", value: activeThreads },
          { icon: "📰", label: "Статей", value: totalArticles },
          { icon: "★", label: "Ср. важность", value: avgImportance.toFixed(1) },
        ].map((m) => (
          <div
            key={m.label}
            className="rounded-xl border border-white/10 p-4 text-center"
            style={{
              background: "linear-gradient(135deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.01) 100%)",
            }}
          >
            <div className="text-2xl mb-1">{m.icon}</div>
            <div className="text-xl font-bold">{m.value}</div>
            <div className="text-xs text-muted-foreground">{m.label}</div>
          </div>
        ))}
      </div>

      {/* Thread list */}
      {loading ? (
        <div className="text-center py-12 text-muted-foreground">⏳ Загрузка сюжетов...</div>
      ) : threads.length === 0 ? (
        <div className="text-center py-16 space-y-3">
          <div className="text-5xl">🧵</div>
          <h2 className="text-xl font-semibold">Сюжетных нитей пока нет</h2>
          <p className="text-muted-foreground text-sm">
            Система автоматически кластеризует события каждый час
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {threads.map((t) => (
            <ThreadCard key={t.id} thread={t} />
          ))}
        </div>
      )}
    </div>
  );
}
