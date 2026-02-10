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
import { Card, CardContent } from "@/components/ui/card";
import type { UNVoteYear } from "@/lib/api";

interface Props {
  data: UNVoteYear[];
}

function pctColor(pct: number) {
  if (pct >= 75) return "text-green-400";
  if (pct >= 60) return "text-yellow-400";
  return "text-red-400";
}

export default function UNVotesChart({ data }: Props) {
  if (!data || data.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
        Данных пока нет
      </div>
    );
  }

  const last = data[data.length - 1];

  return (
    <div className="space-y-4">
      {/* Metrics */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Card className="border-border bg-card">
          <CardContent className="p-4 text-center">
            <div className={`text-2xl font-bold ${pctColor(last.agreement_pct)}`}>
              {last.agreement_pct.toFixed(1)}%
            </div>
            <div className="mt-1 text-[11px] text-muted-foreground">Совпадение</div>
          </CardContent>
        </Card>
        <Card className="border-border bg-card">
          <CardContent className="p-4 text-center">
            <div className="text-2xl font-bold">{last.total_votes}</div>
            <div className="mt-1 text-[11px] text-muted-foreground">Голосований</div>
          </CardContent>
        </Card>
        <Card className="border-border bg-card">
          <CardContent className="p-4 text-center">
            <div className="text-2xl font-bold text-green-400">{last.agree_with_russia}</div>
            <div className="mt-1 text-[11px] text-muted-foreground">Совпали</div>
          </CardContent>
        </Card>
        <Card className="border-border bg-card">
          <CardContent className="p-4 text-center">
            <div className="text-2xl font-bold text-red-400">{last.disagree_with_russia}</div>
            <div className="mt-1 text-[11px] text-muted-foreground">Разошлись</div>
          </CardContent>
        </Card>
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={240}>
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="unFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#8b5cf6" stopOpacity={0.15} />
              <stop offset="100%" stopColor="#8b5cf6" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis
            dataKey="year"
            tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            domain={[30, 100]}
            tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => `${v}%`}
          />
          <ReferenceLine y={50} stroke="rgba(255,255,255,0.1)" strokeDasharray="4 4" />
          <Tooltip
            contentStyle={{
              backgroundColor: "rgba(15,15,20,0.95)",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(value: number | undefined, name: string | undefined) => {
              const v = value ?? 0;
              if (name === "agreement_pct") return [`${v.toFixed(1)}%`, "Совпадение"];
              return [v, name ?? ""];
            }}
            labelFormatter={(label) => `${label} г.`}
          />
          <Area
            type="monotone"
            dataKey="agreement_pct"
            stroke="#8b5cf6"
            strokeWidth={2}
            fill="url(#unFill)"
            dot={{ r: 3, fill: "#8b5cf6" }}
            activeDot={{ r: 5, fill: "#8b5cf6" }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
