"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import CountryRanking from "@/components/CountryRanking";
import Filters, { type FilterState } from "@/components/Filters";
import HealthBadge from "@/components/HealthBadge";
import HeadlinesFeed from "@/components/HeadlinesFeed";
import Markdown from "@/components/Markdown";
import SignalFeed from "@/components/SignalFeed";
import SiteHeader from "@/components/SiteHeader";
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
  const [topicBrief, setTopicBrief] = useState<(Brief & { label?: string }) | null>(null);
  const [topicBriefLoading, setTopicBriefLoading] = useState(false);

  useEffect(() => {
    const load = () => {
      api.countries().then((d) => setCountries(d.countries)).catch(() => {});
      api.signals().then((d) => setSignals(d.signals)).catch(() => {});
      api.worldBrief().then(setBrief).catch(() => {});
    };
    load();
    api.meta().then(setMeta).catch(() => {});
    const t = setInterval(load, 120_000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const loadHeadlines = () => {
      api.worldHeadlines(24, 20, filters.region, filters.topic).then((d) => setHeadlines(d.headlines)).catch(() => {});
    };
    loadHeadlines();
    const t = setInterval(loadHeadlines, 120_000);
    return () => clearInterval(t);
  }, [filters.region, filters.topic]);

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

  useEffect(() => {
    if (!filters.topic) {
      setTopicBrief(null);
      return;
    }
    setTopicBriefLoading(true);
    api
      .topicBrief(filters.topic)
      .then(setTopicBrief)
      .catch(() => setTopicBrief(null))
      .finally(() => setTopicBriefLoading(false));
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
      <SiteHeader
        active="/"
        right={
          <span className="flex items-center gap-3">
            <HealthBadge />
            {updatedAt && (
              <span className="tnum text-[10px] text-dim">
                обновлено {fmtDate(updatedAt)}
              </span>
            )}
          </span>
        }
      />

      {meta && (
        <Filters
          regions={meta.regions}
          topics={meta.topics}
          value={filters}
          onChange={setFilters}
        />
      )}

      <div className="reveal reveal-2 grid gap-3 lg:grid-cols-[1fr_380px]">
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

      <div className="reveal reveal-4 mt-3 grid gap-3 lg:grid-cols-3">
        <section className="card">
          <div className="card-title px-4 pb-1 pt-3">
            {[
              "Главные новости дня",
              filters.region && meta ? `${meta.regions[filters.region]}` : null,
              filters.topic && meta ? `${meta.topics[filters.topic]}` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </div>
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
          <div className="card-title px-4 pb-1 pt-3">
            {filters.topic && meta
              ? `Брифинг · ${meta.topics[filters.topic]}`
              : "Брифинг «Россия и мир»"}
          </div>
          <div className="max-h-[340px] overflow-y-auto px-4 pb-3">
            {filters.topic ? (
              topicBriefLoading ? (
                <div className="px-4 py-3 text-xs text-dim">
                  Готовлю тематический брифинг — первые ~10 секунд при смене линзы…
                </div>
              ) : topicBrief ? (
                <>
                  <Markdown text={topicBrief.content} citations={topicBrief.citations ?? topicBrief.meta?.citations} />
                  <div className="mt-2 text-[11px] text-dim">
                    {topicBrief.model} · {fmtDate(topicBrief.created_at)}
                  </div>
                </>
              ) : (
                <div className="py-2 text-xs text-dim">Недостаточно данных по теме</div>
              )
            ) : brief ? (
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
