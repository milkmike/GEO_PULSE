"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import HomeHot from "@/components/HomeHot";
import RelationsThermometer from "@/components/RelationsThermometer";
import PageShell from "@/components/ui/PageShell";
import WorldMap from "@/components/WorldMap";
import { useFeatureFlags } from "@/components/FeatureFlagsProvider";
import { api } from "@/lib/api";
import { pluralRu } from "@/lib/format";
import type { Brief, CountrySummary, Signal } from "@/lib/types";

const REFRESH_MS = 120_000;

/** Worded scale under the map; the map itself drops its numeric colorbar. */
function MapLegend() {
  return (
    <div className="px-2 pb-1 pt-2">
      <div
        aria-hidden="true"
        className="h-1.5 rounded-full"
        style={{ background: "linear-gradient(90deg, #ef4444, #f97316 25%, #fbbf24 40%, #6b7280 50%, #34d399 65%, #10b981)" }}
      />
      <div className="mt-1 flex justify-between text-xs text-dim">
        <span>−100 · враждебность</span>
        <span>0</span>
        <span>союзники · +100</span>
      </div>
    </div>
  );
}

export default function HomePage() {
  const { signalDetail } = useFeatureFlags();
  const [countries, setCountries] = useState<CountrySummary[]>([]);
  const [countriesError, setCountriesError] = useState(false);
  const [signals, setSignals] = useState<Signal[]>([]);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [highlighted, setHighlighted] = useState<string | null>(null);

  useEffect(() => {
    const load = () => {
      api.countries()
        .then((d) => { setCountries(d.countries); setCountriesError(false); })
        .catch(() => setCountriesError(true));
      api.signals().then((d) => setSignals(d.signals)).catch(() => {});
      api.worldBrief().then(setBrief).catch(() => {});
    };
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => clearInterval(timer);
  }, []);

  const mapEntries = useMemo(
    () => countries.map((c) => ({
      iso3: c.iso3, code: c.code, name: c.name, score: c.score, level: c.level, delta_24h: c.delta_24h,
    })),
    [countries],
  );
  const total = countries.length || 99;

  return (
    <PageShell active="/">

      <header className="pb-5 pt-6">
        <h1 className="display text-h2 leading-tight sm:text-h1">Россия и мир</h1>
        <p className="mt-2 max-w-3xl text-base leading-6 text-dim">
          Как {total} {pluralRu(total, "страна относится", "страны относятся", "стран относятся")} к России.
          Индекс от −100 до +100 складывается из позиции страны и тона её СМИ.
          Нажмите на страну на карте или в термометре, чтобы открыть досье.
        </p>
      </header>

      {countriesError && !countries.length && (
        <p role="alert" className="card mb-3 p-4 text-sm text-dim">
          Не удалось загрузить индекс стран. Обновите страницу через минуту.
        </p>
      )}

      <section aria-label="Карта и термометр отношений" className="grid grid-cols-1 gap-3 lg:h-[560px] lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="card flex min-h-0 min-w-0 flex-col p-2">
          <div className="min-h-[260px] flex-1 sm:min-h-[360px]">
            <WorldMap entries={mapEntries} selectedCountry={highlighted} showScale={false} />
          </div>
          <MapLegend />
        </div>
        <RelationsThermometer countries={countries} highlighted={highlighted} onHighlight={setHighlighted} />
      </section>

      <HomeHot brief={brief} countries={countries} signals={signals} signalDetail={signalDetail} />

      <p className="mt-8 text-sm text-dim">
        Новости по отдельной стране и ранние наблюдения по всему миру собраны в{" "}
        <Link href="/lab" className="text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-accent">лаборатории</Link>.
      </p>
    </PageShell>
  );
}
