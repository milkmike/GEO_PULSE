"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { getThread, formatDate, API_URL, type ThreadDetail, type ThreadTimelineArticle } from "@/lib/api";
import SectionHeader from "@/components/SectionHeader";
import { glossary } from "@/lib/glossary";

// ── Constants ──────────────────────────────────────────

const FLAGS: Record<string, string> = {
  KZ: "🇰🇿", AM: "🇦🇲", UZ: "🇺🇿", KG: "🇰🇬", TJ: "🇹🇯",
  TM: "🇹🇲", AZ: "🇦🇿", GE: "🇬🇪", MD: "🇲🇩", BY: "🇧🇾",
};

const COUNTRIES = [
  { code: "KZ", name: "Казахстан" }, { code: "UZ", name: "Узбекистан" },
  { code: "BY", name: "Беларусь" }, { code: "AZ", name: "Азербайджан" },
  { code: "AM", name: "Армения" }, { code: "GE", name: "Грузия" },
  { code: "KG", name: "Кыргызстан" }, { code: "TJ", name: "Таджикистан" },
  { code: "TM", name: "Туркменистан" }, { code: "MD", name: "Молдова" },
];

const PHASE_CFG: Record<string, { emoji: string; color: string; label: string }> = {
  emerging:   { emoji: "🌱", color: "#3b82f6", label: "Зарождение" },
  escalating: { emoji: "📈", color: "#f59e0b", label: "Эскалация" },
  peak:       { emoji: "🔥", color: "#ef4444", label: "Пик" },
  cooling:    { emoji: "❄️", color: "#06b6d4", label: "Затухание" },
  resolved:   { emoji: "✅", color: "#22c55e", label: "Завершён" },
};
const PHASE_ORDER = ["emerging", "escalating", "peak", "cooling", "resolved"];

function sentColor(s: number): string {
  return s > 0.3 ? "#22c55e" : s > -0.3 ? "#eab308" : "#ef4444";
}

// ── Structured Summary ─────────────────────────────────

function StructuredSummary({ summary, compact }: { summary: any; compact?: boolean }) {
  if (!summary) return null;
  if (compact) {
    return <div className="text-sm text-muted-foreground">{summary.summary}</div>;
  }
  return (
    <div className="space-y-2 text-sm">
      {summary.summary && <div className="text-foreground font-medium">{summary.summary}</div>}
      {summary.dynamics && <div className="text-muted-foreground">{summary.dynamics}</div>}
      <div className="flex flex-col gap-1.5">
        {summary.impact && (
          <div className="flex items-start gap-2 text-xs">
            <span className="shrink-0 text-yellow-400">🎯</span>
            <span className="text-muted-foreground">{summary.impact}</span>
          </div>
        )}
        {summary.forecast && (
          <div className="flex items-start gap-2 text-xs">
            <span className="shrink-0 text-blue-400">🔮</span>
            <span className="text-muted-foreground">{summary.forecast}</span>
          </div>
        )}
      </div>
      {summary.tags?.length > 0 && (
        <div className="flex flex-wrap gap-1 pt-1">
          {summary.tags.map((tag: string, i: number) => (
            <span key={i} className="text-xs px-2 py-0.5 rounded-full bg-white/5 text-muted-foreground">#{tag}</span>
          ))}
        </div>
      )}
      {summary.key_actors?.length > 0 && (
        <div className="text-xs text-muted-foreground">👤 {summary.key_actors.join(", ")}</div>
      )}
    </div>
  );
}

// ── Arc bar ────────────────────────────────────────────

function ArcBar({ phase, height }: { phase: string; height?: string }) {
  const idx = PHASE_ORDER.indexOf(phase);
  return (
    <div className="my-2">
      {/* Bar segments */}
      <div className="flex gap-1">
        {PHASE_ORDER.map((p, i) => (
          <div
            key={p}
            className={`flex-1 rounded-full ${height || "h-1.5"}`}
            style={{ backgroundColor: i <= idx ? PHASE_CFG[p].color : "rgba(255,255,255,0.06)" }}
          />
        ))}
      </div>
      {/* Dots + labels */}
      <div className="flex justify-between mt-1.5">
        {PHASE_ORDER.map((p, i) => (
          <div key={p} className="flex flex-col items-center gap-0.5">
            <div
              className="w-2.5 h-2.5 rounded-full border-2 transition-all"
              style={{
                backgroundColor: i <= idx ? PHASE_CFG[p].color : "transparent",
                borderColor: i <= idx ? PHASE_CFG[p].color : "rgba(255,255,255,0.15)",
                boxShadow: i === idx ? `0 0 6px ${PHASE_CFG[p].color}66` : "none",
              }}
            />
            <span className="text-[9px]" style={{ color: i <= idx ? PHASE_CFG[p].color : "rgba(255,255,255,0.2)" }}>
              {PHASE_CFG[p].label}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Timeline ───────────────────────────────────────────

function Timeline({ threadId }: { threadId: number }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<ThreadDetail | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    if (detail) { setOpen(!open); return; }
    setLoading(true);
    try { const d = await getThread(threadId); setDetail(d); setOpen(true); }
    catch {} finally { setLoading(false); }
  }, [threadId, detail, open]);

  return (
    <div className="mt-3">
      <button onClick={load} className="text-sm text-blue-400 hover:text-blue-300 transition-colors">
        {loading ? "⏳" : open ? "▾ Скрыть хронологию" : "▸ Хронология"}
      </button>
      {open && detail?.timeline && (
        <div className="mt-2 space-y-1 max-h-80 overflow-y-auto">
          {detail.timeline.map((a: ThreadTimelineArticle) => (
            <div key={a.article_id} className="flex items-start gap-2 text-xs py-1.5 px-2 rounded bg-white/5">
              <span className="text-muted-foreground whitespace-nowrap">{formatDate(a.published_at)}</span>
              <span className="px-1 py-0.5 rounded bg-zinc-800 text-muted-foreground shrink-0">{a.tier || "—"}</span>
              <a href={a.url} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline flex-1 truncate">{a.title}</a>
              <span className="text-muted-foreground">{a.source}</span>
              <span style={{ color: sentColor(a.sentiment) }}>{a.sentiment?.toFixed(2) ?? "—"}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Hero Card (главный сюжет) ──────────────────────────

function HeroCard({ thread }: { thread: any }) {
  const phase = PHASE_CFG[thread.arc_phase] || PHASE_CFG.emerging;
  const [expanded, setExpanded] = useState(true);

  return (
    <div
      className="relative rounded-2xl border-2 p-6 md:p-8 overflow-hidden"
      style={{
        borderColor: phase.color + "44",
        background: `linear-gradient(135deg, ${phase.color}08 0%, rgba(10,10,15,0.95) 60%)`,
      }}
    >
      {/* Glow */}
      <div
        className="absolute top-0 right-0 w-64 h-64 rounded-full blur-[100px] opacity-20"
        style={{ backgroundColor: phase.color }}
      />

      <div className="relative z-10">
        {/* Top badges */}
        <div className="flex flex-wrap items-center gap-2 mb-3">
          <span className="text-xs px-3 py-1 rounded-full font-bold uppercase tracking-wider bg-red-500/20 text-red-400 border border-red-500/30">
            🔥 Главный сюжет
          </span>
          <span className="text-xs px-2 py-0.5 rounded-full" style={{ backgroundColor: phase.color + "22", color: phase.color }}>
            {phase.emoji} {phase.label}
          </span>
          <span className="text-xs text-muted-foreground">{FLAGS[thread.country_code]} {thread.country_name}</span>
          {thread.velocity > 1 && (
            <span className="text-xs text-orange-400 font-medium">⚡ {thread.velocity.toFixed(1)} ст/день</span>
          )}
        </div>

        {/* Title */}
        <Link href={`/threads/${thread.id}`}>
          <h2 className="text-2xl md:text-3xl font-bold hover:text-blue-400 transition-colors cursor-pointer mb-3 leading-tight">
            {thread.title}
          </h2>
        </Link>

        <ArcBar phase={thread.arc_phase} height="h-2" />

        {/* Stats */}
        <div className="flex flex-wrap gap-4 text-sm text-muted-foreground mt-3 mb-4">
          <span>📰 {thread.article_count} статей</span>
          <span style={{ color: sentColor(thread.avg_sentiment ?? 0) }}>
            💬 {(thread.avg_sentiment ?? 0).toFixed(2)}
          </span>
          <span>⚡ Уровень {thread.max_action_level}</span>
          <span>★ {thread.importance_score.toFixed(0)} pts</span>
          {thread.merged_keys?.length > 1 && <span className="text-purple-400">🔀 {thread.merged_keys.length} merged</span>}
          {thread.related_threads?.length > 0 && <span>🔗 {thread.related_threads.length} связанных</span>}
          <span>{formatDate(thread.first_seen)} → {formatDate(thread.last_seen)}</span>
        </div>

        {/* Summary */}
        {thread.summary ? (
          <StructuredSummary summary={thread.summary} />
        ) : thread.narrative ? (
          <div className="text-sm text-muted-foreground pl-4 border-l-2" style={{ borderColor: phase.color }}>
            {thread.narrative}
          </div>
        ) : null}

        <Timeline threadId={thread.id} />
      </div>
    </div>
  );
}

// ── Spotlight Card (геополитический фокус) ─────────────

function SpotlightCard({ thread, label }: { thread: any; label?: string }) {
  const phase = PHASE_CFG[thread.arc_phase] || PHASE_CFG.emerging;
  const [showFull, setShowFull] = useState(false);

  return (
    <div
      className="rounded-xl border p-5 transition-all hover:border-white/20"
      style={{
        borderColor: phase.color + "30",
        background: `linear-gradient(135deg, ${phase.color}05 0%, rgba(10,10,15,0.9) 100%)`,
      }}
    >
      {/* Badges */}
      <div className="flex flex-wrap items-center gap-2 mb-2">
        {label && (
          <span className="text-xs px-2 py-0.5 rounded-full font-semibold uppercase tracking-wider bg-blue-500/15 text-blue-400 border border-blue-500/25">
            {label}
          </span>
        )}
        <span className="text-xs px-2 py-0.5 rounded-full" style={{ backgroundColor: phase.color + "22", color: phase.color }}>
          {phase.emoji} {phase.label}
        </span>
        <span className="text-xs text-muted-foreground">{FLAGS[thread.country_code]} {thread.country_name}</span>
        {thread.velocity > 2 && <span className="text-xs text-orange-400">⚡ {thread.velocity.toFixed(1)}/д</span>}
      </div>

      <Link href={`/threads/${thread.id}`}>
        <h3 className="text-lg font-bold hover:text-blue-400 transition-colors cursor-pointer mb-1">
          {thread.title}
        </h3>
      </Link>

      <ArcBar phase={thread.arc_phase} />

      {/* Stats */}
      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground mt-1">
        <span>📰 {thread.article_count}</span>
        <span style={{ color: sentColor(thread.avg_sentiment ?? 0) }}>💬 {(thread.avg_sentiment ?? 0).toFixed(2)}</span>
        <span>★ {thread.importance_score.toFixed(0)}</span>
        <span>{formatDate(thread.first_seen)} → {formatDate(thread.last_seen)}</span>
      </div>

      {/* Summary */}
      {thread.summary ? (
        showFull ? (
          <div className="mt-3"><StructuredSummary summary={thread.summary} /></div>
        ) : (
          <div className="mt-3 text-sm text-muted-foreground">
            {thread.summary.summary}
            <button onClick={() => setShowFull(true)} className="ml-2 text-blue-400 text-xs">подробнее →</button>
          </div>
        )
      ) : thread.narrative ? (
        <div className="mt-3 text-sm text-muted-foreground line-clamp-3">{thread.narrative}</div>
      ) : null}

      <Timeline threadId={thread.id} />
    </div>
  );
}

// ── Compact Card (остальные) ───────────────────────────

function CompactCard({ thread }: { thread: any }) {
  const phase = PHASE_CFG[thread.arc_phase] || PHASE_CFG.emerging;

  return (
    <div className="flex items-start gap-4 rounded-lg border border-white/8 p-4 hover:border-white/15 transition-all">
      {/* Importance score */}
      <div className="shrink-0 w-12 h-12 rounded-lg flex items-center justify-center text-sm font-bold"
        style={{ backgroundColor: phase.color + "15", color: phase.color }}
      >
        {thread.importance_score.toFixed(0)}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-xs" style={{ color: phase.color }}>{phase.emoji}</span>
          <span className="text-xs text-muted-foreground">{FLAGS[thread.country_code]} {thread.country_name}</span>
          {thread.velocity > 2 && <span className="text-xs text-orange-400">⚡ {thread.velocity.toFixed(1)}/д</span>}
          {thread.merged_keys?.length > 1 && <span className="text-xs text-purple-400">🔀 {thread.merged_keys.length}</span>}
        </div>
        <Link href={`/threads/${thread.id}`}>
          <h4 className="font-semibold hover:text-blue-400 transition-colors cursor-pointer truncate">{thread.title}</h4>
        </Link>
        <div className="flex flex-wrap gap-3 text-xs text-muted-foreground mt-1">
          <span>📰 {thread.article_count}</span>
          <span style={{ color: sentColor(thread.avg_sentiment ?? 0) }}>{(thread.avg_sentiment ?? 0).toFixed(2)}</span>
          <span>{formatDate(thread.last_seen)}</span>
        </div>
        {thread.summary?.summary && (
          <div className="mt-1 text-xs text-muted-foreground line-clamp-2">{thread.summary.summary}</div>
        )}
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────

export default function ThreadsPage() {
  const [allThreads, setAllThreads] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterCountry, setFilterCountry] = useState<string[]>([]);
  const [filterStatus, setFilterStatus] = useState("");

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        const res = await fetch(`${API_URL}/api/v1/threads?limit=50&sort=importance`);
        const data = await res.json();
        setAllThreads(data.threads || []);
      } catch { setAllThreads([]); }
      finally { setLoading(false); }
    })();
  }, []);

  // Apply filters
  let threads = allThreads;
  if (filterCountry.length > 0) {
    threads = threads.filter((t) => filterCountry.includes(t.country_code));
  }
  if (filterStatus) {
    threads = threads.filter((t) => t.status === filterStatus);
  }

  // Split into sections
  // Hero: highest media impact — sqrt dampens velocity spikes, cap prevents tiny threads with huge velocity from dominating
  const mediaScore = (t: any) => {
    const v = Math.min(t.velocity || 0.1, t.article_count * 5); // velocity can't exceed 5× article count
    const phaseMult = t.arc_phase === "escalating" ? 2 : t.arc_phase === "peak" ? 1.5 : 1;
    return t.article_count * Math.sqrt(v) * phaseMult;
  };
  const sorted = [...threads].sort((a, b) => mediaScore(b) - mediaScore(a));

  const hero = sorted[0] || null;

  // Spotlight: top 3 by importance (excluding hero), focus on geopolitical weight
  const withoutHero = threads.filter((t) => t !== hero);
  const spotlights = withoutHero
    .sort((a, b) => b.importance_score - a.importance_score)
    .slice(0, 3);

  // Trending: high velocity, not already in hero/spotlights
  const featured = new Set([hero?.id, ...spotlights.map((s) => s.id)]);
  const trending = withoutHero
    .filter((t) => !featured.has(t.id) && (t.velocity || 0) > 1.5)
    .sort((a, b) => (b.velocity || 0) - (a.velocity || 0))
    .slice(0, 4);

  // Rest
  const allFeatured = new Set([...featured, ...trending.map((t) => t.id)]);
  const rest = threads.filter((t) => !allFeatured.has(t.id));

  // Metrics
  const escalating = threads.filter((t) => t.arc_phase === "escalating" || t.arc_phase === "peak").length;
  const totalArticles = threads.reduce((s, t) => s + t.article_count, 0);

  const toggle = (code: string) => {
    setFilterCountry((prev) => prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <div className="text-muted-foreground">⏳ Загрузка сюжетов...</div>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-end justify-between">
        <div>
          <SectionHeader
            icon="📖"
            title="Сюжеты"
            description={glossary.threads.short}
            infoTitle="Сюжеты"
            infoContent={glossary.threads.detail}
          />
          <p className="text-muted-foreground mt-1 text-sm">
            {threads.length} сюжетов · {totalArticles} статей · {escalating} в эскалации
          </p>
        </div>
      </div>

      {/* Filters */}
      <div className="space-y-3">
        <div className="flex flex-wrap gap-2">
          {COUNTRIES.map((c) => (
            <button
              key={c.code}
              onClick={() => toggle(c.code)}
              className={`text-xs px-3 py-1.5 rounded-full border transition-all ${
                filterCountry.includes(c.code)
                  ? "bg-blue-500/20 border-blue-500/50 text-blue-400"
                  : "border-white/10 text-muted-foreground hover:border-white/30"
              }`}
            >
              {FLAGS[c.code]} {c.name}
            </button>
          ))}
          {filterCountry.length > 0 && (
            <button onClick={() => setFilterCountry([])} className="text-xs px-3 py-1.5 rounded-full border border-red-500/30 text-red-400">
              ✕
            </button>
          )}
        </div>
        <div className="flex gap-1">
          {[
            { value: "", label: "Все" },
            { value: "developing", label: "🔄 Активные" },
            { value: "resolved", label: "✅ Завершённые" },
            { value: "dormant", label: "💤 Спящие" },
          ].map((o) => (
            <button
              key={o.value}
              onClick={() => setFilterStatus(o.value)}
              className={`text-xs px-3 py-1.5 rounded-lg transition-all ${
                filterStatus === o.value ? "bg-white/10 text-white" : "text-muted-foreground hover:text-white"
              }`}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {/* Hero */}
      {hero && <HeroCard thread={hero} />}

      {/* Spotlight: Геополитический фокус */}
      {spotlights.length > 0 && (
        <div>
          <SectionHeader
            icon="🎯"
            title="Геополитический фокус"
            description="Топ-3 сюжета по геополитической значимости (importance score)"
            infoTitle="Importance Score"
            infoContent={glossary.importanceScore.detail}
          />
          <div className="grid gap-4 md:grid-cols-3 mt-4">
            {spotlights.map((t, i) => (
              <SpotlightCard key={t.id} thread={t} />
            ))}
          </div>
        </div>
      )}

      {/* Trending */}
      {trending.length > 0 && (
        <div>
          <SectionHeader
            icon="📈"
            title="Быстрорастущие"
            description="Сюжеты с высокой скоростью публикаций — набирают обороты прямо сейчас"
            infoTitle="Velocity (скорость)"
            infoContent={
              <>
                <p><strong>Velocity</strong> — количество статей в день по данному сюжету.</p>
                <p>{'>'} 1.5 ст/день = сюжет активно развивается. {'>'} 5 = информационный шторм.</p>
                <p>Быстрорастущие сюжеты часто сигнализируют о новых событиях, которые ещё не отразились в температуре.</p>
              </>
            }
          />
          <div className="grid gap-3 md:grid-cols-2 mt-4">
            {trending.map((t) => (
              <CompactCard key={t.id} thread={t} />
            ))}
          </div>
        </div>
      )}

      {/* Rest */}
      {rest.length > 0 && (
        <div>
          <h2 className="text-lg font-bold mb-4 text-muted-foreground uppercase tracking-wider text-xs">
            Все сюжеты
          </h2>
          <div className="space-y-2">
            {rest.map((t) => (
              <CompactCard key={t.id} thread={t} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
