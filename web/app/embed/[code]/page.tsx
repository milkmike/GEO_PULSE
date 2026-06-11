"use client";

import { use, useEffect, useMemo, useState } from "react";
import Plot from "@/components/Plot";
import { api, apiBase } from "@/lib/api";
import { fmt, LEVEL_COLOR, LEVEL_RU } from "@/lib/format";
import type { Dossier } from "@/lib/types";

/** Compact iframe widget: score + level + 30d sparkline. */
export default function EmbedPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  const cc = code.toUpperCase();
  const [dossier, setDossier] = useState<Dossier | null>(null);

  useEffect(() => {
    api.dossier(cc, 30).then(setDossier).catch(() => {});
  }, [cc]);

  const spark = useMemo(() => {
    if (!dossier || dossier.index_history.length < 2) return null;
    const color = dossier.index ? LEVEL_COLOR[dossier.index.level] : "#9ca3af";
    return {
      data: [
        {
          x: dossier.index_history.map((h) => h.day),
          y: dossier.index_history.map((h) => h.score),
          mode: "lines",
          line: { color, width: 1.5 },
          fill: "tozeroy",
          fillcolor: `${color}1f`,
          hoverinfo: "skip",
        },
      ],
      layout: {
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        margin: { t: 2, b: 2, l: 2, r: 2 },
        height: 60,
        xaxis: { visible: false },
        yaxis: { visible: false },
      },
    };
  }, [dossier]);

  if (!dossier?.index) {
    return <div className="p-4 text-xs text-dim">GEO PULSE · {cc}: загрузка…</div>;
  }

  const { country, index } = dossier;
  const color = LEVEL_COLOR[index.level];

  return (
    <a
      href={`${typeof window !== "undefined" ? window.location.origin : ""}/country/${cc}`}
      target="_parent"
      className="block h-screen overflow-hidden bg-bg p-3"
    >
      <div className="flex items-center gap-2">
        <span className="text-lg">{country.flag}</span>
        <span className="text-sm font-semibold">{country.name} ↔ Россия</span>
      </div>
      <div className="mt-1 flex items-baseline gap-2.5">
        <span className="tnum text-3xl font-bold" style={{ color }}>
          {fmt(index.score)}
        </span>
        <span className="rounded-full border px-2 py-px text-[10px]" style={{ color, borderColor: color }}>
          {LEVEL_RU[index.level]}
        </span>
        {index.delta_24h != null && (
          <span className="tnum text-xs text-dim">{fmt(index.delta_24h)} за 24ч</span>
        )}
      </div>
      {spark && (
        <Plot
          data={spark.data}
          layout={spark.layout as unknown as Record<string, unknown>}
          config={{ staticPlot: true }}
          className="mt-1 w-full"
        />
      )}
      <div className="mt-0.5 text-right text-[9px] text-dim">geopulse · {apiBase().replace(/^https?:\/\//, "")}</div>
    </a>
  );
}
