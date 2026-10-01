"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ChevronDown } from "lucide-react";
import CountryRanking from "@/components/CountryRanking";
import Filters, { type FilterState } from "@/components/Filters";
import HealthBadge from "@/components/HealthBadge";
import HeadlinesFeed from "@/components/HeadlinesFeed";
import Markdown from "@/components/Markdown";
import RadarPanel from "@/components/RadarPanel";
import EarlyWarningPanel from "@/components/EarlyWarningPanel";
import SignalFeed from "@/components/SignalFeed";
import SiteHeader from "@/components/SiteHeader";
import SortableGrid, { type SortableItem } from "@/components/SortableGrid";
import AgendaHighlights from "@/components/AgendaHighlights";
import DecisionWorkspace from "@/components/DecisionWorkspace";
import { useFeatureFlags } from "@/components/FeatureFlagsProvider";
import WorldMap from "@/components/WorldMap";
import { api } from "@/lib/api";
import { HOME_TIPS } from "@/lib/explain";
import { newsDateTime as fmtDate } from "@/components/NewsReadingList";
import type { Brief, CountrySummary, Headline, Meta, Signal, TopicBriefResponse } from "@/lib/types";

// Secondary data panels can still be reordered; the map and country brief stay fixed.
const HOME_ORDER = ["early-warning", "stories", "headlines", "signals", "brief", "radar"];

export default function HomePage() {
  const { storiesNavigation, signalDetail, earlyWarningRadar } = useFeatureFlags();
  const [countries, setCountries] = useState<CountrySummary[]>([]);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [headlines, setHeadlines] = useState<Headline[]>([]);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [filters, setFilters] = useState<FilterState>({ region: null, level: null, topic: null });
  const [topicCounts, setTopicCounts] =
    useState<Record<string, { articles: number; avg_sentiment: number | null }> | null>(null);
  const [topicBrief, setTopicBrief] = useState<TopicBriefResponse | null>(null);
  const [topicBriefLoading, setTopicBriefLoading] = useState(false);
  const [topicBriefError, setTopicBriefError] = useState(false);

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
      setTopicBriefError(false);
      return;
    }
    setTopicBrief(null);
    setTopicBriefError(false);
    setTopicBriefLoading(true);
    api
      .topicBrief(filters.topic)
      .then(setTopicBrief)
      .catch(() => {
        setTopicBrief(null);
        setTopicBriefError(true);
      })
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

  // Secondary data views remain available below the map and country detail.
  const homePanels: SortableItem[] = [
    {
      id: "early-warning", cellClassName: "col-span-12 lg:col-span-8",
      node: earlyWarningRadar ? <EarlyWarningPanel limit={3} /> : null,
    },
    {
      id: "stories", cellClassName: "col-span-12 lg:col-span-8",
      node: storiesNavigation ? (
        <AgendaHighlights />
      ) : null,
    },
    {
      id: "headlines", cellClassName: "col-span-12 lg:col-span-4", tip: HOME_TIPS.headlines,
      node: (
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
      ),
    },
    {
      id: "signals", cellClassName: "col-span-12 lg:col-span-4", tip: HOME_TIPS.signals,
      node: (
        <section className="card">
          <div className="card-title flex items-baseline justify-between px-4 pb-1 pt-3">
            <span>Сигналы медиаполя</span>
            <Link href="/signals" className="text-[11px] normal-case text-dim hover:text-accent">
              все →
            </Link>
          </div>
          <div className="max-h-[340px] overflow-y-auto">
            <SignalFeed signals={signals.slice(0, 30)} detailEnabled={signalDetail} />
          </div>
        </section>
      ),
    },
    {
      id: "brief", cellClassName: "col-span-12 lg:col-span-4", tip: HOME_TIPS.brief,
      node: (
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
                  Загружаю тематический брифинг…
                </div>
              ) : topicBriefError ? (
                <div className="py-2 text-xs text-dim">
                  Не удалось загрузить тематический брифинг
                </div>
              ) : topicBrief?.status === "ready" ? (
                <>
                  <Markdown text={topicBrief.content} citations={topicBrief.citations ?? topicBrief.meta?.citations} />
                  <div className="mt-2 text-[11px] text-dim">
                    {topicBrief.model} · {fmtDate(topicBrief.created_at)}
                  </div>
                </>
              ) : topicBrief?.status === "pending" ? (
                <div className="py-2 text-xs text-dim">Тематический брифинг обновляется</div>
              ) : topicBrief?.status === "insufficient" ? (
                <div className="py-2 text-xs text-dim">Недостаточно данных по теме</div>
              ) : (
                <div className="py-2 text-xs text-dim">Состояние тематического брифинга неизвестно</div>
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
      ),
    },
    {
      id: "radar", cellClassName: "col-span-12 lg:col-span-4", tip: HOME_TIPS.radar,
      node: <RadarPanel />,
    },
  ];

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

      <DecisionWorkspace
        renderMap={({ selectedCountry, onSelectCountry }) => {
          const selected = countries.find((country) => country.code === selectedCountry);
          const entries = selected && !mapEntries.some((entry) => entry.code === selectedCountry)
            ? [...mapEntries, { iso3: selected.iso3, code: selected.code, name: selected.name, score: selected.score, level: selected.level, delta_24h: selected.delta_24h }]
            : mapEntries;
          return <WorldMap entries={entries} selectedCountry={selectedCountry} onSelectCountry={onSelectCountry} />;
        }}
        mapControls={meta && <Filters regions={meta.regions} topics={meta.topics} value={filters} onChange={setFilters} />}
        activeMapFilters={Number(Boolean(filters.region)) + Number(Boolean(filters.level)) + Number(Boolean(filters.topic))}
      />

      <details className="group border-t border-line py-3">
        <summary className="flex min-h-16 cursor-pointer list-none items-center justify-between gap-4 rounded-lg px-3 py-3 hover:bg-panel focus-visible:outline-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
          <span><span className="block text-lg font-medium text-ru-white">Сравнить страны</span><span className="mt-1 block text-sm text-fg/75">Индекс отношений с Россией и его изменения</span></span>
          <ChevronDown aria-hidden="true" size={20} className="shrink-0 text-accent transition-transform group-open:rotate-180 motion-reduce:transition-none" />
        </summary>
        <div className="card mt-4 max-h-[520px] overflow-y-auto"><CountryRanking countries={filtered} topicCounts={topicCounts ?? undefined} /></div>
      </details>

      <details className="group border-t border-line py-3">
        <summary className="flex min-h-16 cursor-pointer list-none items-center justify-between gap-4 rounded-lg px-3 py-3 hover:bg-panel focus-visible:outline-2 focus-visible:outline-accent [&::-webkit-details-marker]:hidden">
          <span><span className="block text-lg font-medium text-ru-white">Больше данных</span><span className="mt-1 block text-sm text-fg/75">Новостные ленты, сводки и сигналы изменений</span></span>
          <ChevronDown aria-hidden="true" size={20} className="shrink-0 text-accent transition-transform group-open:rotate-180 motion-reduce:transition-none" />
        </summary>
        <div className="reveal reveal-2 mt-4 grid grid-cols-12 gap-3">
          <SortableGrid storageKey="home-panel-order" defaultOrder={HOME_ORDER} items={homePanels} />
        </div>
      </details>
    </main>
  );
}
