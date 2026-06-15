"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Vox = Awaited<ReturnType<typeof api.vox>>;

const EMO_RU: Record<string, string> = {
  anger: "гнев",
  fear: "страх",
  joy: "радость",
  sadness: "грусть",
  disgust: "отвращение",
  surprise: "удивление",
  neutral: "нейтрально",
};

export default function VoxPanel({ code }: { code: string }) {
  const [v, setV] = useState<Vox | null>(null);

  useEffect(() => {
    api.vox(code).then(setV).catch(() => setV(null));
  }, [code]);

  if (!v) return null;
  const last = v.timeline.at(-1);
  if (!v.timeline.length || !last) {
    return (
      <section className="card">
        <div className="card-title px-4 pb-1 pt-3">Народные настроения</div>
        <div className="px-4 pb-3 text-xs text-dim">
          Нет данных по комментариям (собираются для стран СНГ).
        </div>
      </section>
    );
  }

  const emoTotal = Object.values(v.emotions).reduce((a, b) => a + b, 0) || 1;
  const emos = Object.entries(v.emotions)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  return (
    <section className="card">
      <div className="card-title px-4 pb-1 pt-3">Народные настроения (комментарии)</div>
      <div className="px-4 pb-3">
        <div className="flex gap-6">
          {last.vox_temperature != null && (
            <div>
              <div className="tnum text-2xl" style={{ color: last.vox_temperature >= 0 ? "var(--color-partner)" : "var(--color-hostile)" }}>
                {last.vox_temperature > 0 ? "+" : ""}
                {last.vox_temperature.toFixed(0)}°
              </div>
              <div className="text-[11px] uppercase tracking-wide text-dim">температура народа</div>
            </div>
          )}
          {last.elite_gap != null && (
            <div>
              <div className="tnum text-2xl text-ru-white">
                {last.elite_gap > 0 ? "+" : ""}
                {last.elite_gap.toFixed(0)}
              </div>
              <div className="text-[11px] uppercase tracking-wide text-dim">разрыв с медиа</div>
            </div>
          )}
        </div>

        {!!emos.length && (
          <div className="mt-3 space-y-1.5">
            {emos.map(([emo, n]) => (
              <div key={emo} className="flex items-center gap-2 text-[12px]">
                <span className="w-24 shrink-0 text-dim">{EMO_RU[emo] ?? emo}</span>
                <div className="h-2.5 flex-1 rounded bg-panel2">
                  <div
                    className="h-full rounded bg-accent/70"
                    style={{ width: `${(n / emoTotal) * 100}%` }}
                  />
                </div>
                <span className="tnum w-10 text-right text-dim">{Math.round((n / emoTotal) * 100)}%</span>
              </div>
            ))}
          </div>
        )}

        {!!v.top_topics.length && (
          <div className="mt-3 flex flex-wrap gap-1.5">
            {v.top_topics.slice(0, 8).map((t) => (
              <span key={t.topic} className="rounded border border-line px-2 py-0.5 text-[11px] text-dim">
                {t.topic} · {t.count}
              </span>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
