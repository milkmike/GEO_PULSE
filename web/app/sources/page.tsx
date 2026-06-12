"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { fmtDate, safeHttpUrl } from "@/lib/format";
import type { Meta, SourceHealthRow, SourceRow } from "@/lib/types";

const TIERS: Record<string, { label: string; cls: string }> = {
  official: { label: "Официальный", cls: "bg-red-500/15 text-red-400" },
  state: { label: "Государственный", cls: "bg-red-500/15 text-red-400" },
  mainstream: { label: "Мейнстрим", cls: "bg-blue-500/15 text-blue-400" },
  independent: { label: "Независимый", cls: "bg-emerald-500/15 text-emerald-400" },
  social: { label: "Соцмедиа", cls: "bg-cyan-500/15 text-cyan-400" },
  domestic_opposition: { label: "Оппозиция", cls: "bg-yellow-500/15 text-yellow-400" },
  opposition: { label: "Оппозиция (v1)", cls: "bg-yellow-500/15 text-yellow-400" },
  western_proxy: { label: "Западный прокси", cls: "bg-zinc-500/15 text-zinc-400" },
  analytics: { label: "Аналитика", cls: "bg-purple-500/15 text-purple-400" },
};

const STATUS_CLS: Record<string, string> = {
  OK: "text-emerald-400",
  STALE: "text-yellow-400",
  DEAD: "text-red-400",
};

export default function SourcesPage() {
  const [sources, setSources] = useState<SourceRow[]>([]);
  const [health, setHealth] = useState<Map<number, SourceHealthRow>>(new Map());
  const [meta, setMeta] = useState<Meta | null>(null);
  const [loading, setLoading] = useState(true);
  const [fCountry, setFCountry] = useState<string | null>(null);
  const [fTier, setFTier] = useState<string | null>(null);
  const [fLang, setFLang] = useState<string | null>(null);
  const [fStatus, setFStatus] = useState<string | null>(null);
  const [showMatrix, setShowMatrix] = useState(false);

  useEffect(() => {
    Promise.all([api.sources(), api.healthSources(), api.meta()])
      .then(([s, h, m]) => {
        setSources(s.sources);
        setHealth(new Map(h.sources.map((r) => [r.source_id, r])));
        setMeta(m);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const countries = useMemo(
    () => [...new Set(sources.map((s) => s.country_code))].sort(),
    [sources],
  );
  const languages = useMemo(
    () =>
      [...new Set(sources.map((s) => s.language).filter(Boolean))].sort() as string[],
    [sources],
  );

  const filtered = useMemo(
    () =>
      sources
        .filter((s) => {
          const st = health.get(s.id)?.status ?? (s.active ? "DEAD" : "OFF");
          return (
            (!fCountry || s.country_code === fCountry) &&
            (!fTier || s.tier === fTier) &&
            (!fLang || s.language === fLang) &&
            (!fStatus || st === fStatus)
          );
        })
        .sort((a, b) => b.article_count - a.article_count),
    [sources, health, fCountry, fTier, fLang, fStatus],
  );

  const totalArticles = sources.reduce((t, s) => t + s.article_count, 0);

  // coverage matrix: country -> has ru / en / native active source
  const matrix = useMemo(() => {
    if (!meta) return [];
    const byCountry = new Map<string, SourceRow[]>();
    for (const s of sources.filter((x) => x.active)) {
      const arr = byCountry.get(s.country_code) ?? [];
      arr.push(s);
      byCountry.set(s.country_code, arr);
    }
    return meta.countries.map((c) => {
      const srcs = byCountry.get(c.code) ?? [];
      const langs = new Set(srcs.map((s) => s.language));
      const native = (c.langs ?? []).some((l) => langs.has(l));
      return {
        code: c.code,
        name: c.name,
        flag: c.flag,
        ru: langs.has("ru"),
        en: langs.has("en"),
        native,
      };
    });
  }, [meta, sources]);

  if (loading)
    return (
      <main className="mx-auto max-w-[1200px] px-3 py-8 text-dim">Загрузка…</main>
    );

  const chip = (active: boolean) =>
    `cursor-pointer rounded px-2 py-0.5 text-[11px] ${
      active ? "bg-accent/20 text-accent" : "bg-white/5 text-dim hover:text-fg"
    }`;

  return (
    <main className="mx-auto max-w-[1200px] px-3 pb-8">
      <header className="flex flex-wrap items-center gap-3 py-3">
        <h1 className="text-base font-semibold tracking-wider">📡 Источники</h1>
        <nav className="ml-auto text-xs text-dim">
          <Link href="/" className="hover:text-accent">
            ← на главную
          </Link>
        </nav>
      </header>

      <p className="mb-3 max-w-3xl text-[13px] text-dim">
        Все источники платформы — с тиром доверия, языком и живостью. Мы показываем
        и работающие, и замолчавшие фиды: прозрачность важнее красивой статистики.
      </p>

      <div className="mb-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {(
          [
            ["Источников", sources.length],
            ["Активных", sources.filter((s) => s.active).length],
            ["Статей собрано", totalArticles.toLocaleString("ru")],
            ["Стран", countries.length],
          ] as [string, string | number][]
        ).map(([label, v]) => (
          <div key={label} className="card px-4 py-3">
            <div className="text-xl font-semibold">{v}</div>
            <div className="text-[11px] uppercase text-dim">{label}</div>
          </div>
        ))}
      </div>

      <section className="card mb-3 px-4 py-3">
        <button
          className="text-[13px] text-accent"
          onClick={() => setShowMatrix(!showMatrix)}
        >
          {showMatrix ? "▾" : "▸"} Матрица языкового покрытия (ru / en / native)
        </button>
        {showMatrix && (
          <div className="mt-2 grid max-h-[300px] grid-cols-2 gap-x-6 overflow-y-auto sm:grid-cols-3 lg:grid-cols-4">
            {matrix.map((r) => (
              <button
                key={r.code}
                onClick={() => setFCountry(r.code)}
                className="flex items-center justify-between border-b border-white/5 py-1 text-left text-[12px] hover:bg-white/5"
              >
                <span className="truncate">
                  {r.flag} {r.name}
                </span>
                <span className="shrink-0 font-mono">
                  <span className={r.ru ? "text-emerald-400" : "text-zinc-600"}>
                    ru
                  </span>{" "}
                  <span className={r.en ? "text-emerald-400" : "text-zinc-600"}>
                    en
                  </span>{" "}
                  <span className={r.native ? "text-emerald-400" : "text-zinc-600"}>
                    nat
                  </span>
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      <div className="mb-3 flex flex-wrap items-center gap-1.5 text-[11px]">
        <select
          value={fCountry ?? ""}
          onChange={(e) => setFCountry(e.target.value || null)}
          className="rounded bg-white/5 px-2 py-1"
        >
          <option value="">Все страны</option>
          {countries.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        {Object.entries(TIERS).map(([k, t]) => (
          <span
            key={k}
            className={chip(fTier === k)}
            onClick={() => setFTier(fTier === k ? null : k)}
          >
            {t.label}
          </span>
        ))}
        <select
          value={fLang ?? ""}
          onChange={(e) => setFLang(e.target.value || null)}
          className="rounded bg-white/5 px-2 py-1"
        >
          <option value="">Все языки</option>
          {languages.map((l) => (
            <option key={l} value={l}>
              {l}
            </option>
          ))}
        </select>
        {["OK", "STALE", "DEAD"].map((s) => (
          <span
            key={s}
            className={chip(fStatus === s)}
            onClick={() => setFStatus(fStatus === s ? null : s)}
          >
            {s}
          </span>
        ))}
        <span className="ml-auto text-dim">
          {filtered.length} из {sources.length}
        </span>
      </div>

      <section className="card divide-y divide-white/5">
        {filtered.map((s) => {
          const h = health.get(s.id);
          const t = TIERS[s.tier] ?? { label: s.tier, cls: "bg-white/5 text-dim" };
          const safeUrl = s.url ? safeHttpUrl(s.url) : null;
          return (
            <div
              key={s.id}
              className="flex flex-wrap items-center gap-2 px-4 py-2 text-[13px]"
            >
              {safeUrl ? (
                <a
                  href={safeUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="min-w-0 flex-1 truncate hover:text-accent"
                >
                  {s.name}
                </a>
              ) : (
                <span className="min-w-0 flex-1 truncate">{s.name}</span>
              )}
              <span className="text-dim">{s.country_code}</span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] ${t.cls}`}>
                {t.label}
              </span>
              <span className="w-6 text-dim">{s.language ?? "—"}</span>
              <span className="w-20 text-right text-dim">
                {s.article_count.toLocaleString("ru")} ст.
              </span>
              <span className="w-24 text-right text-[11px] text-dim">
                {h?.last_article_at
                  ? fmtDate(h.last_article_at)
                  : s.last_collected
                    ? fmtDate(s.last_collected)
                    : "—"}
              </span>
              <span
                className={`w-12 text-right text-[11px] font-semibold ${
                  STATUS_CLS[h?.status ?? ""] ?? "text-zinc-600"
                }`}
              >
                {s.active ? (h?.status ?? "—") : "выкл"}
              </span>
            </div>
          );
        })}
      </section>
    </main>
  );
}
