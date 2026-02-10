"use client";

import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
} from "recharts";
import type { TemperaturePoint } from "@/lib/api";

interface Props {
  data: TemperaturePoint[];
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
  });
}

export default function TemperatureChart({ data }: Props) {
  if (!data || data.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
        Данных пока нет
      </div>
    );
  }

  const chartData = data.map((p) => ({
    date: fmtDate(p.time),
    temperature: p.temperature,
    articles: p.article_count,
  }));

  return (
    <ResponsiveContainer width="100%" height={300}>
      <AreaChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <defs>
          <linearGradient id="tempFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.15} />
            <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
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
          formatter={(value: number | undefined, name: string | undefined) => {
            const v = value ?? 0;
            if (name === "temperature") return [`${v.toFixed(1)}°`, "Температура"];
            if (name === "articles") return [v, "Статей"];
            return [v, name ?? ""];
          }}
          labelFormatter={(label) => `📅 ${label}`}
        />
        <Area
          type="monotone"
          dataKey="temperature"
          stroke="#3b82f6"
          strokeWidth={2}
          fill="url(#tempFill)"
          dot={false}
          activeDot={{ r: 4, fill: "#3b82f6" }}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
