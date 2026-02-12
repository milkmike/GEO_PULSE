"use client";

import { useState } from "react";
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  Scatter,
  ComposedChart,
} from "recharts";
import type { TemperaturePoint } from "@/lib/api";
import InfoPopover from "@/components/InfoPopover";
import { glossary } from "@/lib/glossary";

interface Props {
  data: TemperaturePoint[];
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
  });
}

function avgTemperatureColor(data: TemperaturePoint[]): string {
  const avg = data.reduce((s, p) => s + p.temperature, 0) / data.length;
  if (avg <= -10) return "#3b82f6"; // blue
  if (avg <= 0) return "#60a5fa";   // light blue
  if (avg <= 10) return "#eab308";  // yellow
  if (avg <= 20) return "#f97316";  // orange
  return "#ef4444";                  // red
}

const COMPONENT_COLORS: Record<string, { color: string; label: string }> = {
  diplomatic: { color: "#3b82f6", label: "Дипломатия" },
  military:   { color: "#ef4444", label: "Военные" },
  economic:   { color: "#22c55e", label: "Экономика" },
  cultural:   { color: "#a855f7", label: "Культура" },
  security:   { color: "#f59e0b", label: "Безопасность" },
};

// Custom dot for anomaly points
function AnomalyDot(props: any) {
  const { cx, cy, payload } = props;
  if (payload?.isAnomaly) {
    return (
      <circle cx={cx} cy={cy} r={5} fill="#ef4444" stroke="#ef444466" strokeWidth={3} />
    );
  }
  return null;
}

export default function TemperatureChart({ data }: Props) {
  const [showComponents, setShowComponents] = useState(false);

  if (!data || data.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
        Данных пока нет
      </div>
    );
  }

  const mainColor = avgTemperatureColor(data);

  const chartData = data.map((p) => ({
    date: fmtDate(p.time),
    temperature: p.temperature,
    articles: p.article_count,
    isAnomaly: (p.anomaly_score ?? 0) > 0.5,
    anomalyScore: p.anomaly_score,
    diplomatic: p.components?.diplomatic ?? null,
    military: p.components?.military ?? null,
    economic: p.components?.economic ?? null,
    cultural: p.components?.cultural ?? null,
    security: p.components?.security ?? null,
  }));

  const COMP_NAMES: Record<string, string> = {
    diplomatic: "Дипломатия",
    military: "Военные",
    economic: "Экономика",
    cultural: "Культура",
    security: "Безопасность",
  };

  return (
    <div>
      <ResponsiveContainer width="100%" height={300}>
        <ComposedChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="tempFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={mainColor} stopOpacity={0.15} />
              <stop offset="100%" stopColor={mainColor} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis
            dataKey="date"
            tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            label={{
              value: "Температура",
              angle: -90,
              position: "insideLeft",
              style: { fill: "rgba(255,255,255,0.4)", fontSize: 11 },
            }}
          />
          <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" strokeDasharray="4 4" />
          <Tooltip
            contentStyle={{
              backgroundColor: "rgba(15,15,20,0.95)",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(value: unknown, name: string | undefined) => {
              if (value === null || value === undefined) return [null, null];
              const v = Number(value) || 0;
              if (name === "temperature") return [`${v.toFixed(1)}°`, "Температура"];
              if (name === "articles") return [v, "Статей"];
              if (COMP_NAMES[name ?? ""]) return [`${v.toFixed(1)}°`, COMP_NAMES[name!]];
              return [v, name ?? ""];
            }}
            labelFormatter={(label) => `📅 ${label}`}
          />
          <Area
            type="monotone"
            dataKey="temperature"
            stroke={mainColor}
            strokeWidth={2}
            fill="url(#tempFill)"
            dot={<AnomalyDot />}
            activeDot={{ r: 4, fill: mainColor }}
          />
          {showComponents && Object.entries(COMPONENT_COLORS).map(([key, { color }]) => (
            <Line
              key={key}
              type="monotone"
              dataKey={key}
              stroke={color}
              strokeWidth={1.5}
              strokeDasharray="4 2"
              dot={false}
              connectNulls
            />
          ))}
        </ComposedChart>
      </ResponsiveContainer>

      {/* Toggle row */}
      <div className="flex items-center gap-3 mt-2 px-1">
        <button
          onClick={() => setShowComponents(!showComponents)}
          className={`text-xs px-3 py-1 rounded-full border transition-all ${
            showComponents
              ? "bg-white/10 border-white/20 text-white"
              : "border-white/10 text-muted-foreground hover:border-white/20"
          }`}
        >
          📊 Компоненты
        </button>
        <InfoPopover title="Компоненты температуры">{glossary.temperatureComponents.detail}</InfoPopover>
        {showComponents && (
          <div className="flex flex-wrap gap-3 text-[10px] text-muted-foreground">
            {Object.entries(COMPONENT_COLORS).map(([key, { color, label }]) => (
              <span key={key} className="flex items-center gap-1">
                <span className="inline-block w-3 h-0.5 rounded" style={{ backgroundColor: color }} />
                {label}
              </span>
            ))}
          </div>
        )}
        <span className="flex items-center gap-1 ml-auto text-[10px] text-muted-foreground">
          <span className="inline-block w-2 h-2 rounded-full bg-red-500" />
          Аномалия
          <InfoPopover title="Аномалии">{glossary.anomalyScore.detail}</InfoPopover>
        </span>
      </div>
    </div>
  );
}
