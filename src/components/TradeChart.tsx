"use client";

import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from "recharts";
import { Card, CardContent } from "@/components/ui/card";
import type { TradeYear } from "@/lib/api";

interface Props {
  data: TradeYear[];
}

function fmtBln(v: number) {
  const b = v / 1e9;
  if (b >= 1) return `$${b.toFixed(1)}B`;
  const m = v / 1e6;
  return `$${m.toFixed(0)}M`;
}

export default function TradeChart({ data }: Props) {
  if (!data || data.length === 0) {
    return (
      <div className="flex h-48 items-center justify-center text-sm text-muted-foreground">
        Данных пока нет
      </div>
    );
  }

  const last = data[data.length - 1];
  const yoyColor =
    last.yoy_change_pct !== null && last.yoy_change_pct !== undefined
      ? last.yoy_change_pct >= 0
        ? "text-green-400"
        : "text-red-400"
      : "text-muted-foreground";
  const yoyText =
    last.yoy_change_pct !== null && last.yoy_change_pct !== undefined
      ? `${last.yoy_change_pct >= 0 ? "+" : ""}${last.yoy_change_pct.toFixed(1)}%`
      : "—";

  const chartData = data.map((d) => ({
    year: d.year,
    export: d.ru_export_usd / 1e9,
    import: d.ru_import_usd / 1e9,
    total: d.total_trade_usd / 1e9,
  }));

  return (
    <div className="space-y-4">
      {/* Metrics */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Card className="border-border bg-card">
          <CardContent className="p-4 text-center">
            <div className="text-2xl font-bold">{fmtBln(last.total_trade_usd)}</div>
            <div className="mt-1 text-[11px] text-muted-foreground">Товарооборот</div>
          </CardContent>
        </Card>
        <Card className="border-border bg-card">
          <CardContent className="p-4 text-center">
            <div className="text-2xl font-bold text-blue-400">{fmtBln(last.ru_export_usd)}</div>
            <div className="mt-1 text-[11px] text-muted-foreground">Экспорт РФ</div>
          </CardContent>
        </Card>
        <Card className="border-border bg-card">
          <CardContent className="p-4 text-center">
            <div className="text-2xl font-bold text-amber-400">{fmtBln(last.ru_import_usd)}</div>
            <div className="mt-1 text-[11px] text-muted-foreground">Импорт в РФ</div>
          </CardContent>
        </Card>
        <Card className="border-border bg-card">
          <CardContent className="p-4 text-center">
            <div className={`text-2xl font-bold ${yoyColor}`}>{yoyText}</div>
            <div className="mt-1 text-[11px] text-muted-foreground">Год к году</div>
          </CardContent>
        </Card>
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={260}>
        <ComposedChart data={chartData} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
          <XAxis
            dataKey="year"
            tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            tickFormatter={(v) => `$${v.toFixed(0)}B`}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "rgba(15,15,20,0.95)",
              border: "1px solid rgba(255,255,255,0.1)",
              borderRadius: 8,
              fontSize: 12,
            }}
            formatter={(value: number | undefined, name: string | undefined) => {
              const v = value ?? 0;
              const label =
                name === "export"
                  ? "Экспорт РФ"
                  : name === "import"
                  ? "Импорт в РФ"
                  : "Товарооборот";
              return [`$${v.toFixed(1)}B`, label];
            }}
            labelFormatter={(label) => `${label} г.`}
          />
          <Legend
            formatter={(value) =>
              value === "export"
                ? "Экспорт РФ"
                : value === "import"
                ? "Импорт в РФ"
                : "Товарооборот"
            }
            wrapperStyle={{ fontSize: 11, color: "rgba(255,255,255,0.6)" }}
          />
          <Bar dataKey="export" stackId="trade" fill="#3b82f6" radius={[0, 0, 0, 0]} />
          <Bar dataKey="import" stackId="trade" fill="#f59e0b" radius={[2, 2, 0, 0]} />
          <Line
            type="monotone"
            dataKey="total"
            stroke="rgba(255,255,255,0.5)"
            strokeWidth={1.5}
            strokeDasharray="4 4"
            dot={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
