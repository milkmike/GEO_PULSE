"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Radar = Awaited<ReturnType<typeof api.ruRadar>>;

// pressure 0..2 → ok / moderate / high
const PRESSURE_COLOR = ["var(--color-partner)", "var(--color-cooling)", "var(--color-hostile)"];

function pct(v: number | null | undefined): string {
  if (v == null) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}%`;
}

/** Tiny dependency-free SVG sparkline. */
function Spark({ data }: { data: { d: string; v: number }[] }) {
  if (data.length < 2) return null;
  const vs = data.map((p) => p.v);
  const min = Math.min(...vs);
  const rng = Math.max(...vs) - min || 1;
  const W = 140, H = 28;
  const pts = data
    .map((p, i) => `${((i / (data.length - 1)) * W).toFixed(1)},${(H - ((p.v - min) / rng) * H).toFixed(1)}`)
    .join(" ");
  const up = vs[vs.length - 1] >= vs[0];
  return (
    <svg width={W} height={H} className="mt-2" aria-hidden="true">
      <polyline
        points={pts}
        fill="none"
        stroke={up ? "var(--color-partner)" : "var(--color-hostile)"}
        strokeWidth="1.5"
      />
    </svg>
  );
}

export default function RadarPanel() {
  const [r, setR] = useState<Radar | null>(null);

  useEffect(() => {
    api.ruRadar().then(setR).catch(() => setR({ has_data: false }));
  }, []);

  if (!r) return null;

  return (
    <section className="card">
      <div className="card-title px-4 pb-1 pt-3">Радар изоляции РФ</div>
      {!r.has_data ? (
        <div className="px-4 pb-3 text-xs text-dim">Рыночные данные ещё собираются.</div>
      ) : (
        <div className="px-4 pb-3">
          <span
            className="inline-block rounded-full border px-2.5 py-0.5 text-[11px]"
            style={{ color: PRESSURE_COLOR[r.pressure ?? 0], borderColor: PRESSURE_COLOR[r.pressure ?? 0] }}
          >
            {r.verdict}
          </span>

          <div className="mt-3 grid grid-cols-3 gap-3 text-[12px]">
            <div>
              <div className="text-[10px] uppercase tracking-wide text-dim">USD/RUB</div>
              <div className="tnum text-base text-ru-white">{r.usd_rub ? r.usd_rub.toFixed(1) : "—"}</div>
              <div className="tnum" style={{ color: (r.usd_rub_chg30 ?? 0) > 0 ? "var(--color-hostile)" : "var(--color-partner)" }}>
                {pct(r.usd_rub_chg30)}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-dim">CNY/RUB</div>
              <div className="tnum text-base text-ru-white">{r.cny_rub ? r.cny_rub.toFixed(2) : "—"}</div>
              <div className="tnum" style={{ color: (r.cny_rub_chg30 ?? 0) > 0 ? "var(--color-hostile)" : "var(--color-partner)" }}>
                {pct(r.cny_rub_chg30)}
              </div>
            </div>
            <div>
              <div className="text-[10px] uppercase tracking-wide text-dim">МосБиржа</div>
              <div className="tnum text-base text-ru-white">{r.moex ? Math.round(r.moex) : "—"}</div>
              <div className="tnum" style={{ color: (r.moex_chg30 ?? 0) >= 0 ? "var(--color-partner)" : "var(--color-hostile)" }}>
                {pct(r.moex_chg30)}
              </div>
            </div>
          </div>

          {!!r.moex_spark?.length && <Spark data={r.moex_spark} />}

          <div className="mt-2 text-[11px] text-dim">
            рубль и индекс МосБиржи · ~30 дней · ориентировочный композит
          </div>
        </div>
      )}
    </section>
  );
}
