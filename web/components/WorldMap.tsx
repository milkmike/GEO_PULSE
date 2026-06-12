"use client";

import { useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import Plot from "./Plot";
import { fmt, LEVEL_RU, SCORE_COLORSCALE } from "@/lib/format";
import type { MapEntry } from "@/lib/types";

const GEO_LAYOUT = {
  geo: {
    projection: { type: "natural earth" },
    bgcolor: "rgba(0,0,0,0)",
    showframe: false,
    showcoastlines: false,
    showcountries: true,
    countrycolor: "#1f2937",
    landcolor: "#161d26",
    oceancolor: "#0b0f14",
    showocean: true,
    showlakes: false,
  },
  paper_bgcolor: "rgba(0,0,0,0)",
  margin: { t: 4, b: 4, l: 4, r: 4 },
  dragmode: "pan" as const,
};

export default function WorldMap({ entries }: { entries: MapEntry[] }) {
  const router = useRouter();

  const byIso3 = useMemo(
    () => Object.fromEntries(entries.map((e) => [e.iso3, e])),
    [entries],
  );

  const data = useMemo(
    () => [
      {
        type: "choropleth",
        locations: entries.map((e) => e.iso3),
        z: entries.map((e) => e.score),
        zmin: -100,
        zmax: 100,
        text: entries.map(
          (e) =>
            `${e.name}<br>индекс ${fmt(e.score)} · ${LEVEL_RU[e.level]}` +
            (e.delta_24h != null ? `<br>за 24ч: ${fmt(e.delta_24h)}` : ""),
        ),
        hovertemplate: "%{text}<extra></extra>",
        colorscale: SCORE_COLORSCALE,
        marker: { line: { color: "#0b0f14", width: 0.5 } },
        colorbar: {
          title: { text: "RRI", font: { color: "#9ca3af", size: 11 } },
          thickness: 8,
          tickfont: { color: "#9ca3af", size: 10 },
          outlinewidth: 0,
          len: 0.75,
        },
      },
    ],
    [entries],
  );

  const handleClick = useCallback(
    (p: { location?: string }) => {
      const e = p.location ? byIso3[p.location] : undefined;
      if (e) router.push(`/country/${e.code}`);
    },
    [byIso3, router],
  );

  return (
    <Plot
      data={data}
      layout={GEO_LAYOUT as unknown as Record<string, unknown>}
      className="min-h-[340px] w-full flex-1"
      onClick={handleClick}
    />
  );
}
