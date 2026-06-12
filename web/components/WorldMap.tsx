"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

/* ── easter egg: «массаракш» ──────────────────────────────
 * A tiny unlabeled ☢ button launches a purely decorative
 * Strangelove-style animation: warheads arc out of Russia
 * along great circles and pop over capitals on every
 * continent. No politics intended beyond the absurd. */

const LAUNCH_SITES: [number, number][] = [
  [55.76, 37.62], // Moscow
  [62.93, 40.57], // Plesetsk
  [56.01, 92.87], // Krasnoyarsk
  [51.66, 39.2], // Voronezh
  [48.48, 135.07], // Khabarovsk
];

const TARGETS: [number, number][] = [
  [38.9, -77.04], [45.42, -75.7], [19.43, -99.13], [-15.79, -47.88],
  [-34.6, -58.38], [51.5, -0.13], [48.86, 2.35], [52.52, 13.4],
  [40.42, -3.7], [41.9, 12.5], [52.23, 21.01], [59.33, 18.07],
  [39.93, 32.86], [30.04, 31.24], [-25.75, 28.19], [24.71, 46.68],
  [35.68, 139.69], [37.57, 126.98], [28.61, 77.21], [-6.21, 106.85],
  [-35.28, 149.13],
];

const EXPLOSION_MS = 1600;
const FINALE_MS = 2400;

type Vec3 = [number, number, number];

function toVec(lat: number, lon: number): Vec3 {
  const la = (lat * Math.PI) / 180;
  const lo = (lon * Math.PI) / 180;
  return [Math.cos(la) * Math.cos(lo), Math.cos(la) * Math.sin(lo), Math.sin(la)];
}

function toLatLon(v: Vec3): [number, number] {
  return [
    (Math.asin(Math.min(1, Math.max(-1, v[2]))) * 180) / Math.PI,
    (Math.atan2(v[1], v[0]) * 180) / Math.PI,
  ];
}

function slerp(a: Vec3, b: Vec3, t: number): Vec3 {
  const dot = Math.min(1, Math.max(-1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2]));
  const th = Math.acos(dot);
  if (th < 1e-6) return a;
  const s = Math.sin(th);
  const k1 = Math.sin((1 - t) * th) / s;
  const k2 = Math.sin(t * th) / s;
  return [a[0] * k1 + b[0] * k2, a[1] * k1 + b[1] * k2, a[2] * k1 + b[2] * k2];
}

interface Missile {
  from: [number, number];
  to: [number, number];
  start: number;
  flight: number;
}

function planStrike(now: number): Missile[] {
  return TARGETS.map((to, i) => {
    const from = LAUNCH_SITES[i % LAUNCH_SITES.length];
    const a = toVec(from[0], from[1]);
    const b = toVec(to[0], to[1]);
    const ang = Math.acos(
      Math.min(1, Math.max(-1, a[0] * b[0] + a[1] * b[1] + a[2] * b[2])),
    );
    return { from, to, start: now + i * 300, flight: 1400 + ang * 900 };
  });
}

export default function WorldMap({ entries }: { entries: MapEntry[] }) {
  const router = useRouter();
  const [missiles, setMissiles] = useState<Missile[] | null>(null);
  const [finale, setFinale] = useState(false);
  const [tick, setTick] = useState(0);
  const rafRef = useRef<number>(0);

  const byIso3 = useMemo(
    () => Object.fromEntries(entries.map((e) => [e.iso3, e])),
    [entries],
  );

  const launch = useCallback(() => {
    if (missiles || finale) return;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setFinale(true);
      setTimeout(() => setFinale(false), FINALE_MS);
      return;
    }
    setMissiles(planStrike(performance.now()));
  }, [missiles, finale]);

  useEffect(() => {
    if (!missiles) return;
    const end =
      Math.max(...missiles.map((m) => m.start + m.flight)) + EXPLOSION_MS;
    let last = 0;
    const loop = (t: number) => {
      if (t - last > 40) {
        last = t;
        setTick((x) => x + 1);
      }
      if (performance.now() < end) {
        rafRef.current = requestAnimationFrame(loop);
      } else {
        setMissiles(null);
        setFinale(true);
        setTimeout(() => setFinale(false), FINALE_MS);
      }
    };
    rafRef.current = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(rafRef.current);
  }, [missiles]);

  const data = useMemo(() => {
    const base: unknown[] = [
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
    ];

    if (!missiles) return base;

    const now = performance.now();
    const trailLat: (number | null)[] = [];
    const trailLon: (number | null)[] = [];
    const headLat: number[] = [];
    const headLon: number[] = [];
    const boomLat: number[] = [];
    const boomLon: number[] = [];
    const boomSize: number[] = [];
    const boomOp: number[] = [];

    for (const m of missiles) {
      const t = (now - m.start) / m.flight;
      if (t <= 0) continue;
      const a = toVec(m.from[0], m.from[1]);
      const b = toVec(m.to[0], m.to[1]);
      if (t < 1) {
        const steps = 24;
        for (let i = 0; i <= steps; i++) {
          const [la, lo] = toLatLon(slerp(a, b, (i / steps) * t));
          trailLat.push(la);
          trailLon.push(lo);
        }
        trailLat.push(null);
        trailLon.push(null);
        const [hla, hlo] = toLatLon(slerp(a, b, t));
        headLat.push(hla);
        headLon.push(hlo);
      } else {
        const age = now - (m.start + m.flight);
        if (age < EXPLOSION_MS) {
          const k = age / EXPLOSION_MS;
          boomLat.push(m.to[0]);
          boomLon.push(m.to[1]);
          boomSize.push(10 + k * 34);
          boomOp.push(1 - k);
        }
      }
    }

    base.push(
      {
        type: "scattergeo", mode: "lines", lat: trailLat, lon: trailLon,
        line: { color: "rgba(255,180,80,0.5)", width: 1 },
        hoverinfo: "skip", showlegend: false,
      },
      {
        type: "scattergeo", mode: "markers", lat: headLat, lon: headLon,
        marker: { size: 5, color: "#ffd166" },
        hoverinfo: "skip", showlegend: false,
      },
      {
        type: "scattergeo", mode: "markers", lat: boomLat, lon: boomLon,
        marker: { size: boomSize, color: "#ff6b35", opacity: boomOp, line: { width: 0 } },
        hoverinfo: "skip", showlegend: false,
      },
      {
        type: "scattergeo", mode: "markers", lat: boomLat, lon: boomLon,
        marker: {
          size: boomSize.map((s) => s * 0.45),
          color: "#fff3c4", opacity: boomOp, line: { width: 0 },
        },
        hoverinfo: "skip", showlegend: false,
      },
    );
    return base;
    // eslint-disable-next-line react-hooks/exhaustive-deps -- tick drives reanimation frames
  }, [entries, missiles, tick]);

  const handleClick = useCallback(
    (p: { location?: string }) => {
      const e = p.location ? byIso3[p.location] : undefined;
      if (e) router.push(`/country/${e.code}`);
    },
    [byIso3, router],
  );

  return (
    <div className="relative h-full w-full">
      <Plot
        data={data}
        layout={GEO_LAYOUT as unknown as Record<string, unknown>}
        className="min-h-[340px] h-full w-full"
        onClick={handleClick}
      />
      <button
        onClick={launch}
        title="не нажимать"
        aria-label="не нажимать"
        className="absolute bottom-2 left-2 z-10 select-none text-[13px] leading-none opacity-25 transition-opacity duration-300 hover:opacity-90"
      >
        ☢
      </button>
      {finale && (
        <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center">
          <span
            className="display reveal text-[34px] tracking-wide"
            style={{ textShadow: "0 0 32px rgba(217,79,67,0.85)" }}
          >
            МАССАРАКШ.
          </span>
        </div>
      )}
    </div>
  );
}
