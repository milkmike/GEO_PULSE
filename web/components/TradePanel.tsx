"use client";

import Plot from "@/components/Plot";
import MotionCard from "@/components/MotionCard";
import type { TradeYear } from "@/lib/types";

function fmtUsd(v: number | null): string {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toLocaleString("en")}`;
}

export default function TradePanel({ data }: { data: TradeYear[] }) {
  if (!data.length) return null;
  const last = data[data.length - 1];
  const yoy = last.yoy_change_pct;
  return (
    <MotionCard>
      <div className="card-title px-4 pb-1 pt-3">Торговля с Россией</div>
      <div className="grid grid-cols-2 gap-2 px-4 pt-1 sm:grid-cols-4">
        <div><div className="text-lg font-semibold">{fmtUsd(last.total_trade_usd)}</div>
          <div className="text-[10px] uppercase text-dim">оборот {last.year}</div></div>
        <div><div className="text-lg font-semibold text-indigo-400">{fmtUsd(last.ru_export_usd)}</div>
          <div className="text-[10px] uppercase text-dim">экспорт РФ</div></div>
        <div><div className="text-lg font-semibold text-teal-400">{fmtUsd(last.ru_import_usd)}</div>
          <div className="text-[10px] uppercase text-dim">импорт РФ</div></div>
        <div><div className={`text-lg font-semibold ${yoy != null && yoy < 0 ? "text-red-400" : "text-emerald-400"}`}>
          {yoy != null ? `${yoy > 0 ? "+" : ""}${yoy.toFixed(1)}%` : "—"}</div>
          <div className="text-[10px] uppercase text-dim">за год</div></div>
      </div>
      <Plot className="h-[200px] w-full px-2 pb-2"
        data={[
          { x: data.map((d) => d.year), y: data.map((d) => d.ru_export_usd),
            type: "bar", name: "Экспорт РФ", marker: { color: "#818cf8" } },
          { x: data.map((d) => d.year), y: data.map((d) => d.ru_import_usd),
            type: "bar", name: "Импорт РФ", marker: { color: "#2dd4bf" } },
          { x: data.map((d) => d.year), y: data.map((d) => d.total_trade_usd),
            type: "scatter", mode: "lines", name: "Оборот",
            line: { color: "#e5e7eb", dash: "dash", width: 1.5 } },
        ]}
        layout={{
          barmode: "stack", margin: { l: 40, r: 8, t: 8, b: 24 },
          paper_bgcolor: "transparent", plot_bgcolor: "transparent",
          font: { color: "#9ca3af", size: 10 },
          legend: { orientation: "h", y: 1.15 },
          yaxis: { gridcolor: "rgba(255,255,255,0.06)" },
          xaxis: { gridcolor: "rgba(255,255,255,0.06)" },
        }} />
    </MotionCard>
  );
}
