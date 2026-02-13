"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { ThreadDetail, ThreadTimelineArticle, getThread, getRelatedThreads } from "@/lib/api";
import { COUNTRY_FLAGS, PHASE_CONFIG, PHASE_ORDER, formatDate } from "@/lib/constants";

function sentimentColor(s: number): string {
  if (s > 0.5) return "#22c55e";
  if (s > -0.5) return "#eab308";
  return "#ef4444";
}

function actionIcon(level: number): string {
  return level >= 4 ? "💥" : "⚡";
}

function tierLabel(tier: string): string {
  const map: Record<string, string> = {
    mainstream: "🏛 Основной",
    regional: "📍 Региональный",
    niche: "🔹 Нишевый",
    state: "🏛 Государственный",
  };
  return map[tier] || tier;
}

export default function ThreadDetailPage() {
  const params = useParams();
  const id = Number(params.id);
  const [thread, setThread] = useState<ThreadDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [relatedThreads, setRelatedThreads] = useState<any[]>([]);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    getThread(id)
      .then((data) => {
        setThread(data);
        // Fetch related threads
        getRelatedThreads(id)
          .then((d) => setRelatedThreads(d.related || []))
          .catch(() => setRelatedThreads([]));
      })
      .catch(() => setError("Не удалось загрузить сюжет"))
      .finally(() => setLoading(false));
  }, [id]);

  if (loading) {
    return <div className="text-center py-16 text-muted-foreground">⏳ Загрузка...</div>;
  }

  if (error || !thread) {
    return (
      <div className="text-center py-16 space-y-3">
        <div className="text-4xl">❌</div>
        <p className="text-muted-foreground">{error || "Сюжет не найден"}</p>
        <Link href="/threads" className="text-blue-400 hover:underline text-sm">← Назад к сюжетам</Link>
      </div>
    );
  }

  const phase = PHASE_CONFIG[thread.arc_phase] || PHASE_CONFIG.emerging;
  const flag = COUNTRY_FLAGS[thread.country_code] || "🏳️";
  const activeIdx = PHASE_ORDER.indexOf(thread.arc_phase);

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      {/* Back link */}
      <Link href="/threads" className="text-sm text-blue-400 hover:underline">← Все сюжеты</Link>

      {/* Header */}
      <div>
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <span
            className="text-xs px-2 py-0.5 rounded-full font-medium"
            style={{ backgroundColor: phase.color + "22", color: phase.color }}
          >
            {phase.emoji} {phase.label}
          </span>
          <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-white/5 text-muted-foreground">
            {flag} {thread.country_name}
          </span>
          <span
            className="text-xs px-2 py-0.5 rounded-full font-medium"
            style={{
              backgroundColor: (thread.importance_score >= 20 ? "#ef4444" : thread.importance_score >= 10 ? "#f59e0b" : "#3b82f6") + "22",
              color: thread.importance_score >= 20 ? "#ef4444" : thread.importance_score >= 10 ? "#f59e0b" : "#3b82f6",
            }}
          >
            ★ {thread.importance_score.toFixed(0)}
          </span>
        </div>
        <h1 className="text-2xl font-bold">{thread.title}</h1>
      </div>

      {/* Arc progress */}
      <div>
        <div className="flex gap-1 mb-1">
          {PHASE_ORDER.map((p, i) => {
            const cfg = PHASE_CONFIG[p];
            return (
              <div
                key={p}
                className="flex-1 h-2.5 rounded-full transition-all"
                style={{ backgroundColor: i <= activeIdx ? cfg.color : "rgba(255,255,255,0.1)" }}
                title={cfg.label}
              />
            );
          })}
        </div>
        <div className="flex justify-between">
          {PHASE_ORDER.map((p, i) => {
            const cfg = PHASE_CONFIG[p];
            return (
              <div key={p} className="flex flex-col items-center gap-1">
                <div
                  className="w-3 h-3 rounded-full border-2 transition-all"
                  style={{
                    backgroundColor: i <= activeIdx ? cfg.color : "transparent",
                    borderColor: i <= activeIdx ? cfg.color : "rgba(255,255,255,0.15)",
                    boxShadow: i === activeIdx ? `0 0 8px ${cfg.color}66` : "none",
                  }}
                />
                <span
                  className="text-[10px]"
                  style={{ color: i <= activeIdx ? cfg.color : "rgba(255,255,255,0.3)" }}
                >
                  {cfg.emoji} {cfg.label}
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* Metadata grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {[
          { label: "Статей", value: thread.article_count, icon: "📰" },
          { label: "Макс. действие", value: `${actionIcon(thread.max_action_level)} ${thread.max_action_level}`, icon: "" },
          { label: "Тональность", value: thread.avg_sentiment.toFixed(2), icon: "", color: sentimentColor(thread.avg_sentiment) },
          { label: "Статус", value: thread.status === "developing" ? "🔄 Развивается" : thread.status === "resolved" ? "✅ Завершён" : "💤 Неактивен", icon: "" },
        ].map((m) => (
          <div
            key={m.label}
            className="rounded-xl border border-white/10 p-3 text-center"
            style={{ background: "linear-gradient(135deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.01) 100%)" }}
          >
            <div className="text-lg font-bold" style={m.color ? { color: m.color } : undefined}>
              {m.icon} {m.value}
            </div>
            <div className="text-xs text-muted-foreground">{m.label}</div>
          </div>
        ))}
      </div>

      {/* Dates */}
      <div className="text-sm text-muted-foreground">
        📅 {formatDate(thread.first_seen)} — {formatDate(thread.last_seen)}
      </div>

      {/* Narrative */}
      {thread.narrative && (
        <div
          className="rounded-xl border border-white/10 p-5"
          style={{ background: "linear-gradient(135deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.01) 100%)" }}
        >
          <h2 className="text-lg font-semibold mb-3">📝 Описание</h2>
          <div
            className="text-sm text-muted-foreground leading-relaxed pl-3 border-l-2"
            style={{ borderColor: phase.color }}
          >
            {thread.narrative}
          </div>
        </div>
      )}

      {/* Timeline */}
      {thread.timeline && thread.timeline.length > 0 && (
        <div
          className="rounded-xl border border-white/10 p-5"
          style={{ background: "linear-gradient(135deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.01) 100%)" }}
        >
          <h2 className="text-lg font-semibold mb-3">📋 Хронология ({thread.timeline.length} статей)</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-muted-foreground border-b border-white/10">
                  <th className="text-left py-2 pr-3">Дата</th>
                  <th className="text-left py-2 pr-3">Заголовок</th>
                  <th className="text-left py-2 pr-3">Источник</th>
                  <th className="text-left py-2 pr-3">Тир</th>
                  <th className="text-center py-2 pr-3">Действие</th>
                  <th className="text-right py-2">Тональность</th>
                </tr>
              </thead>
              <tbody>
                {thread.timeline.map((a: ThreadTimelineArticle) => (
                  <tr key={a.article_id} className="border-b border-white/5 hover:bg-white/5 transition-colors">
                    <td className="py-2 pr-3 text-muted-foreground whitespace-nowrap text-xs">
                      {formatDate(a.published_at)}
                    </td>
                    <td className="py-2 pr-3">
                      <a
                        href={a.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-blue-400 hover:underline"
                      >
                        {a.title}
                      </a>
                    </td>
                    <td className="py-2 pr-3 text-muted-foreground whitespace-nowrap text-xs">{a.source}</td>
                    <td className="py-2 pr-3 text-xs whitespace-nowrap">{tierLabel(a.tier)}</td>
                    <td className="py-2 pr-3 text-center text-xs">
                      {actionIcon(a.action_level)} {a.action_level}
                    </td>
                    <td className="py-2 text-right text-xs" style={{ color: sentimentColor(a.sentiment) }}>
                      {a.sentiment > 0 ? "+" : ""}{a.sentiment.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Related threads */}
      {relatedThreads.length > 0 && (
        <div
          className="rounded-xl border border-white/10 p-5"
          style={{ background: "linear-gradient(135deg, rgba(255,255,255,0.03) 0%, rgba(255,255,255,0.01) 100%)" }}
        >
          <h2 className="text-lg font-semibold mb-3">🔗 Связанные сюжеты</h2>
          <div className="grid gap-3 md:grid-cols-2">
            {relatedThreads.map((rt: any) => {
              const rtPhase = PHASE_CONFIG[rt.arc_phase] || PHASE_CONFIG.emerging;
              const rtFlag = COUNTRY_FLAGS[rt.country_code] || "🏳️";
              return (
                <Link key={rt.id} href={`/threads/${rt.id}`}>
                  <div className="rounded-lg border border-white/[0.08] p-3 hover:border-white/20 transition-all cursor-pointer">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-xs" style={{ color: rtPhase.color }}>{rtPhase.emoji}</span>
                      <span className="text-xs text-muted-foreground">{rtFlag} {rt.country_name || rt.country_code}</span>
                      <span
                        className="text-xs px-1.5 py-0.5 rounded-full ml-auto font-mono"
                        style={{
                          backgroundColor: (rt.importance_score >= 20 ? "#ef4444" : rt.importance_score >= 10 ? "#f59e0b" : "#3b82f6") + "15",
                          color: rt.importance_score >= 20 ? "#ef4444" : rt.importance_score >= 10 ? "#f59e0b" : "#3b82f6",
                        }}
                      >
                        ★ {(rt.importance_score ?? 0).toFixed(0)}
                      </span>
                    </div>
                    <h4 className="text-sm font-medium hover:text-blue-400 transition-colors line-clamp-2">{rt.title}</h4>
                  </div>
                </Link>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
