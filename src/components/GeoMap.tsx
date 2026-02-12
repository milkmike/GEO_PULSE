"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  temperatureColor,
  temperatureLabel,
  trendIcon,
  COUNTRY_FLAGS,
  type Country,
} from "@/lib/api";

// ── Country geo data (capitals + bounding boxes) ───────

interface CountryGeo {
  capital: string;
  lat: number;
  lon: number;
  // Bounding box for zoom-to-country [lonMin, latMin, lonMax, latMax]
  bbox: [number, number, number, number];
}

const COUNTRY_GEO: Record<string, CountryGeo> = {
  KZ: { capital: "Астана", lat: 51.17, lon: 71.45, bbox: [46.5, 40.6, 87.4, 55.4] },
  AM: { capital: "Ереван", lat: 40.18, lon: 44.51, bbox: [43.4, 38.8, 46.6, 41.3] },
  UZ: { capital: "Ташкент", lat: 41.30, lon: 69.27, bbox: [56.0, 37.2, 73.1, 45.6] },
  KG: { capital: "Бишкек", lat: 42.87, lon: 74.59, bbox: [69.3, 39.2, 80.3, 43.3] },
  TJ: { capital: "Душанбе", lat: 38.56, lon: 68.77, bbox: [67.3, 36.7, 75.1, 41.0] },
  TM: { capital: "Ашхабад", lat: 37.95, lon: 58.38, bbox: [52.4, 35.1, 66.7, 42.8] },
  AZ: { capital: "Баку", lat: 40.41, lon: 49.87, bbox: [44.8, 38.4, 50.6, 41.9] },
  GE: { capital: "Тбилиси", lat: 41.72, lon: 44.79, bbox: [40.0, 41.0, 46.7, 43.6] },
  MD: { capital: "Кишинёв", lat: 47.01, lon: 28.86, bbox: [26.6, 45.5, 30.2, 48.5] },
  BY: { capital: "Минск", lat: 53.90, lon: 27.57, bbox: [23.2, 51.3, 32.8, 56.2] },
};

// ── Default view (all CIS countries visible) ───────────

const DEFAULT_CENTER = { lat: 45, lon: 58 };
const DEFAULT_LON_RANGE: [number, number] = [20, 92];
const DEFAULT_LAT_RANGE: [number, number] = [30, 58];

// ── Component ──────────────────────────────────────────

interface GeoMapProps {
  countries: Country[];
  selectedCountry?: string | null;
  onCountrySelect?: (code: string | null) => void;
  onCountryDrillDown?: (code: string) => void;
  height?: number;
}

export default function GeoMap({
  countries,
  selectedCountry = null,
  onCountrySelect,
  onCountryDrillDown,
  height = 520,
}: GeoMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const plotlyRef = useRef<any>(null);
  const router = useRouter();
  const [hoveredCountry, setHoveredCountry] = useState<Country | null>(null);
  const [tooltipPos, setTooltipPos] = useState({ x: 0, y: 0, visible: false });
  const [isZoomed, setIsZoomed] = useState(false);

  // ── Build Plotly traces ──────────────────────────────

  const buildTraces = useCallback(() => {
    if (countries.length === 0) return [];

    // 1. Choropleth — temperature fill
    const choropleth = {
      type: "choropleth" as const,
      locationmode: "ISO-3" as const,
      locations: countries.map((c) => c.iso3),
      z: countries.map((c) => c.temperature),
      text: countries.map((c) => c.code),
      customdata: countries.map((c) => ({
        code: c.code,
        name: c.name,
        temperature: c.temperature,
        trend: c.trend,
        articles: c.article_count,
        divergence: c.divergence,
        sentiment: c.raw_sentiment,
      })),
      hoverinfo: "none" as const,
      colorscale: [
        [0, "#1e40af"],     // deep blue (very cold, cooperation)
        [0.15, "#3b82f6"],  // blue
        [0.3, "#60a5fa"],   // light blue
        [0.45, "#e2e8f0"],  // neutral gray-blue
        [0.5, "#f1f5f9"],   // neutral
        [0.55, "#fef3c7"],  // warm cream
        [0.7, "#fbbf24"],   // amber
        [0.85, "#f97316"],  // orange
        [1, "#dc2626"],     // deep red (very hot, conflict)
      ],
      zmin: -50,
      zmid: 0,
      zmax: 50,
      showscale: true,
      colorbar: {
        title: {
          text: "Температура",
          font: { color: "rgba(255,255,255,0.6)", size: 11 },
          side: "right" as const,
        },
        tickfont: { color: "rgba(255,255,255,0.5)", size: 10 },
        ticksuffix: "°",
        bgcolor: "rgba(0,0,0,0)",
        len: 0.6,
        x: 1.02,
        thickness: 12,
        outlinewidth: 0,
        tickvals: [-40, -20, 0, 20, 40],
        ticktext: ["-40° ❄️", "-20°", "0° ⚖️", "+20°", "+40° 🔥"],
      },
      marker: {
        line: {
          color: countries.map((c) =>
            c.code === selectedCountry
              ? "rgba(255,255,255,0.9)"
              : "rgba(255,255,255,0.35)"
          ),
          width: countries.map((c) => (c.code === selectedCountry ? 3 : 1.2)),
        },
      },
    };

    // 2. Scattergeo — capital markers with article count bubbles
    const activeCountries = countries.filter((c) => COUNTRY_GEO[c.code]);
    const maxArticles = Math.max(...activeCountries.map((c) => c.article_count), 1);

    const bubbles = {
      type: "scattergeo" as const,
      locationmode: "ISO-3" as const,
      lat: activeCountries.map((c) => COUNTRY_GEO[c.code].lat),
      lon: activeCountries.map((c) => COUNTRY_GEO[c.code].lon),
      text: activeCountries.map((c) => {
        const flag = COUNTRY_FLAGS[c.code] || "";
        const tempStr = `${c.temperature > 0 ? "+" : ""}${c.temperature.toFixed(1)}°`;
        return `${flag} ${c.name}\n${tempStr} ${trendIcon(c.trend)}\n📰 ${c.article_count} статей`;
      }),
      customdata: activeCountries.map((c) => c.code),
      hoverinfo: "none" as const,
      marker: {
        size: activeCountries.map((c) => {
          const ratio = c.article_count / maxArticles;
          return Math.max(12, Math.min(40, 12 + ratio * 28));
        }),
        color: activeCountries.map((c) => temperatureColor(c.temperature)),
        opacity: 0.6,
        line: {
          color: activeCountries.map((c) =>
            c.code === selectedCountry
              ? "rgba(255,255,255,0.9)"
              : "rgba(255,255,255,0.3)"
          ),
          width: activeCountries.map((c) => (c.code === selectedCountry ? 2 : 1)),
        },
      },
    };

    // 3. Scattergeo — country labels
    const labels = {
      type: "scattergeo" as const,
      locationmode: "ISO-3" as const,
      lat: activeCountries.map((c) => COUNTRY_GEO[c.code].lat + 1.2),
      lon: activeCountries.map((c) => COUNTRY_GEO[c.code].lon),
      text: activeCountries.map((c) => {
        const temp = `${c.temperature > 0 ? "+" : ""}${c.temperature.toFixed(1)}°`;
        return `${COUNTRY_FLAGS[c.code] || ""} ${temp}`;
      }),
      customdata: activeCountries.map((c) => c.code),
      hoverinfo: "none" as const,
      mode: "text" as const,
      textfont: {
        size: 11,
        color: activeCountries.map((c) => temperatureColor(c.temperature)),
        family: "system-ui, -apple-system, sans-serif",
      },
    };

    return [choropleth, bubbles, labels];
  }, [countries, selectedCountry]);

  // ── Build layout ─────────────────────────────────────

  const buildLayout = useCallback(
    (zoomTo?: string | null) => {
      let center = DEFAULT_CENTER;
      let lonaxis = { range: DEFAULT_LON_RANGE };
      let lataxis = { range: DEFAULT_LAT_RANGE };

      if (zoomTo && COUNTRY_GEO[zoomTo]) {
        const geo = COUNTRY_GEO[zoomTo];
        const bbox = geo.bbox;
        // Add some padding
        const lonPad = (bbox[2] - bbox[0]) * 0.4;
        const latPad = (bbox[3] - bbox[1]) * 0.4;
        center = { lat: (bbox[1] + bbox[3]) / 2, lon: (bbox[0] + bbox[2]) / 2 };
        lonaxis = { range: [bbox[0] - lonPad, bbox[2] + lonPad] };
        lataxis = { range: [bbox[1] - latPad, bbox[3] + latPad] };
      }

      return {
        geo: {
          projection: { type: "natural earth" },
          showframe: false,
          showcoastlines: true,
          coastlinecolor: "rgba(255,255,255,0.12)",
          showland: true,
          landcolor: "#18181b",
          showocean: true,
          oceancolor: "#09090b",
          showlakes: true,
          lakecolor: "#0c0c12",
          showcountries: false,
          showsubunits: false,
          bgcolor: "rgba(0,0,0,0)",
          center,
          lonaxis,
          lataxis,
          // River styling
          showrivers: true,
          rivercolor: "rgba(59,130,246,0.08)",
          riverwidth: 0.5,
        },
        paper_bgcolor: "rgba(0,0,0,0)",
        plot_bgcolor: "rgba(0,0,0,0)",
        margin: { l: 0, r: 60, t: 0, b: 0 },
        height,
        dragmode: "pan" as const,
        showlegend: false,
        // Animations
        transition: {
          duration: 600,
          easing: "cubic-in-out" as const,
        },
      };
    },
    [height]
  );

  // ── Initialize + update plot ─────────────────────────

  useEffect(() => {
    if (!containerRef.current || countries.length === 0) return;

    let mounted = true;

    import("plotly.js-dist-min").then((Plotly: any) => {
      if (!mounted || !containerRef.current) return;

      plotlyRef.current = Plotly;

      const traces = buildTraces();
      const layout = buildLayout(selectedCountry);
      const config = {
        displayModeBar: true,
        modeBarButtonsToRemove: [
          "select2d",
          "lasso2d",
          "autoScale2d",
          "hoverClosestCartesian",
          "hoverCompareCartesian",
          "toggleSpikelines",
        ],
        modeBarButtonsToAdd: [
          {
            name: "Сбросить вид",
            icon: {
              width: 1000,
              height: 1000,
              path: "M512 0C229.2 0 0 229.2 0 512s229.2 512 512 512 512-229.2 512-512S794.8 0 512 0zm0 960C264.6 960 64 759.4 64 512S264.6 64 512 64s448 200.6 448 448-200.6 448-448 448zm192-480H320c-17.7 0-32 14.3-32 32s14.3 32 32 32h384c17.7 0 32-14.3 32-32s-14.3-32-32-32z",
            },
            click: () => {
              if (containerRef.current) {
                Plotly.react(
                  containerRef.current,
                  buildTraces(),
                  buildLayout(null),
                  config
                );
                setIsZoomed(false);
                onCountrySelect?.(null);
              }
            },
          },
        ],
        responsive: true,
        scrollZoom: true,
        displaylogo: false,
      };

      Plotly.newPlot(containerRef.current, traces, layout, config);

      // Plotly adds event methods to the DOM element
      const plotEl = containerRef.current as any;

      // ── Click handler ────────────────────────────────
      plotEl.on("plotly_click", (eventData: any) => {
        if (!eventData?.points?.[0]) return;
        const point = eventData.points[0];

        // Get country code from customdata
        let code: string | null = null;
        if (point.customdata?.code) {
          code = point.customdata.code;
        } else if (typeof point.customdata === "string") {
          code = point.customdata;
        } else if (point.text && typeof point.text === "string" && point.text.length === 2) {
          code = point.text;
        }

        if (code && COUNTRY_GEO[code]) {
          // If same country clicked again → drill down into threads
          if (selectedCountry === code && onCountryDrillDown) {
            onCountryDrillDown(code);
            return;
          }
          // Zoom to country
          const newLayout = buildLayout(code);
          Plotly.relayout(containerRef.current!, {
            "geo.center": newLayout.geo.center,
            "geo.lonaxis.range": newLayout.geo.lonaxis.range,
            "geo.lataxis.range": newLayout.geo.lataxis.range,
          });
          setIsZoomed(true);
          onCountrySelect?.(code);
        }
      });

      // ── Double-click → reset view ────────────────────
      plotEl.on("plotly_doubleclick", () => {
        if (containerRef.current) {
          const resetLayout = buildLayout(null);
          Plotly.relayout(containerRef.current, {
            "geo.center": resetLayout.geo.center,
            "geo.lonaxis.range": resetLayout.geo.lonaxis.range,
            "geo.lataxis.range": resetLayout.geo.lataxis.range,
          });
          setIsZoomed(false);
          onCountrySelect?.(null);
        }
      });

      // ── Hover handler (for custom tooltip) ───────────
      plotEl.on("plotly_hover", (eventData: any) => {
        if (!eventData?.points?.[0]) return;
        const point = eventData.points[0];
        let code: string | null = null;
        if (point.customdata?.code) code = point.customdata.code;
        else if (typeof point.customdata === "string") code = point.customdata;

        if (code) {
          const country = countries.find((c) => c.code === code);
          if (country) {
            setHoveredCountry(country);
            // Get mouse position from event
            const container = containerRef.current!.getBoundingClientRect();
            const evt = (eventData.event as MouseEvent) || window.event;
            if (evt) {
              setTooltipPos({
                x: (evt as MouseEvent).clientX - container.left,
                y: (evt as MouseEvent).clientY - container.top,
                visible: true,
              });
            }
          }
        }
      });

      plotEl.on("plotly_unhover", () => {
        setHoveredCountry(null);
        setTooltipPos((p) => ({ ...p, visible: false }));
      });
    });

    return () => {
      mounted = false;
      if (containerRef.current) {
        import("plotly.js-dist-min").then((Plotly: any) => {
          try { Plotly.purge(containerRef.current); } catch {}
        });
      }
    };
  }, [countries, selectedCountry, buildTraces, buildLayout, onCountrySelect]);

  // ── Navigate to country page ─────────────────────────

  const goToCountry = useCallback(
    (code: string) => {
      router.push(`/country/${code}`);
    },
    [router]
  );

  // ── Selected country info panel ──────────────────────

  const selectedData = selectedCountry
    ? countries.find((c) => c.code === selectedCountry)
    : null;

  return (
    <div className="relative">
      {/* Map container */}
      <div
        ref={containerRef}
        className="w-full rounded-xl border border-border bg-gradient-to-b from-card to-card/80 cursor-grab active:cursor-grabbing"
        style={{ minHeight: height }}
      />

      {/* Custom tooltip */}
      {hoveredCountry && tooltipPos.visible && (
        <div
          className="absolute pointer-events-none z-50 transition-opacity duration-150"
          style={{
            left: Math.min(tooltipPos.x + 16, (containerRef.current?.offsetWidth || 600) - 220),
            top: Math.max(tooltipPos.y - 10, 0),
          }}
        >
          <div className="rounded-lg border border-white/10 bg-zinc-900/95 backdrop-blur-sm p-3 shadow-xl min-w-[200px]">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-lg">{COUNTRY_FLAGS[hoveredCountry.code]}</span>
              <span className="font-semibold text-sm">{hoveredCountry.name}</span>
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Температура</span>
                <span
                  className="text-sm font-bold"
                  style={{ color: temperatureColor(hoveredCountry.temperature) }}
                >
                  {hoveredCountry.temperature > 0 ? "+" : ""}
                  {hoveredCountry.temperature.toFixed(1)}° {trendIcon(hoveredCountry.trend)}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Статус</span>
                <span className="text-xs" style={{ color: temperatureColor(hoveredCountry.temperature) }}>
                  {temperatureLabel(hoveredCountry.temperature)}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Статей</span>
                <span className="text-xs font-medium">{hoveredCountry.article_count}</span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-xs text-muted-foreground">Расхождение</span>
                <span
                  className={`text-xs font-mono ${
                    hoveredCountry.divergence >= 0.5
                      ? "text-red-400"
                      : hoveredCountry.divergence >= 0.2
                      ? "text-yellow-400"
                      : "text-green-400"
                  }`}
                >
                  Δ {hoveredCountry.divergence.toFixed(2)}
                </span>
              </div>
              {/* Temperature bar */}
              <div className="pt-1">
                <div className="h-1.5 w-full rounded-full bg-white/5 overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${Math.min(100, Math.max(0, (hoveredCountry.temperature + 50) / 100 * 100))}%`,
                      background: `linear-gradient(90deg, #3b82f6, #fbbf24, #ef4444)`,
                    }}
                  />
                </div>
                <div className="flex justify-between mt-0.5">
                  <span className="text-[9px] text-blue-400/50">❄️ −50°</span>
                  <span className="text-[9px] text-muted-foreground/30">0°</span>
                  <span className="text-[9px] text-red-400/50">🔥 +50°</span>
                </div>
              </div>
            </div>
            <div className="mt-2 pt-2 border-t border-white/5 text-[10px] text-muted-foreground/50 text-center">
              Клик — приблизить · Двойной клик — сбросить
            </div>
          </div>
        </div>
      )}

      {/* Selected country panel */}
      {selectedData && isZoomed && (
        <div className="absolute top-4 left-4 z-40 animate-in fade-in slide-in-from-left-4 duration-300">
          <Card className="w-64 border-white/10 bg-zinc-900/90 backdrop-blur-sm shadow-2xl">
            <CardContent className="p-4">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <span className="text-2xl">{COUNTRY_FLAGS[selectedData.code]}</span>
                  <div>
                    <h3 className="font-bold text-sm">{selectedData.name}</h3>
                    <span className="text-[10px] text-muted-foreground">
                      {COUNTRY_GEO[selectedData.code]?.capital}
                    </span>
                  </div>
                </div>
                <button
                  onClick={() => {
                    onCountrySelect?.(null);
                    setIsZoomed(false);
                    if (containerRef.current && plotlyRef.current) {
                      const resetLayout = buildLayout(null);
                      plotlyRef.current.relayout(containerRef.current, {
                        "geo.center": resetLayout.geo.center,
                        "geo.lonaxis.range": resetLayout.geo.lonaxis.range,
                        "geo.lataxis.range": resetLayout.geo.lataxis.range,
                      });
                    }
                  }}
                  className="text-muted-foreground hover:text-white transition-colors p-1 rounded hover:bg-white/5"
                >
                  ✕
                </button>
              </div>

              {/* Temperature display */}
              <div className="text-center py-2">
                <div
                  className="text-3xl font-bold"
                  style={{ color: temperatureColor(selectedData.temperature) }}
                >
                  {selectedData.temperature > 0 ? "+" : ""}
                  {selectedData.temperature.toFixed(1)}°
                </div>
                <div className="text-xs mt-1" style={{ color: temperatureColor(selectedData.temperature) }}>
                  {temperatureLabel(selectedData.temperature)} {trendIcon(selectedData.trend)}
                </div>
              </div>

              {/* Stats */}
              <div className="grid grid-cols-2 gap-2 mt-2">
                <div className="rounded-md bg-white/5 p-2 text-center">
                  <div className="text-sm font-bold">{selectedData.article_count}</div>
                  <div className="text-[10px] text-muted-foreground">Статей</div>
                </div>
                <div className="rounded-md bg-white/5 p-2 text-center">
                  <div
                    className={`text-sm font-bold font-mono ${
                      selectedData.divergence >= 0.5
                        ? "text-red-400"
                        : selectedData.divergence >= 0.2
                        ? "text-yellow-400"
                        : "text-green-400"
                    }`}
                  >
                    Δ {selectedData.divergence.toFixed(2)}
                  </div>
                  <div className="text-[10px] text-muted-foreground">Расхождение</div>
                </div>
              </div>

              {/* Action button */}
              <button
                onClick={() => goToCountry(selectedData.code)}
                className="mt-3 w-full rounded-lg bg-blue-500/20 hover:bg-blue-500/30 text-blue-400 text-xs font-medium py-2 transition-colors"
              >
                Открыть профиль страны →
              </button>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Map legend / instructions */}
      <div className="absolute bottom-3 left-3 z-30">
        <div className="flex items-center gap-3 rounded-lg bg-zinc-900/80 backdrop-blur-sm border border-white/5 px-3 py-1.5">
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded-full bg-blue-500/60" />
            <span className="text-[10px] text-muted-foreground">Сотрудничество</span>
          </div>
          <div className="w-px h-3 bg-white/10" />
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded-full bg-amber-400/60" />
            <span className="text-[10px] text-muted-foreground">Нейтрально</span>
          </div>
          <div className="w-px h-3 bg-white/10" />
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-3 rounded-full bg-red-500/60" />
            <span className="text-[10px] text-muted-foreground">Напряжённость</span>
          </div>
          <div className="w-px h-3 bg-white/10" />
          <span className="text-[10px] text-muted-foreground/50">⭕ = кол-во статей</span>
        </div>
      </div>

      {/* Zoom hint */}
      {!isZoomed && (
        <div className="absolute bottom-3 right-3 z-30">
          <div className="rounded-lg bg-zinc-900/60 backdrop-blur-sm border border-white/5 px-2.5 py-1">
            <span className="text-[10px] text-muted-foreground/40">
              Скролл — масштаб · Клик — приблизить
            </span>
          </div>
        </div>
      )}
    </div>
  );
}
