"use client";

import { useEffect, useRef } from "react";

type PlotlyModule = {
  react: (el: HTMLElement, data: unknown[], layout: Record<string, unknown>,
          config?: Record<string, unknown>) => Promise<unknown>;
  purge: (el: HTMLElement) => void;
};

let plotlyPromise: Promise<PlotlyModule> | null = null;
function loadPlotly(): Promise<PlotlyModule> {
  if (!plotlyPromise) {
    plotlyPromise = import("plotly.js-dist-min").then(
      (m) => (m.default ?? m) as unknown as PlotlyModule,
    );
  }
  return plotlyPromise;
}

export interface PlotProps {
  data: unknown[];
  layout: Record<string, unknown>;
  config?: Record<string, unknown>;
  className?: string;
  onClick?: (point: { location?: string }) => void;
}

/** Thin client-only Plotly wrapper (plotly.js can't render on the server). */
export default function Plot({ data, layout, config, className, onClick }: PlotProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const el = ref.current;
    if (!el) return;

    loadPlotly().then((Plotly) => {
      if (cancelled || !ref.current) return;
      Plotly.react(ref.current, data, layout, {
        displayModeBar: false,
        responsive: true,
        ...config,
      }).then(() => {
        if (cancelled || !ref.current || !onClick) return;
        const node = ref.current as HTMLElement & {
          on?: (ev: string, cb: (e: { points?: { location?: string }[] }) => void) => void;
          removeAllListeners?: (ev: string) => void;
        };
        node.removeAllListeners?.("plotly_click");
        node.on?.("plotly_click", (ev) => {
          if (ev.points?.[0]) onClick(ev.points[0]);
        });
      });
    });

    return () => {
      cancelled = true;
    };
  }, [data, layout, config, onClick]);

  useEffect(
    () => () => {
      const el = ref.current;
      if (el) loadPlotly().then((Plotly) => Plotly.purge(el));
    },
    [],
  );

  return <div ref={ref} className={className} />;
}
