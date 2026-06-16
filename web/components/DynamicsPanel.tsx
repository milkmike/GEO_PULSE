"use client";

import { useEffect, useMemo, useState } from "react";
import type { Dossier, Headline, Thread, TopicStat } from "@/lib/types";
import { fmt, fmtDay, safeHttpUrl } from "@/lib/format";
import { api } from "@/lib/api";

// arc_phase → русская подпись + цвет бейджа.
const ARC_RU: Record<string, { label: string; color: string }> = {
  emerging: { label: "зарождается", color: "#9ca3af" },
  escalating: { label: "обостряется", color: "#f97316" },
  peak: { label: "пик", color: "#ef4444" },
  cooling: { label: "затухает", color: "#fbbf24" },
  resolved: { label: "разрешён", color: "#34d399" },
};

function mean(xs: number[]): number | null {
  const v = xs.filter((x) => Number.isFinite(x));
  return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null;
}

export default function DynamicsPanel({
  code, dossier, headlines, topics,
}: {
  code: string;
  dossier: Dossier;
  headlines: { source: string; headlines: Headline[] } | null;
  topics: TopicStat[];
}) {
  const [threads, setThreads] = useState<Thread[] | null>(null);

  useEffect(() => {
    setThreads(null);
    api.countryThreads(code).then((d) => setThreads(d.threads)).catch(() => setThreads([]));
  }, [code]);

  // Вердикт направления — универсально, из dossier (RRI-дельта + тренд тона GDELT).
  const verdict = useMemo(() => {
    const d7 = dossier.index?.delta_7d ?? null;
    const d24 = dossier.index?.delta_24h ?? null;

    const tones = dossier.gdelt.map((g) => g.tone).filter((t): t is number => t != null);
    const last7 = mean(tones.slice(-7));
    const prev7 = mean(tones.slice(-14, -7));
    const toneTrend = last7 != null && prev7 != null ? last7 - prev7 : null;

    const vols = dossier.gdelt.map((g) => g.volume).filter((v): v is number => v != null);
    const lastVol = vols.at(-1) ?? null;
    const avgVol = mean(vols);
    const spike = lastVol != null && avgVol != null && avgVol > 0 && lastVol >= avgVol * 1.6;

    // Направление: приоритет — недельная дельта RRI, фолбэк — тренд тона GDELT.
    let dir: "up" | "down" | "flat" = "flat";
    if (d7 != null && Math.abs(d7) >= 1) dir = d7 > 0 ? "up" : "down";
    else if (d7 == null && toneTrend != null && Math.abs(toneTrend) >= 0.3)
      dir = toneTrend > 0 ? "up" : "down";

    const label = dir === "up" ? "Теплеют" : dir === "down" ? "Охлаждаются" : "Стабильны";
    const color = dir === "up" ? "var(--color-partner)"
      : dir === "down" ? "var(--color-hostile)" : "#9ca3af";
    const arrow = dir === "up" ? "↗" : dir === "down" ? "↘" : "→";

    return { label, color, arrow, d7, d24, toneTrend, spike };
  }, [dossier]);

  const loading = threads === null;
  const list = threads ?? [];

  return (
    <section className="card md:col-span-2">
      <div className="card-title px-4 pb-1 pt-3">Динамика отношений с Россией</div>

      {/* Вердикт направления */}
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 px-4 pb-2">
        <span className="text-lg font-semibold" style={{ color: verdict.color }}>
          {verdict.arrow} {verdict.label}
        </span>
        {verdict.d7 != null && (
          <span className="tnum text-xs text-dim">
            RRI: 7д {fmt(verdict.d7)} · сутки {fmt(verdict.d24)}
          </span>
        )}
        {verdict.toneTrend != null && (
          <span className="tnum text-xs text-dim">тон GDELT {fmt(verdict.toneTrend, 2)}</span>
        )}
        {verdict.spike && (
          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[11px] text-amber-400">
            всплеск внимания
          </span>
        )}
      </div>

      {/* Что будоражит: сюжеты (tier-1) или фолбэк (фон + заголовки) */}
      {list.length > 0 ? (
        <div className="space-y-2 px-4 pb-3">
          {list.slice(0, 4).map((t) => {
            const arc = ARC_RU[t.arc_phase] ?? { label: t.arc_phase, color: "#9ca3af" };
            const shift = t.sentiment_shift;
            return (
              <div key={t.id} className="rounded-lg border border-line bg-panel2 px-3 py-2">
                <div className="flex items-baseline gap-2">
                  <span
                    className="shrink-0 rounded px-1.5 py-0.5 text-[10px]"
                    style={{ color: arc.color, border: `1px solid ${arc.color}55` }}
                  >
                    {arc.label}
                  </span>
                  <span className="text-[13px] font-medium">{t.summary?.title ?? t.title}</span>
                  <span className="ml-auto shrink-0 text-[11px] text-dim">
                    {t.velocity > 0 && `${t.velocity.toFixed(0)} ст./день`}
                    {Math.abs(shift) >= 0.3 && (
                      <span style={{ color: shift > 0 ? "var(--color-partner)" : "var(--color-hostile)" }}>
                        {" "}{shift > 0 ? "↗ тон" : "↘ тон"}
                      </span>
                    )}
                  </span>
                </div>
                {t.summary?.dynamics && (
                  <p className="mt-1 text-[12px] text-gray-300">{t.summary.dynamics}</p>
                )}
                {t.summary?.forecast && (
                  <p className="mt-1 text-[12px] text-dim">
                    <span className="text-zinc-500">куда движется:</span> {t.summary.forecast}
                  </p>
                )}
                {t.articles.length > 0 && (
                  <ul className="mt-1.5 space-y-0.5">
                    {t.articles.map((a, i) => {
                      const url = a.url ? safeHttpUrl(a.url) : null;
                      return (
                        <li key={i} className="truncate text-[12px]">
                          {url ? (
                            <a href={url} target="_blank" rel="noopener noreferrer"
                               className="text-dim hover:text-accent">↗ {a.title}</a>
                          ) : (
                            <span className="text-dim">{a.title}</span>
                          )}
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      ) : loading ? (
        <div className="px-4 pb-3 text-[12px] text-dim">Загрузка сюжетов…</div>
      ) : (
        <div className="px-4 pb-3">
          <div className="text-[11px] text-dim">
            Глубоких сюжетов нет (GDELT-мониторинг). Свежий информационный фон:
          </div>
          {topics.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {topics.slice(0, 6).map((t) => (
                <span
                  key={t.topic}
                  className="rounded-full border border-line bg-panel2 px-2.5 py-0.5 text-[11px]"
                  title={`тон ${fmt(t.avg_sentiment)}`}
                >
                  {t.label} <span className="text-dim">×{t.articles}</span>
                </span>
              ))}
            </div>
          )}
          {headlines && headlines.headlines.length > 0 ? (
            <ul className="mt-2 space-y-1">
              {headlines.headlines.slice(0, 5).map((h, i) => {
                const url = h.url ? safeHttpUrl(h.url) : null;
                return (
                  <li key={i} className="text-[12px]">
                    {url ? (
                      <a href={url} target="_blank" rel="noopener noreferrer"
                         className="hover:text-accent">↗ {h.title}</a>
                    ) : (
                      <span>{h.title}</span>
                    )}
                    <span className="text-[10px] text-zinc-600">
                      {h.source ? ` — ${h.source}` : ""}
                      {h.published_at ? ` · ${fmtDay(h.published_at)}` : ""}
                    </span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="mt-2 text-[12px] text-dim">Свежих материалов пока нет.</div>
          )}
        </div>
      )}
    </section>
  );
}
