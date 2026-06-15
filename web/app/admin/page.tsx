"use client";

import { useCallback, useEffect, useState } from "react";
import SiteHeader from "@/components/SiteHeader";
import { AdminAuthError, adminGet } from "@/lib/api";

const KEY_STORE = "mr_admin_key";
const PERIODS = [7, 30, 90] as const;

type Visits = {
  days: number;
  totals: { views: number; uniques: number };
  daily: { day: string; views: number; uniques: number }[];
  top_paths: { path: string; views: number }[];
  top_referrers: { host: string; views: number }[];
};
type Summary = {
  total_cost_today: number;
  total_cost_week: number;
  total_cost_month: number;
  calls_today: number;
  top_service: string | null;
  top_model: string | null;
};
type HealthSvc = {
  service: string;
  status: "ok" | "degraded" | "unreachable";
  http_code: number | null;
  latency_ms: number | null;
  error: string | null;
};

const usd = (n: number) => `$${(n ?? 0).toFixed(2)}`;
const STATUS_COLOR: Record<string, string> = {
  ok: "var(--color-ally)",
  degraded: "var(--color-cooling)",
  unreachable: "var(--color-hostile)",
};

export default function AdminPage() {
  const [key, setKey] = useState<string | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [authErr, setAuthErr] = useState(false);
  const [loading, setLoading] = useState(false);
  const [days, setDays] = useState<number>(30);

  const [visits, setVisits] = useState<Visits | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [health, setHealth] = useState<HealthSvc[] | null>(null);

  const logout = () => {
    localStorage.removeItem(KEY_STORE);
    setKey(null);
    setVisits(null);
    setSummary(null);
    setHealth(null);
  };

  const load = useCallback(async (k: string, d: number) => {
    setLoading(true);
    setAuthErr(false);
    try {
      const [v, s, h] = await Promise.all([
        adminGet<Visits>(`/api/v1/admin/visits?days=${d}`, k),
        adminGet<Summary>(`/api/v1/admin/summary`, k),
        adminGet<{ services: HealthSvc[] }>(`/api/v1/admin/health`, k),
      ]);
      setVisits(v);
      setSummary(s);
      setHealth(h.services ?? []);
    } catch (e) {
      if (e instanceof AdminAuthError) {
        setAuthErr(true);
        localStorage.removeItem(KEY_STORE);
        setKey(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  // restore key on mount
  useEffect(() => {
    const k = localStorage.getItem(KEY_STORE);
    if (k) {
      setKey(k);
      load(k, 30);
    }
  }, [load]);

  // refetch visits when period changes
  useEffect(() => {
    if (!key) return;
    adminGet<Visits>(`/api/v1/admin/visits?days=${days}`, key)
      .then(setVisits)
      .catch(() => {});
  }, [days, key]);

  const submitKey = (e: React.FormEvent) => {
    e.preventDefault();
    const k = keyInput.trim();
    if (!k) return;
    localStorage.setItem(KEY_STORE, k);
    setKey(k);
    load(k, days);
  };

  // ── login gate ──
  if (!key) {
    return (
      <main className="mx-auto max-w-md px-4 pb-20">
        <SiteHeader active="/admin" />
        <form onSubmit={submitKey} className="card mt-10 p-6">
          <h1 className="display mb-1 text-lg">Вход в админку</h1>
          <p className="mb-4 text-[13px] text-dim">
            Введите <span className="tnum">ADMIN_API_KEY</span> (из серверного{" "}
            <span className="tnum">.env</span>). Ключ хранится только в этом браузере.
          </p>
          <input
            type="password"
            value={keyInput}
            onChange={(e) => setKeyInput(e.target.value)}
            placeholder="ADMIN_API_KEY"
            className="w-full rounded border border-line bg-panel2 px-3 py-2 text-fg outline-none focus:border-accent"
            autoFocus
          />
          {authErr && (
            <p className="mt-2 text-[12px] text-ru-red">Неверный ключ (или не задан на сервере).</p>
          )}
          <button
            type="submit"
            className="mt-4 w-full rounded bg-accent/15 py-2 text-accent transition-colors hover:bg-accent/25"
          >
            Войти
          </button>
        </form>
      </main>
    );
  }

  // ── dashboard ──
  return (
    <main className="mx-auto max-w-5xl px-4 pb-24">
      <SiteHeader
        active="/admin"
        right={
          <button onClick={logout} className="text-dim transition-colors hover:text-ru-white">
            выход
          </button>
        }
      />

      {loading && !visits ? (
        <p className="mt-10 text-dim">Загрузка…</p>
      ) : (
        <div className="mt-6 grid gap-5">
          {/* Посещения */}
          <section className="card p-5">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="display text-base">Посещения</h2>
              <div className="flex gap-1 text-[12px]">
                {PERIODS.map((d) => (
                  <button
                    key={d}
                    onClick={() => setDays(d)}
                    className={
                      days === d
                        ? "rounded bg-accent/20 px-2 py-1 text-accent"
                        : "rounded px-2 py-1 text-dim hover:text-ru-white"
                    }
                  >
                    {d} дн.
                  </button>
                ))}
              </div>
            </div>

            <div className="flex gap-8">
              <div>
                <div className="tnum text-3xl text-ru-white">{visits?.totals.views ?? 0}</div>
                <div className="text-[12px] uppercase tracking-wide text-dim">визитов</div>
              </div>
              <div>
                <div className="tnum text-3xl text-ru-white">{visits?.totals.uniques ?? 0}</div>
                <div className="text-[12px] uppercase tracking-wide text-dim">уникальных</div>
              </div>
            </div>

            <div className="mt-5 grid gap-6 md:grid-cols-3">
              <div>
                <h3 className="mb-2 text-[12px] uppercase tracking-wide text-dim">По дням</h3>
                <ul className="space-y-1 text-[13px]">
                  {(visits?.daily ?? []).slice(0, 12).map((r) => (
                    <li key={r.day} className="flex justify-between tnum">
                      <span className="text-dim">{r.day.slice(5)}</span>
                      <span>
                        {r.views}
                        <span className="text-dim"> / {r.uniques}</span>
                      </span>
                    </li>
                  ))}
                  {!visits?.daily.length && <li className="text-dim">— нет данных —</li>}
                </ul>
              </div>
              <div>
                <h3 className="mb-2 text-[12px] uppercase tracking-wide text-dim">Топ-страницы</h3>
                <ul className="space-y-1 text-[13px]">
                  {(visits?.top_paths ?? []).map((r) => (
                    <li key={r.path} className="flex justify-between gap-3 tnum">
                      <span className="truncate text-fg">{r.path}</span>
                      <span className="text-dim">{r.views}</span>
                    </li>
                  ))}
                </ul>
              </div>
              <div>
                <h3 className="mb-2 text-[12px] uppercase tracking-wide text-dim">Рефереры</h3>
                <ul className="space-y-1 text-[13px]">
                  {(visits?.top_referrers ?? []).map((r) => (
                    <li key={r.host} className="flex justify-between gap-3 tnum">
                      <span className="truncate text-fg">{r.host}</span>
                      <span className="text-dim">{r.views}</span>
                    </li>
                  ))}
                  {!visits?.top_referrers.length && (
                    <li className="text-dim">прямые заходы</li>
                  )}
                </ul>
              </div>
            </div>
          </section>

          {/* Расходы LLM */}
          <section className="card p-5">
            <h2 className="display mb-3 text-base">Расходы LLM</h2>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
              <Stat label="сегодня" value={usd(summary?.total_cost_today ?? 0)} />
              <Stat label="за неделю" value={usd(summary?.total_cost_week ?? 0)} />
              <Stat label="за месяц" value={usd(summary?.total_cost_month ?? 0)} />
              <Stat label="вызовов сегодня" value={String(summary?.calls_today ?? 0)} />
            </div>
            <p className="mt-3 text-[12px] text-dim">
              топ-модель: <span className="text-fg">{summary?.top_model ?? "—"}</span>
              {" · "}топ-сервис: <span className="text-fg">{summary?.top_service ?? "—"}</span>
            </p>
          </section>

          {/* Здоровье */}
          <section className="card p-5">
            <h2 className="display mb-3 text-base">Здоровье сервисов</h2>
            <ul className="grid gap-1.5 text-[13px] sm:grid-cols-2">
              {(health ?? []).map((s) => (
                <li key={s.service} className="flex items-center justify-between gap-3">
                  <span className="text-fg">{s.service}</span>
                  <span className="tnum" style={{ color: STATUS_COLOR[s.status] ?? "var(--color-dim)" }}>
                    {s.status}
                    {s.latency_ms != null && (
                      <span className="text-dim"> · {s.latency_ms}ms</span>
                    )}
                  </span>
                </li>
              ))}
              {!health?.length && <li className="text-dim">— нет данных —</li>}
            </ul>
          </section>
        </div>
      )}
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="tnum text-xl text-ru-white">{value}</div>
      <div className="text-[12px] uppercase tracking-wide text-dim">{label}</div>
    </div>
  );
}
