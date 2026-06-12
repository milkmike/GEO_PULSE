"use client";

import { useEffect, useState } from "react";
import HeadlinesFeed from "@/components/HeadlinesFeed";
import SiteHeader from "@/components/SiteHeader";
import { apiBase } from "@/lib/api";
import type { Headline } from "@/lib/types";

const PERIODS = [
  { label: "7 дней",   hours: 168   },
  { label: "30 дней",  hours: 720   },
  { label: "90 дней",  hours: 2160  },
  { label: "полгода",  hours: 4320  },
  { label: "2 года",   hours: 17520 },
] as const;

export default function AnalyticsPage() {
  const [items, setItems] = useState<Headline[]>([]);
  const [country, setCountry] = useState<string | null>(null);
  const [hours, setHours] = useState(720);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const qs = `hours=${hours}&limit=100&tier=analytics${country ? `&country=${country}` : ""}`;
    fetch(`${apiBase()}/api/v2/headlines?${qs}`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => setItems(d.headlines ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [hours, country]);

  const countries = [...new Set(items.map((h) => h.country_code).filter(Boolean))] as string[];

  return (
    <main className="mx-auto max-w-[900px] px-3 pb-8">
      <SiteHeader active="/analytics" />

      <div className="reveal reveal-1 pt-8">
        <h1 className="display text-[30px] leading-tight">Аналитические центры о&nbsp;России</h1>
      </div>
      <p className="lead reveal reveal-2 mb-5 mt-2 !text-[16px]">
        Публикации think tanks и OSINT-расследователей за выбранный период. Это не новости,
        а аналитика — у каждого центра своя оптика и свои спонсоры; читайте с поправкой.
      </p>

      {/* Period selector */}
      <div className="mb-3 flex flex-wrap items-center gap-1.5 text-[11px]">
        {PERIODS.map((p) => (
          <button
            key={p.hours}
            onClick={() => { setHours(p.hours); setCountry(null); }}
            className={`rounded px-2 py-0.5 ${
              hours === p.hours
                ? "bg-accent/20 text-accent"
                : "bg-white/5 text-dim hover:bg-white/10"
            }`}
          >
            {p.label}
          </button>
        ))}
        <span className="ml-auto text-dim">
          {loading ? "…" : `${items.length} материалов`}
        </span>
      </div>

      {/* Country filter */}
      <div className="mb-3 flex flex-wrap gap-1.5 text-[11px]">
        <span
          onClick={() => setCountry(null)}
          className={`cursor-pointer rounded px-2 py-0.5 ${!country ? "bg-accent/20 text-accent" : "bg-white/5 text-dim"}`}
        >
          все
        </span>
        {countries.map((c) => (
          <span
            key={c}
            onClick={() => setCountry(c)}
            className={`cursor-pointer rounded px-2 py-0.5 ${country === c ? "bg-accent/20 text-accent" : "bg-white/5 text-dim"}`}
          >
            {c}
          </span>
        ))}
      </div>

      <section className="card">
        {loading
          ? <div className="px-4 py-3 text-xs text-dim">Загрузка…</div>
          : <HeadlinesFeed items={items} />}
      </section>
    </main>
  );
}
