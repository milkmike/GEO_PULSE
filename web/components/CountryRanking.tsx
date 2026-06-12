"use client";

import Link from "next/link";
import { motion, useReducedMotion } from "motion/react";
import { fmt, LEVEL_COLOR } from "@/lib/format";
import type { CountrySummary } from "@/lib/types";

export default function CountryRanking({
  countries,
  topicCounts,
}: {
  countries: CountrySummary[];
  /** topic lens: code → {articles, tone}; when set, shown instead of deltas */
  topicCounts?: Record<string, { articles: number; avg_sentiment: number | null }>;
}) {
  const reduce = useReducedMotion();
  return (
    <div className="flex-1 overflow-y-auto px-2 pb-3">
      {countries.map((c) => {
        const lens = topicCounts?.[c.code];
        if (topicCounts && !lens) return null;
        return (
          <Link key={c.code} href={`/country/${c.code}`}>
            <motion.div
              className="flex items-center gap-2 rounded-md px-2 py-1.5 hover:bg-panel2"
              whileHover={reduce ? undefined : { x: 3 }}
              whileTap={reduce ? undefined : { scale: 0.99 }}
              transition={{ duration: 0.15, ease: "easeOut" }}
            >
              <span
                className="h-2 w-2 flex-none rounded-full"
                style={{ background: LEVEL_COLOR[c.level] }}
              />
              <span className="w-6 flex-none text-center">{c.flag}</span>
              <span className="flex-1 truncate">{c.name}</span>
              {lens ? (
                <span className="tnum text-xs text-dim">
                  {lens.articles} ст. · {fmt(lens.avg_sentiment)}
                </span>
              ) : (
                <span className="tnum w-12 text-right text-xs text-dim">
                  {c.delta_24h != null ? fmt(c.delta_24h) : ""}
                </span>
              )}
              <span
                className="tnum w-14 text-right font-semibold"
                style={{ color: LEVEL_COLOR[c.level] }}
              >
                {fmt(c.score)}
              </span>
            </motion.div>
          </Link>
        );
      })}
    </div>
  );
}
