"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import CountryRanking from "@/components/CountryRanking";
import Filters, { type FilterState } from "@/components/Filters";
import HealthBadge from "@/components/HealthBadge";
import HeadlinesFeed from "@/components/HeadlinesFeed";
import Markdown from "@/components/Markdown";
import SignalFeed from "@/components/SignalFeed";
import WorldMap from "@/components/WorldMap";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { Brief, CountrySummary, Headline, Meta, Signal } from "@/lib/types";

export default function HomePage() {
  const [countries, setCountries] = useState<CountrySummary[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [headlines, setHeadlines] = useState<Headline[]>([]);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [filters, setFilters] = useState<FilterState>({ region: null, level: null, topic: null });
  const [topicCounts, setTopicCounts] =
    useState<Record<string, { articles: number; avg_sentiment: number | null }> | null>(null);

  useEffect(() => {
    const load = () => {
      api.countries().then((d) => setCountries(d.countries)).catch(() => {});
      api.signals().then((d) => setSignals(d.signals)).catch(() => {});
      api.worldHeadlines().then((d) => setHeadlines(d.headlines)).catch(() => {});
      api.worldBrief().then(setBrief).catch(() => {});
    };
    load();
    api.meta().then(setMeta).catch(() => {});
    const t = setInterval(load, 120_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    if (!filters.topic) {
      setTopicCounts(null);
      return;
    }
    api
      .topicCountries(filters.topic)
      .then((d) =>
        setTopicCounts(
          Object.fromEntries(
            d.countries.map((c) => [
              c.country_code,
              { articles: c.articles, avg_sentiment: c.avg_sentiment },
            ]),
          ),
        ),
      )
      .catch(() => setTopicCounts({}));
  }, [filters.topic]);

  const filtered = useMemo(
    () =>
      countries.filter(
        (c) =>
          (!filters.region || c.region === filters.region) &&
          (!filters.level || c.level === filters.level),
      ),
    [countries, filters.region, filters.level],
  );

  const mapEntries = useMemo(
    () =>
      filtered.map((c) => ({
        iso3: c.iso3, code: c.code, name: c.name,
        score: c.score, level: c.level, delta_24h: c.delta_24h,
      })),
    [filtered],
  );

  const updatedAt = countries[0]?.updated_at;

  return (
    <main className="mx-auto max-w-[1500px] px-3 pb-8">
      <header className="flex flex-wrap items-center gap-3 py-3">
        <h1 className="text-base font-semibold tracking-wider">
          🌍 GEO PULSE <span className="text-accent">МИР ↔ РОССИЯ</span>
        </h1>
        <HealthBadge />
        <nav className="ml-auto flex items-center gap-4 text-xs text-dim">
          <Link href="/about" className="hover:text-accent">о проекте</Link>
          <Link href="/signals" className="hover:text-accent">все сигналы</Link>
          {updatedAt && <span>обновлено {fmtDate(updatedAt)}</span>}
        </nav>
      </header>

      {meta && (
        <Filters
          regions={meta.regions}
          topics={meta.topics}
          value={filters}
          onChange={setFilters}
        />
      )}

      <div className="grid gap-3 lg:grid-cols-[1fr_380px]">
        <section className="card min-h-[420px] lg:h-[58vh]">
          <WorldMap entries={mapEntries} />
        </section>

        <section className="card flex max-h-[58vh] min-h-[320px] flex-col">
          <div className="card-title px-4 pb-1 pt-3">
            {filters.topic && meta
              ? `Линза: ${meta.topics[filters.topic]}`
              : "Страны по индексу отношений"}
          </div>
          <CountryRanking countries={filtered} topicCounts={topicCounts ?? undefined} />
        </section>
      </div>

      <div className="mt-3 grid gap-3 lg:grid-cols-3">
        <section className="card">
          <div className="card-title px-4 pb-1 pt-3">Главные новости дня</div>
          <div className="max-h-[340px] overflow-y-auto">
            <HeadlinesFeed items={headlines} />
          </div>
        </section>

        <section className="card">
          <div className="card-title flex items-baseline justify-between px-4 pb-1 pt-3">
            <span>Сигналы медиаполя</span>
            <Link href="/signals" className="text-[11px] normal-case text-dim hover:text-accent">
              все →
            </Link>
          </div>
          <div className="max-h-[340px] overflow-y-auto">
            <SignalFeed signals={signals.slice(0, 30)} />
          </div>
        </section>

        <section className="card">
          <div className="card-title px-4 pb-1 pt-3">Брифинг «Россия и мир»</div>
          <div className="max-h-[340px] overflow-y-auto px-4 pb-3">
            {brief ? (
              <>
                <Markdown text={brief.content} citations={brief.citations ?? brief.meta?.citations} />
                <div className="mt-2 text-[11px] text-dim">
                  {brief.model} · {fmtDate(brief.created_at)}
                </div>
              </>
            ) : (
              <div className="py-2 text-xs text-dim">Брифинг ещё не сгенерирован</div>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
