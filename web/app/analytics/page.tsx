"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import HeadlinesFeed from "@/components/HeadlinesFeed";
import { apiBase } from "@/lib/api";
import type { Headline } from "@/lib/types";

export default function AnalyticsPage() {
  const [items, setItems] = useState<Headline[]>([]);
  const [country, setCountry] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const qs = `hours=168&limit=100&tier=analytics${country ? `&country=${country}` : ""}`;
    fetch(`${apiBase()}/api/v2/headlines?${qs}`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => setItems(d.headlines ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [country]);

  const countries = [...new Set(items.map((h) => h.country_code).filter(Boolean))] as string[];

  return (
    <main className="mx-auto max-w-[900px] px-3 pb-8">
      <header className="flex flex-wrap items-center gap-3 py-3">
        <h1 className="text-base font-semibold tracking-wider">🔍 Аналитические центры о России</h1>
        <nav className="ml-auto text-xs text-dim">
          <Link href="/" className="hover:text-accent">← на главную</Link>
        </nav>
      </header>
      <p className="mb-3 text-[13px] text-dim">
        Публикации think tanks и OSINT-расследователей за неделю. Это не новости,
        а аналитика — у каждого центра своя оптика и свои спонсоры; читайте с поправкой.
      </p>
      <div className="mb-3 flex flex-wrap gap-1.5 text-[11px]">
        <span onClick={() => setCountry(null)}
          className={`cursor-pointer rounded px-2 py-0.5 ${!country ? "bg-accent/20 text-accent" : "bg-white/5 text-dim"}`}>все</span>
        {countries.map((c) => (
          <span key={c} onClick={() => setCountry(c)}
            className={`cursor-pointer rounded px-2 py-0.5 ${country === c ? "bg-accent/20 text-accent" : "bg-white/5 text-dim"}`}>{c}</span>
        ))}
      </div>
      <section className="card">
        {loading ? <div className="px-4 py-3 text-xs text-dim">Загрузка…</div>
                 : <HeadlinesFeed items={items} />}
      </section>
    </main>
  );
}
