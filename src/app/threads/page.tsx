"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import {
  getThreads,
  getThread,
  formatDate,
  type Thread,
  type ThreadDetail,
  type ThreadTimelineArticle,
} from "@/lib/api";

// ── Constants ──────────────────────────────────────────

const COUNTRY_FLAGS: Record<string, string> = {
  KZ: "🇰🇿", AM: "🇦🇲", UZ: "🇺🇿", KG: "🇰🇬", TJ: "🇹🇯",
  TM: "🇹🇲", AZ: "🇦🇿", GE: "🇬🇪", MD: "🇲🇩", BY: "🇧🇾",
};

const ALL_COUNTRIES = [
  { code: "KZ", name: "Казахстан" }, { code: "UZ", name: "Узбекистан" },
  { code: "BY", name: "Беларусь" }, { code: "AZ", name: "Азербайджан" },
  { code: "AM", name: "Армения" }, { code: "GE", name: "Грузия" },
  { code: "KG", name: "Кыргызстан" }, { code: "TJ", name: "Таджикистан" },
  { code: "TM", name: "Туркменистан" }, { code: "MD", name: "Молдова" },
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

const SORT_OPTIONS = [
  { value: "importance", label: "★ Важность" },
  { value: "velocity", label: "⚡ Скорость" },
  { value: "recent", label: "🕐 Свежесть" },
  { value: "articles", label: "📰 Статьи" },
];

// ── Helpers ────────────────────────────────────────────

function sentimentColor(s: number): string {
  if (s > 0.3) return "#22c55e";
  if (s > -0.3) return "#eab308";
  return "#ef4444";
}

function importanceBadgeColor(score: number): string {
  if (score >= 30) return "#ef4444";
  if (score >= 15) return "#f59e0b";
  return "#3b82f6";
}

// ── Components ─────────────────────────────────────────

function ArcProgressBar({ phase }: { phase: string }) {
  const activeIdx = PHASE_ORDER.indexOf(phase);
  return (
    <div className="flex gap-1 my-2">
      {PHASE_ORDER.map((p, i) => {
        const cfg = PHASE_CONFIG[p];
        return (
          <div
            key={p}
            className="flex-1 h-1.5 rounded-full transition-all"
            style={{ backgroundColor: i <= activeIdx ? cfg.color : "rgba(255,255,255,0.08)" }}
            title={cfg.label}
          />
        );
      })}
    </div>
  );
}

function VelocityIndicator({ velocity }: { velocity: number }) {
  if (!velocity || velocity < 0.5) return null;
  const bars = velocity > 10 ? 3 : velocity > 3 ? 2 : 1;
  const color = velocity > 10 ? "text-red-400" : velocity > 3 ? "text-orange-400" : "text-blue-400";
  return (
    <span className={`text-xs ${color} font-medium`} title={`${velocity.toFixed(1)} статей/день`}>
      ⚡ {velocity.toFixed(1)}/д
    </span>
  );
}

function SentimentShiftBadge({ shift }: { shift: number }) {
  if (!shift || Math.abs(shift) < 0.05) return null;
  const positive = shift > 0;
  return (
    <span
      className={`text-xs px-1.5 py-0.5 rounded ${positive ? "bg-green-500/15 text-green-400" : "bg-red-500/15 text-red-400"}`}
      title={`Сдвиг тональности: ${shift > 0 ? "+" : ""}${shift.toFixed(2)}`}
    >
      {positive ? "↗" : "↘"} {Math.abs(shift).toFixed(2)}
    </span>
  );
}

function StructuredSummary({ summary }: { summary: any }) {
  if (!summary) return null;
  return (
    <div className="mt-3 space-y-2 text-sm">
      {/* Summary */}
      {summary.summary && (
        <div className="text-foreground font-medium">{summary.summary}</div>
      )}
      {/* Dynamics */}
      {summary.dynamics && (
        <div className="text-muted-foreground">{summary.dynamics}</div>
      )}
      {/* Impact + Forecast */}
      <div className="flex flex-col gap-1.5">
        {summary.impact && (
          <div className="flex items-start gap-2 text-xs">
            <span className="shrink-0 mt-0.5 text-yellow-400">🎯</span>
            <span className="text-muted-foreground">{summary.impact}</span>
          </div>
        )}
        {summary.forecast && (
          <div className="flex items-start gap-2 text-xs">
            <span className="shrink-0 mt-0.5 text-blue-400">🔮</span>
            <span className="text-muted-foreground">{summary.forecast}</span>
          </div>
        )}
      </div>
      {/* Tags */}
      {summary.tags && summary.tags.length > 0 && (
        <div className="flex flex-wrap gap-1 pt-1">
          {summary.tags.map((tag: string, i: number) => (
            <span key={i} className="text-xs px-2 py-0.5 rounded-full bg-white/5 text-muted-foreground">
              #{tag}
            </span>
          ))}
        </div>
      )}
      {/* Key actors */}
      {summary.key_actors && summary.key_actors.length > 0 && (
        <div className="text-xs text-muted-foreground">
          👤 {summary.key_actors.join(", ")}
        </div>
      )}
    </div>
  );
}

function RelatedThreads({ related }: { related: number[] }) {
  if (!related || related.length === 0) return null;
  return (
    <div className="flex items-center gap-1 text-xs text-muted-foreground">
      <span>🔗</span>
      <span>{related.length} связанных</span>
    </div>
  );
}

function MergedKeysBadge({ keys }: { keys: string[] }) {
  if (!keys || keys.length <= 1) return null;
  return (
    <span
      className="text-xs px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-400"
      title={`Объединено ${keys.length} вариантов: ${keys.join(", ")}`}
    >
      🔀 {keys.length} merged
    </span>
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
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [threadId, detail, open]);

  return (
    <div className="mt-3">
      <button
        onClick={loadTimeline}
        className="text-sm text-blue-400 hover:text-blue-300 transition-colors flex items-center gap-1"
      >
        {loading ? "⏳ Загрузка..." : open ? "📋 Скрыть" : "📋 Хронология"}
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
              <span className="px-1 py-0.5 rounded bg-zinc-800 text-muted-foreground shrink-0">
                {a.tier || "—"}
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
              <span
                className="whitespace-nowrap"
                style={{ color: sentimentColor(a.sentiment) }}
              >
                {a.sentiment > 0 ? "+" : ""}{a.sentiment?.toFixed(2) ?? "—"}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function ThreadCard({ thread }: { thread: any }) {
  const phase = PHASE_CONFIG[thread.arc_phase] || PHASE_CONFIG.emerging;
  const flag = COUNTRY_FLAGS[thread.country_code] || "🏳️";
  const [showFull, setShowFull] = useState(false);

  const hasSummary = thread.summary && (thread.summary.summary || thread.summary.dynamics);

  return (
    <div
      className="relative rounded-xl border border-white/10 p-5 transition-all duration-200 hover:border-white/20"
      style={{
        background: "linear-gradient(135deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.01) 100%)",
      }}
    >
      {/* Top row: country + velocity */}
      <div className="absolute top-4 right-4 flex items-center gap-2">
        <VelocityIndicator velocity={thread.velocity} />
        <span className="text-xs text-muted-foreground">
          {flag} {thread.country_name}
        </span>
      </div>

      {/* Badges row */}
      <div className="flex flex-wrap items-center gap-2 mb-2">
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
        <SentimentShiftBadge shift={thread.sentiment_shift} />
        <MergedKeysBadge keys={thread.merged_keys} />
        <RelatedThreads related={thread.related_threads} />
      </div>

      {/* Title */}
      <Link href={`/threads/${thread.id}`}>
        <h3 className="text-lg font-bold mb-1 hover:text-blue-400 transition-colors cursor-pointer pr-32">
          {thread.title}
        </h3>
      </Link>

      {/* Arc progress */}
      <ArcProgressBar phase={thread.arc_phase} />

      {/* Meta row */}
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground mt-1">
        <span>📰 {thread.article_count} статей</span>
        <span style={{ color: sentimentColor(thread.avg_sentiment ?? 0) }}>
          💬 {(thread.avg_sentiment ?? 0) > 0 ? "+" : ""}{(thread.avg_sentiment ?? 0).toFixed(2)}
        </span>
        <span>⚡ Уровень {thread.max_action_level}</span>
        <span>{formatDate(thread.first_seen)} → {formatDate(thread.last_seen)}</span>
      </div>

      {/* Structured summary or narrative */}
      {hasSummary ? (
        <>
          {showFull ? (
            <StructuredSummary summary={thread.summary} />
          ) : (
            <div className="mt-3 text-sm text-muted-foreground">
              {thread.summary.summary}
              <button
                onClick={() => setShowFull(true)}
                className="ml-2 text-blue-400 hover:text-blue-300 text-xs"
              >
                подробнее →
              </button>
            </div>
          )}
        </>
      ) : thread.narrative ? (
        <div
          className="mt-3 text-sm text-muted-foreground pl-3 border-l-2"
          style={{ borderColor: phase.color }}
        >
          {thread.narrative.length > 300
            ? thread.narrative.slice(0, 300) + "…"
            : thread.narrative}
        </div>
      ) : null}

      {/* Timeline */}
      <TimelineSection threadId={thread.id} />
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────

export default function ThreadsPage() {
  const [threads, setThreads] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedCountries, setSelectedCountries] = useState<string[]>([]);
  const [status, setStatus] = useState("developing");
  const [sort, setSort] = useState("importance");
  const [limit, setLimit] = useState(30);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string | number> = { limit, sort };
      if (status) params.status = status;
      if (selectedCountries.length === 1) params.country = selectedCountries[0];

      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || "http://YOUR_SERVER_IP:8100"}/api/v1/threads?${new URLSearchParams(
          Object.entries(params).reduce((a, [k, v]) => ({ ...a, [k]: String(v) }), {} as Record<string, string>)
        )}`
      );
      const data = await res.json();
      let filtered = data.threads || [];

      if (selectedCountries.length > 1) {
        filtered = filtered.filter((t: any) => selectedCountries.includes(t.country_code));
      }
      setThreads(filtered);
    } catch {
      setThreads([]);
    } finally {
      setLoading(false);
    }
  }, [selectedCountries, status, sort, limit]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const toggleCountry = (code: string) => {
    setSelectedCountries((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
    );
  };

  // Metrics
  const totalThreads = threads.length;
  const escalating = threads.filter((t) => t.arc_phase === "escalating").length;
  const totalArticles = threads.reduce((s, t) => s + t.article_count, 0);
  const avgVelocity = totalThreads > 0
    ? threads.reduce((s, t) => s + (t.velocity || 0), 0) / totalThreads
    : 0;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">🧵 Сюжетные нити</h1>
        <p className="text-muted-foreground mt-1">
          AI-кластеризация событий с дедупликацией, структурированными нарративами и кросс-страновыми связями
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
              className="text-xs px-3 py-1.5 rounded-full border border-red-500/30 text-red-400 hover:bg-red-500/10"
            >
              ✕ Сбросить
            </button>
          )}
        </div>

        {/* Status + Sort + Limit */}
        <div className="flex flex-wrap gap-4">
          <div className="flex gap-1">
            {STATUS_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setStatus(opt.value)}
                className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
                  status === opt.value ? "bg-white/10 text-white" : "text-muted-foreground hover:text-white"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <div className="flex gap-1">
            {SORT_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                onClick={() => setSort(opt.value)}
                className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
                  sort === opt.value ? "bg-blue-500/20 text-blue-400" : "text-muted-foreground hover:text-white"
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { icon: "🧵", label: "Сюжетов", value: totalThreads },
          { icon: "📈", label: "Эскалация", value: escalating, accent: escalating > 0 ? "text-orange-400" : "" },
          { icon: "📰", label: "Статей", value: totalArticles },
          { icon: "⚡", label: "Ср. скорость", value: avgVelocity.toFixed(1) + "/д" },
        ].map((m) => (
          <div
            key={m.label}
            className="rounded-xl border border-white/10 p-4 text-center"
            style={{ background: "linear-gradient(135deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.01) 100%)" }}
          >
            <div className="text-2xl mb-1">{m.icon}</div>
            <div className={`text-xl font-bold ${(m as any).accent || ""}`}>{m.value}</div>
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
          <h2 className="text-xl font-semibold">Сюжетных нитей не найдено</h2>
          <p className="text-muted-foreground text-sm">Попробуйте изменить фильтры</p>
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
