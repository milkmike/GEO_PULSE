"use client";

import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import SiteHeader from "@/components/SiteHeader";
import AgreementsPanel from "@/components/AgreementsPanel";
import Markdown from "@/components/Markdown";
import Plot from "@/components/Plot";
import SignalFeed from "@/components/SignalFeed";
import TradePanel from "@/components/TradePanel";
import UNVotesPanel from "@/components/UNVotesPanel";
import SparklineStrip from "@/components/SparklineStrip";
import DynamicsPanel from "@/components/DynamicsPanel";
import TierDivergencePanel from "@/components/TierDivergencePanel";
import SanctionsPanel from "@/components/SanctionsPanel";
import VoxPanel from "@/components/VoxPanel";
import { api, apiBase } from "@/lib/api";
import { fmt, fmtDate, LEVEL_COLOR, LEVEL_RU } from "@/lib/format";
import type {
  AgreementGroup, Brief, Dossier, EntityStat, FxSeries, Headline, Signal, TopicStat,
  TradeYear, UNVoteYear,
} from "@/lib/types";

const CHART_BASE = {
  paper_bgcolor: "rgba(0,0,0,0)",
  plot_bgcolor: "rgba(0,0,0,0)",
  margin: { t: 8, b: 26, l: 38, r: 10 },
  xaxis: { color: "#6b7280", gridcolor: "#1f2937" },
  yaxis: { color: "#6b7280", gridcolor: "#1f2937", zerolinecolor: "#374151" },
  showlegend: true,
  legend: { font: { color: "#9ca3af", size: 10 }, orientation: "h" as const, y: 1.12 },
};

export default function CountryPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = use(params);
  const cc = code.toUpperCase();

  const [dossier, setDossier] = useState<Dossier | null>(null);
  const [topics, setTopics] = useState<TopicStat[]>([]);
  const [headlines, setHeadlines] = useState<{ source: string; headlines: Headline[] } | null>(null);
  const [entities, setEntities] = useState<EntityStat[]>([]);
  const [fx, setFx] = useState<FxSeries | null>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [un, setUn] = useState<UNVoteYear[]>([]);
  const [trade, setTrade] = useState<TradeYear[]>([]);
  const [agreements, setAgreements] = useState<AgreementGroup[]>([]);
  const [error, setError] = useState(false);
  const [showEmbed, setShowEmbed] = useState(false);
  const [briefState, setBriefState] = useState<"idle" | "generating" | "empty" | "error">("idle");

  useEffect(() => {
    setBrief(null);
    setBriefState("idle");
    api.dossier(cc).then(setDossier).catch(() => setError(true));
    api.topics(cc).then((d) => setTopics(d.topics)).catch(() => {});
    api.headlines(cc).then(setHeadlines).catch(() => {});
    api.entities(cc).then((d) => setEntities(d.entities)).catch(() => {});
    api.fx(cc).then(setFx).catch(() => {});
    api.countryBrief(cc).then(setBrief).catch(() => {}); // 404 = not generated yet → button
    api.unVotes(cc).then((d) => setUn(d.data)).catch(() => {});
    api.trade(cc).then((d) => setTrade(d.data)).catch(() => {});
    api.agreements(cc).then((d) => setAgreements(d.agreements)).catch(() => {});
  }, [cc]);

  const generateBrief = () => {
    setBriefState("generating");
    api.generateCountryBrief(cc)
      .then((b) => { setBrief(b); setBriefState("idle"); })
      .catch((e) => setBriefState(String(e).includes("404") ? "empty" : "error"));
  };

  const indexChart = useMemo(() => {
    if (!dossier || dossier.index_history.length < 2) return null;
    const color = dossier.index ? LEVEL_COLOR[dossier.index.level] : "#9ca3af";
    const data: unknown[] = [
      {
        x: dossier.index_history.map((h) => h.day),
        y: dossier.index_history.map((h) => h.score),
        name: "RRI",
        mode: "lines",
        line: { color, width: 2 },
        fill: "tozeroy",
        fillcolor: `${color}22`,
        hovertemplate: "%{x}: %{y:+.1f}<extra>RRI</extra>",
      },
    ];
    if (dossier.temperature_history.length > 1) {
      data.push({
        x: dossier.temperature_history.map((t) => t.time.slice(0, 10)),
        y: dossier.temperature_history.map((t) => t.temperature),
        name: "Термометр (свои СМИ)",
        mode: "lines",
        line: { color: "#38bdf8", width: 1.5, dash: "dot" },
        hovertemplate: "%{x}: %{y:+.1f}°<extra>термометр</extra>",
      });
    }
    return data;
  }, [dossier]);

  const gdeltChart = useMemo(() => {
    if (!dossier || dossier.gdelt.length < 2) return null;
    return [
      {
        x: dossier.gdelt.map((g) => g.day),
        y: dossier.gdelt.map((g) => g.tone),
        name: "Тон GDELT",
        mode: "lines",
        line: { color: "#a78bfa", width: 1.5 },
        hovertemplate: "%{x}: %{y:.2f}<extra>тон</extra>",
      },
      {
        x: dossier.gdelt.map((g) => g.day),
        y: dossier.gdelt.map((g) => g.volume),
        name: "Объём (статей/день)",
        yaxis: "y2",
        type: "bar",
        marker: { color: "#1f2937" },
        hovertemplate: "%{x}: %{y}<extra>объём</extra>",
      },
    ];
  }, [dossier]);

  const fxChart = useMemo(() => {
    if (!fx?.currency || fx.series.length < 2) return null;
    return [
      {
        x: fx.series.map((p) => p.day),
        y: fx.series.map((p) => p.rate_to_rub),
        name: `${fx.currency}/RUB`,
        mode: "lines",
        line: { color: "#fbbf24", width: 1.5 },
        hovertemplate: "%{x}: %{y:.4f} ₽<extra></extra>",
      },
    ];
  }, [fx]);

  if (error) {
    return (
      <main className="mx-auto max-w-[1100px] px-3 pb-10">
        <SiteHeader />
        <div className="reveal reveal-1 px-1 py-10 text-center">
          <p className="text-dim">Страна не найдена или API недоступен.</p>
          <Link href="/" className="text-accent">← на карту</Link>
        </div>
      </main>
    );
  }
  if (!dossier) {
    return (
      <main className="mx-auto max-w-[1100px] px-3 pb-10">
        <SiteHeader />
        <div className="reveal reveal-1 px-1 py-10 text-center text-dim">
          Загрузка досье…
        </div>
      </main>
    );
  }

  const { country, index } = dossier;
  const color = index ? LEVEL_COLOR[index.level] : "#9ca3af";
  const embedSnippet = `<iframe src="${typeof window !== "undefined" ? window.location.origin : ""}/embed/${cc}" width="320" height="180" frameborder="0" loading="lazy"></iframe>`;

  return (
    <main className="mx-auto max-w-[1100px] px-3 pb-10">
      <SiteHeader />

      <div className="reveal reveal-1 flex flex-wrap items-baseline gap-3 pb-3 pt-8">
        <h1 className="display text-[32px] leading-tight">
          {country.flag} {country.name}{" "}
          <span className="font-normal text-dim">↔ Россия</span>
        </h1>
        <button
          onClick={() => setShowEmbed(!showEmbed)}
          className="ml-auto rounded-md border border-line px-2.5 py-1 text-[11px] text-dim transition-colors hover:border-ru-blue hover:text-ru-white"
        >
          {"</> embed"}
        </button>
      </div>

      {showEmbed && (
        <div className="card mb-3 px-4 py-3">
          <div className="card-title pb-1">Виджет для встраивания</div>
          <code className="block select-all break-all rounded bg-panel2 px-3 py-2 text-[11px] text-gray-400">
            {embedSnippet}
          </code>
        </div>
      )}

      <div className="text-xs text-dim">
        {country.region_name ?? country.region} ·{" "}
        {country.tier === 1 ? "глубокое покрытие" : "GDELT-мониторинг"}
        {country.memberships.length > 0 && ` · ${country.memberships.join(", ").toUpperCase()}`}
        {country.sanctions_on_russia && " · санкции против РФ"}
        {country.baseline_note && ` · ${country.baseline_note}`}
      </div>

      {index ? (
        <div className="mt-2 flex flex-wrap items-end gap-x-6 gap-y-2">
          <span className="tnum text-5xl font-bold" style={{ color }}>
            {fmt(index.score)}
          </span>
          <span
            className="mb-1.5 rounded-full border px-3 py-0.5 text-xs"
            style={{ color, borderColor: color }}
          >
            {LEVEL_RU[index.level]}
          </span>
          {[
            ["структурный", index.structural],
            ["медиа", index.media],
            ["буст", index.boost],
            ["24ч", index.delta_24h],
            ["7д", index.delta_7d],
          ].map(([label, v]) => (
            <div key={label as string} className="mb-1">
              <div className="text-[10px] uppercase tracking-wider text-dim">{label}</div>
              <div className="tnum text-base font-semibold">{fmt(v as number | null)}</div>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-3 text-sm text-dim">Индекс ещё не рассчитан</div>
      )}

      {dossier && <div className="mt-4"><SparklineStrip dossier={dossier} /></div>}

      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <DynamicsPanel code={cc} dossier={dossier} headlines={headlines} topics={topics} />

        {indexChart && (
          <section className="card md:col-span-2">
            <div className="card-title px-4 pt-3">Индекс и термометр · 90 дней</div>
            <Plot
              data={indexChart}
              layout={{ ...CHART_BASE, height: 230 } as unknown as Record<string, unknown>}
              className="w-full"
            />
          </section>
        )}

        {gdeltChart && (
          <section className="card">
            <div className="card-title px-4 pt-3">GDELT: тон и объём о России</div>
            <Plot
              data={gdeltChart}
              layout={{
                ...CHART_BASE, height: 210,
                yaxis2: { overlaying: "y", side: "right", color: "#374151", showgrid: false },
              } as unknown as Record<string, unknown>}
              className="w-full"
            />
          </section>
        )}

        {fxChart && (
          <section className="card">
            <div className="card-title px-4 pt-3">Курс {fx?.currency} к рублю (ЦБ РФ)</div>
            <Plot
              data={fxChart}
              layout={{ ...CHART_BASE, height: 210, showlegend: false } as unknown as Record<string, unknown>}
              className="w-full"
            />
          </section>
        )}

        <UNVotesPanel data={un} />
        <TradePanel data={trade} />
        <AgreementsPanel items={agreements} />

        {topics.length > 0 && (
          <section className="card">
            <div className="card-title px-4 pb-2 pt-3">Темы · 30 дней</div>
            <div className="px-4 pb-3">
              {topics.map((t) => (
                <div key={t.topic} className="flex items-center justify-between border-b border-dashed border-line py-1 text-[13px]">
                  <span>{t.label}</span>
                  <span className="tnum text-xs text-dim">
                    {t.articles} ст. ·{" "}
                    <span className={t.avg_sentiment > 0 ? "text-ally" : t.avg_sentiment < 0 ? "text-hostile" : ""}>
                      {fmt(t.avg_sentiment)}
                    </span>
                  </span>
                </div>
              ))}
            </div>
          </section>
        )}

        {entities.length > 0 && (
          <section className="card">
            <div className="card-title px-4 pb-2 pt-3">Сущности в повестке · 30 дней</div>
            <div className="flex flex-wrap gap-1.5 px-4 pb-3">
              {entities.map((e) => (
                <span
                  key={e.key}
                  className="rounded-full border border-line bg-panel2 px-2.5 py-0.5 text-[11px]"
                  title={`тон ${fmt(e.avg_sentiment)}`}
                >
                  {e.name} <span className="text-dim">×{e.mentions}</span>
                </span>
              ))}
            </div>
          </section>
        )}

        <TierDivergencePanel code={cc} />
        <SanctionsPanel code={cc} />
        <VoxPanel code={cc} />

        {headlines && headlines.headlines.length > 0 && (
          <section className="card md:col-span-2">
            <div className="card-title px-4 pb-1 pt-3">
              Заголовки ({headlines.source === "gdelt" ? "GDELT" : "собственные источники"})
            </div>
            <div className="px-4 pb-3">
              {headlines.headlines.map((h, i) => (
                <div key={i} className="border-b border-dashed border-line py-1.5 text-[13px]">
                  <a
                    href={h.url ?? "#"}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="hover:text-accent"
                  >
                    {h.title}
                  </a>
                  <div className="text-[11px] text-dim">
                    {h.source}
                    {h.sentiment != null && ` · тон ${fmt(h.sentiment)}`}
                    {h.tier && ` · ${h.tier}`}
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        <section className="card">
          <div className="card-title px-4 pb-1 pt-3">Сигналы · 90 дней</div>
          <div className="max-h-[300px] overflow-y-auto">
            <SignalFeed
              signals={dossier.signals.slice(0, 12) as unknown as Signal[]}
              emptyText="Сигналов не было"
            />
          </div>
        </section>

        <section className="card md:col-span-2">
          <div className="card-title px-4 pb-1 pt-3">AI-досье</div>
          <div className="max-h-[360px] overflow-y-auto px-4 pb-3">
            {brief ? (
              <>
                <Markdown text={brief.content} citations={brief.citations ?? brief.meta?.citations} />
                <div className="mt-2 flex items-center gap-3 text-[11px] text-dim">
                  <span>{brief.model} · {fmtDate(brief.created_at)}</span>
                  <button onClick={generateBrief} className="underline hover:text-ru-white">
                    обновить
                  </button>
                </div>
              </>
            ) : briefState === "generating" ? (
              <div className="flex items-center gap-2 py-3 text-xs text-dim">
                <span className="inline-block h-3 w-3 animate-spin rounded-full border border-dim border-t-accent" />
                Генерируется ИИ-досье… это занимает ~30–60 секунд.
              </div>
            ) : briefState === "empty" ? (
              <div className="py-3 text-xs text-dim">
                Недостаточно данных для брифинга по этой стране (нет индекса, GDELT и своих статей).
              </div>
            ) : (
              <div className="py-2">
                <p className="mb-2 text-xs text-dim">
                  {briefState === "error"
                    ? "Не удалось собрать досье — попробуйте ещё раз."
                    : "Сводка по стране ещё не собрана."}
                </p>
                <button
                  onClick={generateBrief}
                  className="rounded bg-accent/15 px-3 py-1.5 text-[13px] text-accent transition-colors hover:bg-accent/25"
                >
                  Собрать AI-досье
                </button>
              </div>
            )}
          </div>
        </section>
      </div>

      <div className="mt-4 text-[11px] text-dim">
        API: <code className="text-gray-500">{apiBase()}/api/v2/countries/{cc}</code>
      </div>
    </main>
  );
}
