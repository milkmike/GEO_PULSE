"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { API_URL } from "@/lib/api";
import InfoPopover from "@/components/InfoPopover";
import { glossary } from "@/lib/glossary";

interface ArticleInfo {
  id: number;
  title: string;
  sentiment: number;
  url: string;
  body_preview: string;
}

interface PairData {
  similarity: number;
  delta: number;
  published_at: string | null;
  [key: string]: unknown; // article_ru, article_en, etc.
}

interface SplitSource {
  source: string;
  country_code: string;
  pairs_count: number;
  delta: number;
  pairs: PairData[];
  [key: string]: unknown; // avg_sentiment_ru, avg_sentiment_en, etc.
}

interface AudienceSplitResponse {
  splits: SplitSource[];
  summary: {
    total_bilingual_sources: number;
    sources_with_significant_split: number;
    avg_split: number;
  };
}

interface AudienceSplitProps {
  country?: string;
  source?: string;
  days?: number;
  compact?: boolean;
}

function getLangsFromSplit(split: SplitSource): string[] {
  const langs: string[] = [];
  for (const key of Object.keys(split)) {
    const m = key.match(/^avg_sentiment_(\w+)$/);
    if (m) langs.push(m[1]);
  }
  return langs.length >= 2 ? langs : ["ru", "en"];
}

function getArticleFromPair(pair: PairData, lang: string): ArticleInfo | null {
  const key = `article_${lang}`;
  const art = pair[key] as ArticleInfo | undefined;
  return art || null;
}

function sentimentPosition(val: number): number {
  // Map -3..+3 to 0..100%
  return ((val + 3) / 6) * 100;
}

function deltaColor(delta: number): string {
  if (delta > 0.5) return "#ef4444"; // red
  if (delta > 0.3) return "#eab308"; // yellow
  return "#6b7280"; // gray
}

function formatSentiment(val: number): string {
  return val >= 0 ? `+${val.toFixed(2)}` : val.toFixed(2);
}

export default function AudienceSplit({
  country,
  source,
  days = 30,
  compact = false,
}: AudienceSplitProps) {
  const [data, setData] = useState<AudienceSplitResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams();
    params.set("days", String(days));
    if (country) params.set("country", country);
    if (source) params.set("source", source);

    setLoading(true);
    fetch(`${API_URL}/api/v1/audience-split?${params}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [country, source, days]);

  if (loading) {
    return (
      <Card className="border-border bg-card">
        <CardContent className="p-6">
          <div className="flex items-center gap-2 text-muted-foreground">
            <span className="animate-pulse">🎭</span>
            <span className="text-sm">Загрузка аудиторного сплита…</span>
          </div>
        </CardContent>
      </Card>
    );
  }

  if (error || !data) {
    return null; // Silent fail — don't break the page if no data
  }

  if (data.splits.length === 0) {
    if (compact) return null;
    return (
      <Card className="border-border bg-card">
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            🎭 Аудиторный сплит
            <InfoPopover title="Аудиторный сплит">{glossary.audienceSplit.detail}</InfoPopover>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted-foreground">
            Двуязычных расхождений не обнаружено за {days} дней.
          </p>
        </CardContent>
      </Card>
    );
  }

  const { splits, summary } = data;
  const displaySplits = compact ? splits.slice(0, 5) : splits;

  return (
    <Card className="border-border bg-card">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-lg flex items-center gap-2">
            🎭 Аудиторный сплит
            <InfoPopover title="Аудиторный сплит">{glossary.audienceSplit.detail}</InfoPopover>
          </CardTitle>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span>
              {summary.total_bilingual_sources} двуязычных СМИ
            </span>
            {summary.sources_with_significant_split > 0 && (
              <Badge
                variant="outline"
                className="border-red-500/30 text-red-400 text-[10px]"
              >
                ⚠ {summary.sources_with_significant_split} с расхождением
              </Badge>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-1">
        {/* Scale labels */}
        <div className="flex items-center justify-between mb-3 px-32">
          <span className="text-[10px] text-muted-foreground/60">
            ← Антироссийский
          </span>
          <span className="text-[10px] text-muted-foreground/30">0</span>
          <span className="text-[10px] text-muted-foreground/60">
            Пророссийский →
          </span>
        </div>

        {displaySplits.map((split) => {
          const langs = getLangsFromSplit(split);
          const isExpanded = expanded === split.source;
          const lineColor = deltaColor(split.delta);

          // Get sentiment values for the two languages
          const sentA = (split[`avg_sentiment_${langs[0]}`] as number) || 0;
          const sentB = (split[`avg_sentiment_${langs[1]}`] as number) || 0;

          const posA = sentimentPosition(sentA);
          const posB = sentimentPosition(sentB);
          const leftPos = Math.min(posA, posB);
          const rightPos = Math.max(posA, posB);

          return (
            <div key={split.source} className="group">
              {/* Main row */}
              <div
                className="flex items-center gap-3 py-2 px-2 rounded-lg cursor-pointer hover:bg-white/[0.03] transition-colors"
                onClick={() => setExpanded(isExpanded ? null : split.source)}
              >
                {/* Source name */}
                <div className="w-28 shrink-0 text-right">
                  <span className="text-xs font-medium truncate block">
                    {split.source}
                  </span>
                </div>

                {/* Scale bar */}
                <div className="flex-1 relative h-8">
                  {/* Background scale */}
                  <div className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-px bg-white/5" />
                  <div
                    className="absolute top-1/2 -translate-y-1/2 w-px h-3 bg-white/10"
                    style={{ left: "50%" }}
                  />

                  {/* Connecting line */}
                  <div
                    className="absolute top-1/2 -translate-y-1/2 h-0.5 rounded-full"
                    style={{
                      left: `${leftPos}%`,
                      width: `${rightPos - leftPos}%`,
                      backgroundColor: lineColor,
                      opacity: 0.7,
                    }}
                  />

                  {/* Language dots */}
                  {langs.map((lang, i) => {
                    const sent = i === 0 ? sentA : sentB;
                    const pos = sentimentPosition(sent);
                    return (
                      <div
                        key={lang}
                        className="absolute top-1/2 -translate-y-1/2 -translate-x-1/2 flex flex-col items-center"
                        style={{ left: `${pos}%` }}
                      >
                        <div
                          className="w-3 h-3 rounded-full border-2 border-background"
                          style={{ backgroundColor: lineColor }}
                        />
                        <span
                          className="text-[9px] font-mono mt-1 whitespace-nowrap"
                          style={{ color: lineColor }}
                        >
                          {lang.toUpperCase()} {formatSentiment(sent)}
                        </span>
                      </div>
                    );
                  })}
                </div>

                {/* Delta & count */}
                <div className="w-32 shrink-0 flex items-center gap-2">
                  <Badge
                    variant="outline"
                    className="text-[10px] font-mono"
                    style={{
                      borderColor: lineColor + "44",
                      color: lineColor,
                    }}
                  >
                    Δ {split.delta.toFixed(2)}
                  </Badge>
                  <span className="text-[10px] text-muted-foreground">
                    📰 {split.pairs_count}
                  </span>
                </div>
              </div>

              {/* Expanded pairs list */}
              {isExpanded && (
                <div className="ml-32 mr-4 mb-4 space-y-3 animate-in slide-in-from-top-2 duration-200">
                  {split.pairs.slice(0, 10).map((pair, idx) => {
                    const artA = getArticleFromPair(pair, langs[0]);
                    const artB = getArticleFromPair(pair, langs[1]);
                    if (!artA && !artB) return null;

                    return (
                      <div
                        key={idx}
                        className="rounded-lg border border-border/50 bg-white/[0.02] p-3"
                      >
                        <div className="flex items-center gap-2 mb-2 text-[10px] text-muted-foreground">
                          {pair.published_at && (
                            <span>{pair.published_at}</span>
                          )}
                          <span>sim: {pair.similarity.toFixed(3)}</span>
                          <Badge
                            variant="outline"
                            className="text-[9px]"
                            style={{
                              borderColor: deltaColor(pair.delta) + "44",
                              color: deltaColor(pair.delta),
                            }}
                          >
                            Δ {pair.delta.toFixed(1)}
                          </Badge>
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                          {[
                            { lang: langs[0], art: artA },
                            { lang: langs[1], art: artB },
                          ].map(({ lang, art }) =>
                            art ? (
                              <div key={lang} className="space-y-1">
                                <div className="flex items-center gap-1.5">
                                  <Badge
                                    variant="outline"
                                    className="text-[9px] px-1"
                                  >
                                    {lang.toUpperCase()}
                                  </Badge>
                                  <span
                                    className="text-[10px] font-mono"
                                    style={{
                                      color:
                                        art.sentiment > 0.05
                                          ? "#22c55e"
                                          : art.sentiment < -0.05
                                          ? "#ef4444"
                                          : "#94a3b8",
                                    }}
                                  >
                                    {formatSentiment(art.sentiment)}
                                  </span>
                                </div>
                                <a
                                  href={art.url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-xs text-foreground hover:text-blue-400 hover:underline line-clamp-2"
                                >
                                  {art.title}
                                </a>
                                {art.body_preview && (
                                  <p className="text-[10px] text-muted-foreground/60 line-clamp-3">
                                    {art.body_preview}
                                  </p>
                                )}
                              </div>
                            ) : null
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}
