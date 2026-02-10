"use client";

import { useEffect, useRef } from "react";
import type { Country } from "@/lib/api";

interface PlotlyMapProps {
  countries: Country[];
}

export default function PlotlyMap({ countries }: PlotlyMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!containerRef.current || countries.length === 0) return;

    let mounted = true;

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    import("plotly.js-dist-min").then((Plotly: any) => {
      if (!mounted || !containerRef.current) return;

      const data = [
        {
          type: "choropleth",
          locationmode: "ISO-3",
          locations: countries.map((c) => c.iso3),
          z: countries.map((c) => c.temperature),
          text: countries.map(
            (c) => `${c.name}: ${c.temperature.toFixed(1)}°`
          ),
          hoverinfo: "text",
          colorscale: [
            [0, "#3b82f6"],
            [0.3, "#60a5fa"],
            [0.5, "#fbbf24"],
            [0.7, "#f97316"],
            [1, "#ef4444"],
          ],
          showscale: true,
          colorbar: {
            title: { text: "°T", font: { color: "#a1a1aa", size: 12 } },
            tickfont: { color: "#a1a1aa" },
            bgcolor: "rgba(0,0,0,0)",
          },
          marker: {
            line: { color: "rgba(255,255,255,0.1)", width: 0.5 },
          },
        },
      ];

      const layout = {
        geo: {
          scope: "asia",
          projection: { type: "natural earth" },
          showframe: false,
          showcoastlines: true,
          coastlinecolor: "rgba(255,255,255,0.15)",
          showland: true,
          landcolor: "#16161d",
          showocean: true,
          oceancolor: "#0a0a0f",
          showlakes: false,
          showcountries: true,
          countrycolor: "rgba(255,255,255,0.1)",
          bgcolor: "rgba(0,0,0,0)",
          center: { lat: 42, lon: 65 },
          lonaxis: { range: [38, 90] },
          lataxis: { range: [32, 55] },
        },
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        margin: { l: 0, r: 0, t: 0, b: 0 },
        height: 420,
        dragmode: false,
      };

      const config = {
        displayModeBar: false,
        responsive: true,
      };

      Plotly.newPlot(containerRef.current, data, layout, config);
    });

    return () => {
      mounted = false;
    };
  }, [countries]);

  return (
    <div
      ref={containerRef}
      className="w-full rounded-lg border border-border bg-card"
    />
  );
}
