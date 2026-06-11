"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import Plot from "./Plot";
import { api } from "@/lib/api";
import { fmt, fmtDay, LEVEL_RU, SCORE_COLORSCALE } from "@/lib/format";
import type { MapEntry, MapHistoryFrame } from "@/lib/types";

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
  const [frames, setFrames] = useState<MapHistoryFrame[]>([]);
  const [frameIdx, setFrameIdx] = useState<number | null>(null); // null = live
  const [playing, setPlaying] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const byIso3 = useMemo(
    () => Object.fromEntries(entries.map((e) => [e.iso3, e])),
    [entries],
  );

  useEffect(() => {
    api.mapHistory(90).then((h) => setFrames(h.days)).catch(() => {});
  }, []);

  useEffect(() => {
    if (playing && frames.length > 1) {
      timer.current = setInterval(() => {
        setFrameIdx((i) => {
          const next = (i ?? 0) + 1;
          if (next >= frames.length) {
            setPlaying(false);
            return null; // вернуться к «сейчас»
          }
          return next;
        });
      }, 350);
    }
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [playing, frames.length]);

  const current = useMemo(() => {
    if (frameIdx != null && frames[frameIdx]) {
      const f = frames[frameIdx];
      return {
        iso3: f.iso3,
        scores: f.scores,
        text: f.iso3.map((iso, i) => {
          const e = byIso3[iso];
          return `${e?.name ?? iso}<br>${fmtDay(f.day)}: ${fmt(f.scores[i])}`;
        }),
        label: fmtDay(f.day),
      };
    }
    return {
      iso3: entries.map((e) => e.iso3),
      scores: entries.map((e) => e.score),
      text: entries.map(
        (e) =>
          `${e.name}<br>индекс ${fmt(e.score)} · ${LEVEL_RU[e.level]}` +
          (e.delta_24h != null ? `<br>за 24ч: ${fmt(e.delta_24h)}` : ""),
      ),
      label: "сейчас",
    };
  }, [frameIdx, frames, entries, byIso3]);

  const data = useMemo(
    () => [
      {
        type: "choropleth",
        locations: current.iso3,
        z: current.scores,
        zmin: -100,
        zmax: 100,
        text: current.text,
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
    [current],
  );

  const handleClick = useCallback(
    (p: { location?: string }) => {
      const e = p.location ? byIso3[p.location] : undefined;
      if (e) router.push(`/country/${e.code}`);
    },
    [byIso3, router],
  );

  return (
    <div className="flex h-full flex-col">
      <Plot
        data={data}
        layout={GEO_LAYOUT as unknown as Record<string, unknown>}
        className="min-h-[340px] w-full flex-1"
        onClick={handleClick}
      />
      {frames.length > 1 && (
        <div className="flex items-center gap-3 border-t border-line px-4 py-2">
          <button
            onClick={() => {
              if (!playing && frameIdx == null) setFrameIdx(0);
              setPlaying(!playing);
            }}
            className="rounded-md border border-line px-3 py-1 text-xs text-accent hover:bg-panel2"
          >
            {playing ? "⏸ пауза" : "▶ таймлапс"}
          </button>
          <input
            type="range"
            min={0}
            max={frames.length - 1}
            value={frameIdx ?? frames.length - 1}
            onChange={(e) => {
              setPlaying(false);
              const v = Number(e.target.value);
              setFrameIdx(v >= frames.length - 1 ? null : v);
            }}
            className="flex-1 accent-sky-400"
          />
          <span className="tnum w-20 text-right text-xs text-dim">{current.label}</span>
        </div>
      )}
    </div>
  );
}
