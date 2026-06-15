"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

const TIER_RU: Record<string, string> = {
  official: "официальные",
  state: "государственные",
  mainstream: "мейнстрим",
  analytics: "аналитика",
  independent: "независимые",
  social: "соцсети",
  opposition: "оппозиция",
  domestic_opposition: "оппозиция",
  western_proxy: "зап. прокси",
};

type Tier = { tier: string; sentiment: number; articles: number; sources: number };

export default function TierDivergencePanel({ code }: { code: string }) {
  const [tiers, setTiers] = useState<Tier[] | null>(null);

  useEffect(() => {
    api.tierDivergence(code).then((d) => setTiers(d.tiers)).catch(() => setTiers([]));
  }, [code]);

  if (!tiers) return null;
  if (!tiers.length)
    return (
      <section className="card">
        <div className="card-title px-4 pb-1 pt-3">Расхождение по тирам</div>
        <div className="px-4 pb-3 text-xs text-dim">Недостаточно размеченных статей.</div>
      </section>
    );

  const vals = tiers.map((t) => t.sentiment);
  const spread = Math.max(...vals) - Math.min(...vals);

  return (
    <section className="card">
      <div className="flex items-baseline justify-between px-4 pb-1 pt-3">
        <span className="card-title">Расхождение по тирам</span>
        <span className="tnum text-[11px] text-dim">разброс {spread.toFixed(1)}</span>
      </div>
      <div className="space-y-2 px-4 pb-3">
        {tiers.map((t) => {
          const pos = t.sentiment >= 0;
          const w = Math.min(50, (Math.abs(t.sentiment) / 3) * 50);
          return (
            <div key={t.tier} className="flex items-center gap-2 text-[12px]">
              <span className="w-28 shrink-0 text-dim">{TIER_RU[t.tier] ?? t.tier}</span>
              <div className="relative h-3 flex-1 rounded bg-panel2">
                <div className="absolute left-1/2 top-0 h-full w-px bg-line" />
                <div
                  className="absolute top-0 h-full rounded"
                  style={{
                    width: `${w}%`,
                    [pos ? "left" : "right"]: "50%",
                    background: pos ? "var(--color-partner)" : "var(--color-hostile)",
                  }}
                />
              </div>
              <span className="tnum w-10 text-right" style={{ color: pos ? "var(--color-partner)" : "var(--color-hostile)" }}>
                {t.sentiment > 0 ? "+" : ""}
                {t.sentiment.toFixed(1)}
              </span>
              <span className="tnum w-12 text-right text-dim">{t.articles}</span>
            </div>
          );
        })}
      </div>
      <div className="px-4 pb-3 text-[11px] text-dim">
        Тон статей о России по типам источников (−3 враждебно … +3 благожелательно), число статей справа.
      </div>
    </section>
  );
}
