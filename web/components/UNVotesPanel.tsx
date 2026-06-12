"use client";

import Plot from "@/components/Plot";
import type { UNVoteYear } from "@/lib/types";

export default function UNVotesPanel({ data }: { data: UNVoteYear[] }) {
  if (!data.length) return null;
  const last = data[data.length - 1];
  const pct = last.agreement_pct;
  const pctCls = pct == null ? "text-dim"
    : pct >= 60 ? "text-emerald-400" : pct >= 40 ? "text-yellow-400" : "text-red-400";
  return (
    <section className="card">
      <div className="card-title px-4 pb-1 pt-3">Голосования ООН (совпадение с РФ)</div>
      <div className="grid grid-cols-2 gap-2 px-4 pt-1 sm:grid-cols-4">
        <div><div className={`text-lg font-semibold ${pctCls}`}>{pct != null ? `${pct.toFixed(0)}%` : "—"}</div>
          <div className="text-[10px] uppercase text-dim">совпадение {last.year}</div></div>
        <div><div className="text-lg font-semibold">{last.total_votes ?? "—"}</div>
          <div className="text-[10px] uppercase text-dim">голосований</div></div>
        <div><div className="text-lg font-semibold text-emerald-400">{last.agree_with_russia ?? "—"}</div>
          <div className="text-[10px] uppercase text-dim">вместе с РФ</div></div>
        <div><div className="text-lg font-semibold text-red-400">{last.disagree_with_russia ?? "—"}</div>
          <div className="text-[10px] uppercase text-dim">против РФ</div></div>
      </div>
      <Plot className="h-[180px] w-full px-2 pb-2"
        data={[{
          x: data.map((d) => d.year), y: data.map((d) => d.agreement_pct),
          type: "scatter", mode: "lines+markers", fill: "tozeroy",
          line: { color: "#a78bfa" }, fillcolor: "rgba(167,139,250,0.15)",
          hovertemplate: "%{x}: %{y:.0f}%<extra></extra>",
        }]}
        layout={{
          margin: { l: 32, r: 8, t: 8, b: 24 },
          paper_bgcolor: "transparent", plot_bgcolor: "transparent",
          font: { color: "#9ca3af", size: 10 },
          yaxis: { range: [0, 100], gridcolor: "rgba(255,255,255,0.06)" },
          xaxis: { gridcolor: "rgba(255,255,255,0.06)" },
          shapes: [{ type: "line", x0: data[0].year, x1: last.year, y0: 50, y1: 50,
                     line: { color: "rgba(255,255,255,0.25)", dash: "dot", width: 1 } }],
        }} />
    </section>
  );
}
