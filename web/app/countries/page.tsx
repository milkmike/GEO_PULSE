"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import PageShell from "@/components/ui/PageShell";
import Card from "@/components/ui/Card";
import Delta from "@/components/ui/Delta";
import LevelChip from "@/components/ui/LevelChip";
import EmptyLine from "@/components/ui/EmptyLine";
import LowDataBadge from "@/components/LowDataBadge";
import { api } from "@/lib/api";
import { fmt } from "@/lib/format";
import type { CountrySummary } from "@/lib/types";

const REGION_LABELS: Record<string, string> = {
  "cis": "Постсоветское пространство",
  "west_europe": "Западная Европа",
  "east_europe": "Восточная Европа",
  "balkans": "Балканы и Юго-Восточная Европа",
  "north_america": "Северная Америка",
  "latin_america": "Латинская Америка",
  "middle_east": "Ближний Восток",
  "north_africa": "Северная Африка",
  "africa": "Африка",
  "east_asia": "Восточная Азия",
  "south_asia": "Южная Азия",
  "se_asia": "Юго-Восточная Азия",
  "oceania": "Океания"
};

type SortKey = "name" | "score" | "level" | "delta_7d" | "low_data";
const COLUMNS: { key: SortKey; title: string }[] = [
  { key: "name", title: "Страна" }, { key: "score", title: "Индекс" },
  { key: "level", title: "Уровень" }, { key: "delta_7d", title: "За неделю" },
  { key: "low_data", title: "Мало данных" },
];
const LEVEL_ORDER = { hostile: 0, tension: 1, cooling: 2, neutral: 3, partner: 4, ally: 5 };

export default function CountriesPage() {
  const [countries, setCountries] = useState<CountrySummary[]>([]);
  const [regions, setRegions] = useState<Record<string, string>>({});
  const [region, setRegion] = useState("");
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [reload, setReload] = useState(0);
  const [sort, setSort] = useState<{ key: SortKey; ascending: boolean }>({ key: "score", ascending: false });

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setFailed(false);
    api.countries().then((data) => {
      if (!controller.signal.aborted) setCountries(data.countries);
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    api.meta().then((data) => { if (!controller.signal.aborted) setRegions(data.regions ?? {}); }).catch(() => {});
    return () => controller.abort();
  }, [reload]);

  const regionOptions = useMemo(() => Object.keys(regions).length ? regions
    : Object.fromEntries([...new Set(countries.map((country) => country.region).filter(Boolean))].map((value) => [value, REGION_LABELS[value] ?? value])), [regions, countries]);
  const rows = useMemo(() => countries.filter((country) => !region || country.region === region).sort((a, b) => {
    const value = (country: CountrySummary) => sort.key === "level" ? LEVEL_ORDER[country.level]
      : sort.key === "low_data" ? Number(Boolean(country.low_data)) : country[sort.key];
    const left = value(a), right = value(b);
    if (left == null) return right == null ? a.name.localeCompare(b.name, "ru") : 1;
    if (right == null) return -1;
    const comparison = typeof left === "string" && typeof right === "string" ? left.localeCompare(right, "ru") : Number(left) - Number(right);
    return (sort.ascending ? comparison : -comparison) || a.name.localeCompare(b.name, "ru");
  }), [countries, region, sort]);

  return <PageShell active="/countries">
    <h1 className="display pb-5 pt-8 text-h1">Страны</h1>
    <label className="mb-5 flex flex-wrap items-center gap-3 text-sm">Регион
      <select value={region} onChange={(event) => setRegion(event.target.value)} className="min-h-11 max-w-full rounded border border-line bg-panel px-3 focus-visible:outline-2 focus-visible:outline-accent">
        <option value="">Все регионы</option>
        {Object.entries(regionOptions).sort((a, b) => a[1].localeCompare(b[1], "ru")).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
      </select>
    </label>
    {loading ? <div role="status"><EmptyLine>Загружаем страны…</EmptyLine></div>
      : failed ? <div role="alert"><EmptyLine>Не удалось загрузить страны.</EmptyLine><button type="button" onClick={() => setReload((value) => value + 1)} className="min-h-11 text-sm text-accent">Повторить загрузку</button></div>
      : <Card className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Индекс отношений стран с Россией</caption>
          <thead className="border-b border-line text-xs text-dim"><tr>
            {COLUMNS.map(({ key, title }) => <th key={key} scope="col" aria-sort={sort.key === key ? sort.ascending ? "ascending" : "descending" : "none"} className="whitespace-nowrap px-3 font-normal">
              <button type="button" onClick={() => setSort((current) => ({ key, ascending: current.key === key ? !current.ascending : key === "name" }))} className="min-h-11 focus-visible:outline-2 focus-visible:outline-accent">
                {title} {sort.key === key && <span aria-hidden="true">{sort.ascending ? "↑" : "↓"}</span>}
              </button>
            </th>)}
          </tr></thead>
          <tbody className="divide-y divide-line">
            {rows.map((country) => <tr key={country.code} className="hover:bg-panel2">
              <th scope="row" className="px-3 font-normal"><Link href={`/country/${country.code}`} className="flex min-h-11 items-center gap-2 text-accent focus-visible:outline-2 focus-visible:outline-accent"><span aria-hidden="true">{country.flag}</span>{country.name}</Link></th>
              <td className="tnum px-3">{fmt(country.score)}</td>
              <td className="px-3"><LevelChip level={country.level} /></td>
              <td className="px-3"><Delta value={country.delta_7d} /></td>
              <td className="px-3">{country.low_data && <LowDataBadge />}</td>
            </tr>)}
          </tbody>
        </table>
        {!rows.length && <div className="px-3"><EmptyLine>В этом регионе стран нет.</EmptyLine></div>}
      </Card>}
  </PageShell>;
}
