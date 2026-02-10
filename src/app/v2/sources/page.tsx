"use client";

import { useEffect, useState, useCallback } from "react";
import { getSources, type Source, COUNTRY_FLAGS, COUNTRY_NAMES } from "@/lib/api";
import {
  createSource,
  updateSource,
  deleteSource,
  toggleSource,
  testSource,
  type SourceCreate,
  type TestResult,
} from "@/lib/api-v2";

const TIERS = ["official", "mainstream", "analytics", "independent", "opposition", "western_proxy"] as const;
const TIER_LABELS: Record<string, string> = {
  official: "🏛️ Офиц.",
  mainstream: "📰 Мейнстрим",
  analytics: "🔍 Аналитика",
  independent: "🎯 Независ.",
  opposition: "📢 Оппозиция",
  western_proxy: "🌐 Запад",
};
const TYPES = ["rss", "web", "telegram"] as const;
const COUNTRIES = ["KZ", "AM", "UZ", "KG", "TJ", "TM", "AZ", "GE", "MD", "BY"] as const;

interface EditingSource extends Partial<SourceCreate> {
  id?: number;
}

function SourceForm({
  initial,
  onSave,
  onCancel,
}: {
  initial?: EditingSource;
  onSave: (data: SourceCreate, id?: number) => Promise<void>;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<SourceCreate>({
    name: initial?.name || "",
    url: initial?.url || "",
    country_code: initial?.country_code || "KZ",
    source_type: initial?.source_type || "rss",
    weight: initial?.weight ?? 1.0,
    language: initial?.language || "ru",
    config: initial?.config || {},
    active: initial?.active ?? true,
    tier: initial?.tier || "mainstream",
  });
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(form, initial?.id);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="rounded-lg border border-blue-500/30 bg-zinc-900/80 p-4">
      <h3 className="mb-3 text-sm font-semibold">
        {initial?.id ? "Редактировать источник" : "Новый источник"}
      </h3>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <div>
          <label className="text-xs text-muted-foreground">Название</label>
          <input
            className="mt-1 w-full rounded-md border border-border bg-zinc-800 px-3 py-1.5 text-sm"
            value={form.name}
            onChange={(e) => setForm({ ...form, name: e.target.value })}
            placeholder="Tengrinews"
          />
        </div>
        <div className="sm:col-span-2">
          <label className="text-xs text-muted-foreground">URL</label>
          <input
            className="mt-1 w-full rounded-md border border-border bg-zinc-800 px-3 py-1.5 text-sm"
            value={form.url}
            onChange={(e) => setForm({ ...form, url: e.target.value })}
            placeholder="https://tengrinews.kz/rss/"
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">Страна</label>
          <select
            className="mt-1 w-full rounded-md border border-border bg-zinc-800 px-3 py-1.5 text-sm"
            value={form.country_code}
            onChange={(e) => setForm({ ...form, country_code: e.target.value })}
          >
            {COUNTRIES.map((c) => (
              <option key={c} value={c}>
                {COUNTRY_FLAGS[c]} {COUNTRY_NAMES[c]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs text-muted-foreground">Тип</label>
          <select
            className="mt-1 w-full rounded-md border border-border bg-zinc-800 px-3 py-1.5 text-sm"
            value={form.source_type}
            onChange={(e) => setForm({ ...form, source_type: e.target.value })}
          >
            {TYPES.map((t) => (
              <option key={t} value={t}>{t.toUpperCase()}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs text-muted-foreground">Tier</label>
          <select
            className="mt-1 w-full rounded-md border border-border bg-zinc-800 px-3 py-1.5 text-sm"
            value={form.tier}
            onChange={(e) => setForm({ ...form, tier: e.target.value })}
          >
            {TIERS.map((t) => (
              <option key={t} value={t}>{TIER_LABELS[t] || t}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-xs text-muted-foreground">Вес</label>
          <input
            type="number"
            step="0.1"
            min="0"
            max="5"
            className="mt-1 w-full rounded-md border border-border bg-zinc-800 px-3 py-1.5 text-sm"
            value={form.weight}
            onChange={(e) => setForm({ ...form, weight: parseFloat(e.target.value) || 1 })}
          />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">Язык</label>
          <input
            className="mt-1 w-full rounded-md border border-border bg-zinc-800 px-3 py-1.5 text-sm"
            value={form.language}
            onChange={(e) => setForm({ ...form, language: e.target.value })}
          />
        </div>
        <div className="flex items-end">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.active}
              onChange={(e) => setForm({ ...form, active: e.target.checked })}
              className="rounded"
            />
            Активен
          </label>
        </div>
      </div>
      <div className="mt-4 flex gap-2">
        <button
          onClick={handleSave}
          disabled={saving || !form.name || !form.url}
          className="rounded-md bg-blue-600 px-4 py-1.5 text-sm font-medium text-white transition hover:bg-blue-500 disabled:opacity-50"
        >
          {saving ? "Сохранение…" : initial?.id ? "Сохранить" : "Создать"}
        </button>
        <button
          onClick={onCancel}
          className="rounded-md border border-border px-4 py-1.5 text-sm text-muted-foreground transition hover:text-foreground"
        >
          Отмена
        </button>
      </div>
    </div>
  );
}

export default function V2SourcesPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<EditingSource | null>(null);
  const [creating, setCreating] = useState(false);
  const [testing, setTesting] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<{ id: number; result: TestResult } | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // Filters
  const [filterCountry, setFilterCountry] = useState<string>("all");
  const [filterTier, setFilterTier] = useState<string>("all");
  const [filterActive, setFilterActive] = useState<string>("all");
  const [search, setSearch] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getSources();
      setSources(res.sources);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(null), 3000);
  };

  const handleSave = async (data: SourceCreate, id?: number) => {
    if (id) {
      await updateSource(id, data);
      showToast("✅ Источник обновлён");
    } else {
      await createSource(data);
      showToast("✅ Источник создан");
    }
    setEditing(null);
    setCreating(false);
    refresh();
  };

  const handleToggle = async (id: number) => {
    await toggleSource(id);
    refresh();
  };

  const handleDelete = async (id: number, name: string) => {
    if (!window.confirm(`Удалить источник «${name}»? Все его статьи и анализ будут удалены.`)) return;
    await deleteSource(id);
    showToast(`🗑️ «${name}» удалён`);
    refresh();
  };

  const handleTest = async (id: number) => {
    setTesting(id);
    setTestResult(null);
    try {
      const result = await testSource(id);
      setTestResult({ id, result });
    } catch (e) {
      setTestResult({ id, result: { success: false, message: String(e) } });
    } finally {
      setTesting(null);
    }
  };

  // Filter logic
  const filtered = sources.filter((s) => {
    if (filterCountry !== "all" && s.country_code !== filterCountry) return false;
    if (filterTier !== "all" && s.tier !== filterTier) return false;
    if (filterActive === "active" && !s.active) return false;
    if (filterActive === "inactive" && s.active) return false;
    if (search && !s.name.toLowerCase().includes(search.toLowerCase()) && !s.url.toLowerCase().includes(search.toLowerCase()))
      return false;
    return true;
  });

  return (
    <div className="space-y-4">
      {/* Toast */}
      {toast && (
        <div className="fixed right-4 top-20 z-50 rounded-md bg-green-900/80 px-4 py-2 text-sm text-green-300 shadow-lg">
          {toast}
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Управление источниками</h1>
          <p className="text-sm text-muted-foreground">
            {sources.length} источников · {sources.filter((s) => s.active).length} активных
          </p>
        </div>
        <button
          onClick={() => {
            setCreating(true);
            setEditing(null);
          }}
          className="rounded-md bg-blue-600 px-4 py-1.5 text-sm font-medium text-white transition hover:bg-blue-500"
        >
          + Добавить
        </button>
      </div>

      {/* Create form */}
      {creating && (
        <SourceForm onSave={handleSave} onCancel={() => setCreating(false)} />
      )}

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <input
          className="rounded-md border border-border bg-zinc-800 px-3 py-1.5 text-sm placeholder:text-muted-foreground"
          placeholder="🔍 Поиск…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          className="rounded-md border border-border bg-zinc-800 px-2 py-1.5 text-sm"
          value={filterCountry}
          onChange={(e) => setFilterCountry(e.target.value)}
        >
          <option value="all">Все страны</option>
          {COUNTRIES.map((c) => (
            <option key={c} value={c}>{COUNTRY_FLAGS[c]} {COUNTRY_NAMES[c]}</option>
          ))}
        </select>
        <select
          className="rounded-md border border-border bg-zinc-800 px-2 py-1.5 text-sm"
          value={filterTier}
          onChange={(e) => setFilterTier(e.target.value)}
        >
          <option value="all">Все tier</option>
          {TIERS.map((t) => (
            <option key={t} value={t}>{TIER_LABELS[t]}</option>
          ))}
        </select>
        <select
          className="rounded-md border border-border bg-zinc-800 px-2 py-1.5 text-sm"
          value={filterActive}
          onChange={(e) => setFilterActive(e.target.value)}
        >
          <option value="all">Все</option>
          <option value="active">Активные</option>
          <option value="inactive">Отключённые</option>
        </select>
        <span className="ml-auto text-xs text-muted-foreground">
          {filtered.length} из {sources.length}
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full text-sm">
          <thead className="border-b border-border bg-zinc-900/50">
            <tr>
              <th className="px-3 py-2 text-left font-medium text-muted-foreground">Источник</th>
              <th className="px-3 py-2 text-left font-medium text-muted-foreground">Страна</th>
              <th className="px-3 py-2 text-left font-medium text-muted-foreground">Tier</th>
              <th className="px-3 py-2 text-center font-medium text-muted-foreground">Тип</th>
              <th className="px-3 py-2 text-right font-medium text-muted-foreground">Статей</th>
              <th className="px-3 py-2 text-right font-medium text-muted-foreground">Релевантных</th>
              <th className="px-3 py-2 text-right font-medium text-muted-foreground">Sentiment</th>
              <th className="px-3 py-2 text-center font-medium text-muted-foreground">Последний сбор</th>
              <th className="px-3 py-2 text-center font-medium text-muted-foreground">Действия</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map((s) => (
              <>
                <tr
                  key={s.id}
                  className={`border-b border-border transition hover:bg-zinc-800/50 ${!s.active ? "opacity-50" : ""}`}
                >
                  <td className="px-3 py-2">
                    <div className="font-medium">{s.name}</div>
                    <div className="max-w-xs truncate text-xs text-muted-foreground">{s.url}</div>
                  </td>
                  <td className="px-3 py-2">
                    <span>{COUNTRY_FLAGS[s.country_code]} {s.country_code}</span>
                  </td>
                  <td className="px-3 py-2 text-xs">{TIER_LABELS[s.tier] || s.tier}</td>
                  <td className="px-3 py-2 text-center">
                    <span className="rounded bg-zinc-700 px-1.5 py-0.5 text-xs uppercase">
                      {s.source_type}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{s.article_count.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right tabular-nums">{s.relevant_count.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right tabular-nums">
                    {s.avg_sentiment !== null ? (
                      <span className={s.avg_sentiment < -0.2 ? "text-red-400" : s.avg_sentiment > 0.2 ? "text-green-400" : "text-muted-foreground"}>
                        {s.avg_sentiment.toFixed(2)}
                      </span>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-3 py-2 text-center text-xs text-muted-foreground">
                    {s.last_collected
                      ? new Date(s.last_collected).toLocaleString("ru-RU", {
                          day: "2-digit",
                          month: "2-digit",
                          hour: "2-digit",
                          minute: "2-digit",
                        })
                      : "—"}
                  </td>
                  <td className="px-3 py-2">
                    <div className="flex items-center justify-center gap-1">
                      <button
                        onClick={() => handleToggle(s.id)}
                        className="rounded px-1.5 py-0.5 text-xs hover:bg-zinc-700"
                        title={s.active ? "Отключить" : "Включить"}
                      >
                        {s.active ? "⏸️" : "▶️"}
                      </button>
                      <button
                        onClick={() => {
                          setEditing({
                            id: s.id,
                            name: s.name,
                            url: s.url,
                            country_code: s.country_code,
                            source_type: s.source_type,
                            weight: s.weight,
                            language: s.language,
                            config: s.config as Record<string, unknown>,
                            active: s.active,
                            tier: s.tier,
                          });
                          setCreating(false);
                        }}
                        className="rounded px-1.5 py-0.5 text-xs hover:bg-zinc-700"
                        title="Редактировать"
                      >
                        ✏️
                      </button>
                      <button
                        onClick={() => handleTest(s.id)}
                        disabled={testing === s.id}
                        className="rounded px-1.5 py-0.5 text-xs hover:bg-zinc-700 disabled:opacity-50"
                        title="Тестировать"
                      >
                        {testing === s.id ? "⏳" : "🧪"}
                      </button>
                      <button
                        onClick={() => handleDelete(s.id, s.name)}
                        className="rounded px-1.5 py-0.5 text-xs text-red-400 hover:bg-red-900/30"
                        title="Удалить"
                      >
                        🗑️
                      </button>
                    </div>
                  </td>
                </tr>
                {/* Inline edit form */}
                {editing?.id === s.id && (
                  <tr key={`edit-${s.id}`}>
                    <td colSpan={9} className="px-2 py-2">
                      <SourceForm
                        initial={editing}
                        onSave={handleSave}
                        onCancel={() => setEditing(null)}
                      />
                    </td>
                  </tr>
                )}
                {/* Test result */}
                {testResult?.id === s.id && (
                  <tr key={`test-${s.id}`}>
                    <td colSpan={9} className="px-3 py-2">
                      <div
                        className={`rounded-md p-3 text-sm ${
                          testResult.result.success
                            ? "border border-green-500/30 bg-green-900/20 text-green-300"
                            : "border border-red-500/30 bg-red-900/20 text-red-300"
                        }`}
                      >
                        <div className="font-medium">
                          {testResult.result.success ? "✅" : "❌"} {testResult.result.message}
                        </div>
                        {testResult.result.sample && (
                          <div className="mt-1 text-xs text-muted-foreground">
                            <div className="font-medium">{testResult.result.sample.title}</div>
                            {testResult.result.sample.body && (
                              <div className="mt-1 line-clamp-2">{testResult.result.sample.body}</div>
                            )}
                          </div>
                        )}
                        <button
                          onClick={() => setTestResult(null)}
                          className="mt-2 text-xs text-muted-foreground hover:text-foreground"
                        >
                          Закрыть
                        </button>
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
