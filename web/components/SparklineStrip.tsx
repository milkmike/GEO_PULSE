"use client";

import Plot from "./Plot";

type SparkDossier = {
  index?: { score: number; level: string } | null;
  index_history: { day: string; score: number }[];
  gdelt: { day: string; tone: number | null; volume: number | null }[];
};

const LEVEL_COLOR: Record<string, string> = {
  ally: "#10b981", partner: "#34d399", neutral: "#9ca3af",
  cooling: "#fbbf24", tension: "#f97316", hostile: "#ef4444",
};

function spark(x: (string | number)[], y: (number | null)[], color: string) {
  return {
    data: [{
      x, y, type: "scatter", mode: "lines",
      line: { color, width: 1.5 }, fill: "tozeroy", fillcolor: `${color}22`,
      hoverinfo: "y",
    }],
    layout: {
      margin: { l: 0, r: 0, t: 0, b: 0 }, height: 46,
      paper_bgcolor: "transparent", plot_bgcolor: "transparent",
      xaxis: { visible: false }, yaxis: { visible: false }, showlegend: false,
    } as Record<string, unknown>,
  };
}

export default function SparklineStrip({ dossier }: { dossier: SparkDossier }) {
  const ih = dossier.index_history ?? [];
  const gd = dossier.gdelt ?? [];
  if (ih.length < 2 && gd.length < 2) return null;

  const rriColor = dossier.index ? LEVEL_COLOR[dossier.index.level] ?? "#9ca3af" : "#9ca3af";
  const tones = gd.map((g) => g.tone).filter((t): t is number => t != null);

  // media-shock: z-score of the latest GDELT tone vs the window
  let shock = 0;
  if (tones.length >= 7) {
    const mean = tones.reduce((a, b) => a + b, 0) / tones.length;
    const sd = Math.sqrt(tones.reduce((a, b) => a + (b - mean) ** 2, 0) / tones.length) || 1;
    shock = (tones[tones.length - 1] - mean) / sd;
  }

  const rri = spark(ih.map((h) => h.day), ih.map((h) => h.score), rriColor);
  const tone = spark(gd.map((g) => g.day), gd.map((g) => g.tone), "#38bdf8");
  const vol = spark(gd.map((g) => g.day), gd.map((g) => g.volume), "#6b9be8");

  const Cell = ({ title, value, plot, badge }: {
    title: string; value: string; plot: ReturnType<typeof spark>; badge?: string;
  }) => (
    <div className="card flex-1 px-3 py-2">
      <div className="flex items-baseline justify-between">
        <span className="text-[11px] uppercase tracking-wide text-dim">{title}</span>
        {badge && (
          <span className="rounded px-1.5 text-[10px]" style={{ color: "var(--color-hostile)", border: "1px solid var(--color-hostile)" }}>
            {badge}
          </span>
        )}
      </div>
      <div className="tnum text-lg text-ru-white">{value}</div>
      <Plot data={plot.data} layout={plot.layout} className="h-[46px] w-full" />
    </div>
  );

  return (
    <div className="flex gap-3">
      <Cell
        title="индекс RRI"
        value={dossier.index ? `${dossier.index.score > 0 ? "+" : ""}${dossier.index.score.toFixed(0)}` : "—"}
        plot={rri}
      />
      <Cell
        title="тон GDELT"
        value={tones.length ? `${tones[tones.length - 1] > 0 ? "+" : ""}${tones[tones.length - 1].toFixed(1)}` : "—"}
        plot={tone}
        badge={Math.abs(shock) >= 1.6 ? "медиа-шок" : undefined}
      />
      <Cell
        title="объём упоминаний"
        value={gd.length ? String(gd[gd.length - 1].volume ?? "—") : "—"}
        plot={vol}
      />
    </div>
  );
}
