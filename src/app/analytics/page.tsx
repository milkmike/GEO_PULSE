"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ScatterChart,
  Scatter,
  Cell,
  ReferenceLine,
  ZAxis,
  LabelList,
} from "recharts";
import {
  getCountries,
  getCountryUNVotes,
  getCountryTrade,
  COUNTRY_CODES,
  COUNTRY_FLAGS,
  COUNTRY_NAMES,
  temperatureColor,
  type Country,
  type UNVoteYear,
  type TradeYear,
} from "@/lib/api";

interface CountryAnalytics {
  code: string;
  name: string;
  flag: string;
  temperature: number;
  unData: UNVoteYear[];
  tradeData: TradeYear[];
  lastUN: UNVoteYear | null;
  lastTrade: TradeYear | null;
}

function pctColorClass(pct: number) {
  if (pct >= 75) return "text-green-400";
  if (pct >= 60) return "text-yellow-400";
  return "text-red-400";
}

function pctBg(pct: number) {
  if (pct >= 75) return "bg-green-500/10 border-green-500/20";
  if (pct >= 60) return "bg-yellow-500/10 border-yellow-500/20";
  return "bg-red-500/10 border-red-500/20";
}

function fmtBln(v: number) {
  const b = v / 1e9;
  if (b >= 1) return `$${b.toFixed(1)}B`;
  const m = v / 1e6;
  return `$${m.toFixed(0)}M`;
}

export default function AnalyticsPage() {
  const [data, setData] = useState<CountryAnalytics[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function fetchAll() {
      try {
        const [countriesRes, ...rest] = await Promise.all([
          getCountries(365),
          ...COUNTRY_CODES.flatMap((code) => [
            getCountryUNVotes(code),
            getCountryTrade(code),
          ]),
        ]);

        const countries = countriesRes.countries;
        const analytics: CountryAnalytics[] = COUNTRY_CODES.map((code, i) => {
          const unRes = rest[i * 2] as { data: UNVoteYear[] };
          const tradeRes = rest[i * 2 + 1] as { data: TradeYear[] };
          const country = countries.find((c: Country) => c.code === code);
          const unData = unRes?.data || [];
          const tradeData = tradeRes?.data || [];

          return {
            code,
            name: COUNTRY_NAMES[code],
            flag: COUNTRY_FLAGS[code],
            temperature: country?.temperature ?? 0,
            unData,
            tradeData,
            lastUN: unData.length > 0 ? unData[unData.length - 1] : null,
            lastTrade: tradeData.length > 0 ? tradeData[tradeData.length - 1] : null,
          };
        });

        setData(analytics);
      } catch (err) {
        console.error("Failed to fetch analytics:", err);
      } finally {
        setLoading(false);
      }
    }
    fetchAll();
  }, []);

  if (loading) {
    return (
      <div className="space-y-6">
        <h1 className="text-2xl font-bold">📊 Аналитика</h1>
        <p className="text-muted-foreground">Загрузка данных…</p>
        <div className="grid gap-4 md:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-64 animate-pulse rounded-lg border border-border bg-card" />
          ))}
        </div>
      </div>
    );
  }

  // Sort by UN agreement (descending)
  const unSorted = [...data]
    .filter((d) => d.lastUN)
    .sort((a, b) => (b.lastUN?.agreement_pct ?? 0) - (a.lastUN?.agreement_pct ?? 0));

  // Sort by trade (descending)
  const tradeSorted = [...data]
    .filter((d) => d.lastTrade)
    .sort((a, b) => (b.lastTrade?.total_trade_usd ?? 0) - (a.lastTrade?.total_trade_usd ?? 0));

  // Trade bar chart data
  const tradeBarData = tradeSorted.map((d) => ({
    name: `${d.flag} ${d.name}`,
    export: (d.lastTrade?.ru_export_usd ?? 0) / 1e9,
    import: (d.lastTrade?.ru_import_usd ?? 0) / 1e9,
  }));

  // Scatter data: UN agreement vs media temperature
  const scatterData = data
    .filter((d) => d.lastUN)
    .map((d) => ({
      name: d.name,
      flag: d.flag,
      x: d.lastUN!.agreement_pct,
      y: d.temperature,
      code: d.code,
    }));

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold">📊 Аналитика</h1>
        <p className="mt-1 text-muted-foreground">
          Объективные данные: голосования в ООН, торговля с Россией и корреляция с медийной температурой
        </p>
      </div>

      {/* UN Votes Section */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold">🗳️ Голосования в ООН — совпадение с Россией</h2>
        <Card className="border-border bg-card overflow-x-auto">
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="px-4 py-3 font-medium">#</th>
                  <th className="px-4 py-3 font-medium">Страна</th>
                  <th className="px-4 py-3 font-medium text-right">Год</th>
                  <th className="px-4 py-3 font-medium text-right">Совпадение</th>
                  <th className="px-4 py-3 font-medium text-right">Голосований</th>
                  <th className="px-4 py-3 font-medium text-right">Совпали</th>
                  <th className="px-4 py-3 font-medium text-right">Разошлись</th>
                  <th className="px-4 py-3 font-medium text-right">Тренд</th>
                </tr>
              </thead>
              <tbody>
                {unSorted.map((d, i) => {
                  const last = d.lastUN!;
                  const prev = d.unData.length >= 2 ? d.unData[d.unData.length - 2] : null;
                  const trend = prev
                    ? last.agreement_pct - prev.agreement_pct
                    : 0;
                  return (
                    <tr key={d.code} className="border-b border-border/50 hover:bg-white/[0.02]">
                      <td className="px-4 py-3 text-muted-foreground">{i + 1}</td>
                      <td className="px-4 py-3 font-medium">
                        {d.flag} {d.name}
                      </td>
                      <td className="px-4 py-3 text-right text-muted-foreground">{last.year}</td>
                      <td className="px-4 py-3 text-right">
                        <span className={`font-bold ${pctColorClass(last.agreement_pct)}`}>
                          {last.agreement_pct.toFixed(1)}%
                        </span>
                      </td>
                      <td className="px-4 py-3 text-right text-muted-foreground">{last.total_votes}</td>
                      <td className="px-4 py-3 text-right text-green-400">{last.agree_with_russia}</td>
                      <td className="px-4 py-3 text-right text-red-400">{last.disagree_with_russia}</td>
                      <td className="px-4 py-3 text-right">
                        {trend !== 0 && (
                          <span className={trend > 0 ? "text-green-400" : "text-red-400"}>
                            {trend > 0 ? "↑" : "↓"} {Math.abs(trend).toFixed(1)}%
                          </span>
                        )}
                        {trend === 0 && <span className="text-muted-foreground">→</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>
      </section>

      {/* Trade Section */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold">💰 Торговля с Россией</h2>

        <Card className="border-border bg-card overflow-x-auto">
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th className="px-4 py-3 font-medium">#</th>
                  <th className="px-4 py-3 font-medium">Страна</th>
                  <th className="px-4 py-3 font-medium text-right">Год</th>
                  <th className="px-4 py-3 font-medium text-right">Товарооборот</th>
                  <th className="px-4 py-3 font-medium text-right">Экспорт РФ</th>
                  <th className="px-4 py-3 font-medium text-right">Импорт в РФ</th>
                  <th className="px-4 py-3 font-medium text-right">YoY</th>
                </tr>
              </thead>
              <tbody>
                {tradeSorted.map((d, i) => {
                  const last = d.lastTrade!;
                  const yoy = last.yoy_change_pct;
                  return (
                    <tr key={d.code} className="border-b border-border/50 hover:bg-white/[0.02]">
                      <td className="px-4 py-3 text-muted-foreground">{i + 1}</td>
                      <td className="px-4 py-3 font-medium">
                        {d.flag} {d.name}
                      </td>
                      <td className="px-4 py-3 text-right text-muted-foreground">{last.year}</td>
                      <td className="px-4 py-3 text-right font-bold">{fmtBln(last.total_trade_usd)}</td>
                      <td className="px-4 py-3 text-right text-blue-400">{fmtBln(last.ru_export_usd)}</td>
                      <td className="px-4 py-3 text-right text-amber-400">{fmtBln(last.ru_import_usd)}</td>
                      <td className="px-4 py-3 text-right">
                        {yoy !== null && yoy !== undefined ? (
                          <span className={yoy >= 0 ? "text-green-400" : "text-red-400"}>
                            {yoy >= 0 ? "+" : ""}{yoy.toFixed(1)}%
                          </span>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </CardContent>
        </Card>

        {/* Trade Bar Chart */}
        <Card className="border-border bg-card">
          <CardHeader>
            <CardTitle className="text-base">Товарооборот с Россией (последний год, $B)</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={360}>
              <BarChart data={tradeBarData} layout="vertical" margin={{ top: 0, right: 20, bottom: 0, left: 10 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                <XAxis
                  type="number"
                  tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `$${v}B`}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  tick={{ fill: "rgba(255,255,255,0.7)", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={140}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "rgba(15,15,20,0.95)",
                    border: "1px solid rgba(255,255,255,0.1)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(value: number | undefined, name: string | undefined) => [
                    `$${(value ?? 0).toFixed(2)}B`,
                    name === "export" ? "Экспорт РФ" : "Импорт в РФ",
                  ]}
                />
                <Legend
                  formatter={(value) => (value === "export" ? "Экспорт РФ" : "Импорт в РФ")}
                  wrapperStyle={{ fontSize: 11, color: "rgba(255,255,255,0.6)" }}
                />
                <Bar dataKey="export" stackId="trade" fill="#3b82f6" />
                <Bar dataKey="import" stackId="trade" fill="#f59e0b" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </section>

      {/* Correlation Section */}
      <section className="space-y-4">
        <h2 className="text-xl font-semibold">🔗 Корреляция: ООН vs Медийная температура</h2>
        <Card className="border-border bg-card">
          <CardContent className="pt-6">
            <ResponsiveContainer width="100%" height={400}>
              <ScatterChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                <XAxis
                  type="number"
                  dataKey="x"
                  name="UN Agreement"
                  domain={[30, 100]}
                  tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `${v}%`}
                  label={{
                    value: "Совпадение с Россией в ООН, %",
                    position: "insideBottom",
                    offset: -10,
                    fill: "rgba(255,255,255,0.4)",
                    fontSize: 12,
                  }}
                />
                <YAxis
                  type="number"
                  dataKey="y"
                  name="Temperature"
                  domain={[-50, 50]}
                  tick={{ fill: "rgba(255,255,255,0.5)", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `${v}°`}
                  label={{
                    value: "Медийная температура, °",
                    angle: -90,
                    position: "insideLeft",
                    offset: 10,
                    fill: "rgba(255,255,255,0.4)",
                    fontSize: 12,
                  }}
                />
                <ZAxis range={[120, 120]} />
                {/* Diagonal reference line */}
                <ReferenceLine
                  segment={[{ x: 30, y: -50 }, { x: 100, y: 50 }]}
                  stroke="rgba(255,255,255,0.1)"
                  strokeDasharray="6 4"
                />
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.08)" />
                <ReferenceLine x={65} stroke="rgba(255,255,255,0.08)" />
                <Tooltip
                  contentStyle={{
                    backgroundColor: "rgba(15,15,20,0.95)",
                    border: "1px solid rgba(255,255,255,0.1)",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                  formatter={(value: number | undefined, name: string | undefined) => {
                    const v = value ?? 0;
                    if (name === "UN Agreement") return [`${v.toFixed(1)}%`, "ООН"];
                    return [`${v.toFixed(1)}°`, "Температура"];
                  }}
                  labelFormatter={() => ""}
                />
                <Scatter data={scatterData} shape="circle">
                  {scatterData.map((entry) => (
                    <Cell key={entry.code} fill={temperatureColor(entry.y)} />
                  ))}
                  <LabelList
                    dataKey="flag"
                    position="top"
                    offset={8}
                    style={{ fontSize: 16 }}
                  />
                  <LabelList
                    dataKey="name"
                    position="bottom"
                    offset={8}
                    style={{ fontSize: 10, fill: "rgba(255,255,255,0.5)" }}
                  />
                </Scatter>
              </ScatterChart>
            </ResponsiveContainer>
            <p className="mt-4 text-center text-sm text-muted-foreground">
              Если точки близки к диагонали — наш медийный термометр коррелирует с реальным голосованием в ООН
            </p>
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
