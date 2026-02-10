"use client";

import { useEffect, useState, useCallback } from "react";
import {
  getAdminSummary,
  getAdminUsage,
  getAdminKeys,
  getAdminHealth,
  getAdminUsageByScript,
  type AdminSummary,
  type AdminUsageRow,
  type AdminKeyInfo,
  type AdminHealthService,
  type AdminScriptUsage,
} from "@/lib/api";
import {
  LineChart,
  Line,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";

// ── Summary Cards ──────────────────────────────────────

function SummaryCards({ summary }: { summary: AdminSummary | null }) {
  if (!summary) return <CardsSkeleton />;

  const cards = [
    {
      icon: "💰",
      label: "Сегодня",
      value: `$${summary.total_cost_today.toFixed(4)}`,
    },
    {
      icon: "📅",
      label: "Неделя",
      value: `$${summary.total_cost_week.toFixed(4)}`,
    },
    {
      icon: "📆",
      label: "Месяц",
      value: `$${summary.total_cost_month.toFixed(4)}`,
    },
    {
      icon: "📊",
      label: "Вызовов сегодня",
      value: summary.calls_today.toLocaleString("ru-RU"),
    },
    {
      icon: "🏆",
      label: "Топ сервис",
      value: summary.top_service || "—",
    },
    {
      icon: "🤖",
      label: "Топ модель",
      value: summary.top_model?.split("/").pop() || "—",
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
      {cards.map((c) => (
        <div
          key={c.label}
          className="rounded-xl border border-white/10 bg-white/5 p-4 backdrop-blur"
        >
          <div className="mb-1 text-2xl">{c.icon}</div>
          <div className="text-xs text-muted-foreground">{c.label}</div>
          <div className="mt-1 text-lg font-semibold text-foreground">
            {c.value}
          </div>
        </div>
      ))}
    </div>
  );
}

function CardsSkeleton() {
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
      {Array.from({ length: 6 }).map((_, i) => (
        <div
          key={i}
          className="h-24 animate-pulse rounded-xl border border-white/10 bg-white/5"
        />
      ))}
    </div>
  );
}

// ── Cost Chart ─────────────────────────────────────────

const SERVICE_COLORS: Record<string, string> = {
  openrouter: "#8b5cf6",
  jina: "#06b6d4",
  openai: "#22c55e",
  comtrade: "#f59e0b",
};

function CostChart({
  data,
  period,
  onPeriodChange,
}: {
  data: AdminUsageRow[];
  period: string;
  onPeriodChange: (p: string) => void;
}) {
  // Pivot data: date -> {date, openrouter, jina, ...}
  const byDate: Record<string, Record<string, number>> = {};
  for (const row of data) {
    if (!byDate[row.date]) byDate[row.date] = { date: 0 };
    byDate[row.date][row.service] =
      (byDate[row.date][row.service] || 0) + row.cost_usd;
  }

  const chartData = Object.entries(byDate)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, vals]) => ({
      date: date.slice(5), // MM-DD
      ...vals,
    }));

  const services = [...new Set(data.map((d) => d.service))];

  const periods = [
    { label: "7 дней", value: "day" },
    { label: "30 дней", value: "week" },
    { label: "90 дней", value: "month" },
  ];

  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold">📈 Расходы по дням</h2>
        <div className="flex gap-1">
          {periods.map((p) => (
            <button
              key={p.value}
              onClick={() => onPeriodChange(p.value)}
              className={`rounded-md px-3 py-1 text-xs transition-colors ${
                period === p.value
                  ? "bg-blue-600 text-white"
                  : "bg-white/10 text-muted-foreground hover:bg-white/20"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>
      </div>
      {chartData.length === 0 ? (
        <div className="flex h-64 items-center justify-center text-muted-foreground">
          Нет данных за выбранный период
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={300}>
          <LineChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#333" />
            <XAxis dataKey="date" stroke="#888" fontSize={12} />
            <YAxis stroke="#888" fontSize={12} tickFormatter={(v) => `$${v}`} />
            <Tooltip
              contentStyle={{
                background: "#1a1a2e",
                border: "1px solid rgba(255,255,255,0.1)",
                borderRadius: 8,
              }}
              formatter={(value: number | undefined) => [`$${(value ?? 0).toFixed(4)}`, ""]}
            />
            <Legend />
            {services.map((svc) => (
              <Line
                key={svc}
                type="monotone"
                dataKey={svc}
                stroke={SERVICE_COLORS[svc] || "#888"}
                strokeWidth={2}
                dot={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}

// ── API Keys Table ─────────────────────────────────────

function KeysTable({
  keys,
  health,
  onCheckHealth,
  healthLoading,
}: {
  keys: AdminKeyInfo[];
  health: AdminHealthService[];
  onCheckHealth: () => void;
  healthLoading: boolean;
}) {
  const healthMap: Record<string, AdminHealthService> = {};
  for (const h of health) healthMap[h.service] = h;

  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-5">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold">🔑 API ключи</h2>
        <button
          onClick={onCheckHealth}
          disabled={healthLoading}
          className="rounded-md bg-blue-600 px-3 py-1.5 text-xs font-medium text-white transition-colors hover:bg-blue-700 disabled:opacity-50"
        >
          {healthLoading ? "Проверка…" : "Проверить"}
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/10 text-left text-xs text-muted-foreground">
              <th className="pb-2">Сервис</th>
              <th className="pb-2">Ключ</th>
              <th className="pb-2">ENV</th>
              <th className="pb-2">Статус</th>
              <th className="pb-2">Latency</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => {
              const h = healthMap[k.service];
              return (
                <tr
                  key={k.service}
                  className="border-b border-white/5"
                >
                  <td className="py-2.5 font-medium">{k.service}</td>
                  <td className="py-2.5 font-mono text-xs text-muted-foreground">
                    {k.key_masked || "—"}
                  </td>
                  <td className="py-2.5 font-mono text-xs text-muted-foreground">
                    {k.env_var}
                  </td>
                  <td className="py-2.5">
                    <span
                      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${
                        k.status === "active"
                          ? "bg-green-500/10 text-green-400"
                          : k.status === "missing"
                          ? "bg-red-500/10 text-red-400"
                          : "bg-yellow-500/10 text-yellow-400"
                      }`}
                    >
                      <span
                        className={`h-1.5 w-1.5 rounded-full ${
                          k.status === "active"
                            ? "bg-green-400"
                            : k.status === "missing"
                            ? "bg-red-400"
                            : "bg-yellow-400"
                        }`}
                      />
                      {k.status === "active"
                        ? "Активен"
                        : k.status === "missing"
                        ? "Отсутствует"
                        : "Заблокирован"}
                    </span>
                  </td>
                  <td className="py-2.5 text-xs text-muted-foreground">
                    {h ? (
                      <span
                        className={
                          h.status === "ok"
                            ? "text-green-400"
                            : h.status === "degraded"
                            ? "text-yellow-400"
                            : "text-red-400"
                        }
                      >
                        {h.latency_ms != null
                          ? `${h.latency_ms}ms`
                          : h.status}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Script Usage Chart ─────────────────────────────────

const SCRIPT_COLORS = [
  "#8b5cf6",
  "#06b6d4",
  "#22c55e",
  "#f59e0b",
  "#ef4444",
  "#ec4899",
];

function ScriptUsageChart({ scripts }: { scripts: AdminScriptUsage[] }) {
  if (scripts.length === 0) {
    return (
      <div className="rounded-xl border border-white/10 bg-white/5 p-5">
        <h2 className="mb-4 text-lg font-semibold">📦 Использование по скриптам</h2>
        <div className="flex h-64 items-center justify-center text-muted-foreground">
          Нет данных
        </div>
      </div>
    );
  }

  const chartData = scripts.map((s) => ({
    name: s.script.replace(".py", ""),
    "Токены вх.": s.tokens_in,
    "Токены вых.": s.tokens_out,
    "Стоимость ($)": parseFloat(s.cost_usd.toFixed(4)),
    calls: s.total_calls,
  }));

  return (
    <div className="rounded-xl border border-white/10 bg-white/5 p-5">
      <h2 className="mb-4 text-lg font-semibold">📦 Использование по скриптам</h2>
      <div className="grid gap-6 lg:grid-cols-2">
        {/* Token chart */}
        <div>
          <h3 className="mb-2 text-sm text-muted-foreground">Токены</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#333" />
              <XAxis dataKey="name" stroke="#888" fontSize={11} />
              <YAxis stroke="#888" fontSize={11} />
              <Tooltip
                contentStyle={{
                  background: "#1a1a2e",
                  border: "1px solid rgba(255,255,255,0.1)",
                  borderRadius: 8,
                }}
              />
              <Legend />
              <Bar dataKey="Токены вх." fill="#8b5cf6" radius={[4, 4, 0, 0]} />
              <Bar dataKey="Токены вых." fill="#06b6d4" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Cost chart */}
        <div>
          <h3 className="mb-2 text-sm text-muted-foreground">Стоимость</h3>
          <ResponsiveContainer width="100%" height={260}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#333" />
              <XAxis dataKey="name" stroke="#888" fontSize={11} />
              <YAxis stroke="#888" fontSize={11} tickFormatter={(v) => `$${v}`} />
              <Tooltip
                contentStyle={{
                  background: "#1a1a2e",
                  border: "1px solid rgba(255,255,255,0.1)",
                  borderRadius: 8,
                }}
                formatter={(value: number | undefined) => [`$${(value ?? 0).toFixed(4)}`, ""]}
              />
              <Bar dataKey="Стоимость ($)" fill="#22c55e" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* Stats table */}
      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-white/10 text-left text-xs text-muted-foreground">
              <th className="pb-2">Скрипт</th>
              <th className="pb-2 text-right">Вызовы</th>
              <th className="pb-2 text-right">Токены (вх)</th>
              <th className="pb-2 text-right">Токены (вых)</th>
              <th className="pb-2 text-right">Стоимость</th>
              <th className="pb-2 text-right">Ср. время</th>
            </tr>
          </thead>
          <tbody>
            {scripts.map((s) => (
              <tr key={s.script} className="border-b border-white/5">
                <td className="py-2 font-mono text-xs">{s.script}</td>
                <td className="py-2 text-right">
                  {s.total_calls.toLocaleString("ru-RU")}
                </td>
                <td className="py-2 text-right">
                  {s.tokens_in.toLocaleString("ru-RU")}
                </td>
                <td className="py-2 text-right">
                  {s.tokens_out.toLocaleString("ru-RU")}
                </td>
                <td className="py-2 text-right font-medium text-green-400">
                  ${s.cost_usd.toFixed(4)}
                </td>
                <td className="py-2 text-right text-muted-foreground">
                  {s.avg_duration_ms}ms
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Main Page ──────────────────────────────────────────

export default function AdminPage() {
  const [summary, setSummary] = useState<AdminSummary | null>(null);
  const [usageData, setUsageData] = useState<AdminUsageRow[]>([]);
  const [keys, setKeys] = useState<AdminKeyInfo[]>([]);
  const [health, setHealth] = useState<AdminHealthService[]>([]);
  const [scripts, setScripts] = useState<AdminScriptUsage[]>([]);
  const [period, setPeriod] = useState("week");
  const [healthLoading, setHealthLoading] = useState(false);
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(async () => {
    try {
      const [summaryRes, usageRes, keysRes, scriptsRes] = await Promise.all([
        getAdminSummary(),
        getAdminUsage(period),
        getAdminKeys(),
        getAdminUsageByScript(period === "day" ? 1 : period === "week" ? 7 : 30),
      ]);
      setSummary(summaryRes);
      setUsageData(usageRes.data);
      setKeys(keysRes.keys);
      setScripts(scriptsRes.scripts);
    } catch (e) {
      console.error("Failed to load admin data:", e);
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const checkHealth = async () => {
    setHealthLoading(true);
    try {
      const res = await getAdminHealth();
      setHealth(res.services);
    } catch (e) {
      console.error("Health check failed:", e);
    } finally {
      setHealthLoading(false);
    }
  };

  const handlePeriodChange = (p: string) => {
    setPeriod(p);
    setLoading(true);
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">⚙️ Admin Dashboard</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Мониторинг API вызовов, расходов и ключей
          </p>
        </div>
        <button
          onClick={() => {
            setLoading(true);
            loadData();
          }}
          className="rounded-md bg-white/10 px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-white/20"
        >
          🔄 Обновить
        </button>
      </div>

      {/* Summary */}
      <SummaryCards summary={summary} />

      {/* Cost Chart */}
      {loading ? (
        <div className="h-80 animate-pulse rounded-xl border border-white/10 bg-white/5" />
      ) : (
        <CostChart
          data={usageData}
          period={period}
          onPeriodChange={handlePeriodChange}
        />
      )}

      {/* Keys */}
      <KeysTable
        keys={keys}
        health={health}
        onCheckHealth={checkHealth}
        healthLoading={healthLoading}
      />

      {/* Script Usage */}
      {loading ? (
        <div className="h-80 animate-pulse rounded-xl border border-white/10 bg-white/5" />
      ) : (
        <ScriptUsageChart scripts={scripts} />
      )}
    </div>
  );
}
