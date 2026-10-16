"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import SignalFeed from "@/components/SignalFeed";
import { useFeatureFlags } from "@/components/FeatureFlagsProvider";
import PageShell from "@/components/ui/PageShell";
import { api } from "@/lib/api";
import { SIGNAL_RU } from "@/lib/format";
import type { Signal } from "@/lib/types";

const SEVERITIES = ["critical", "warning", "info"] as const;

export default function SignalsPage() {
  const { signalDetail, earlyWarningRadar } = useFeatureFlags();
  const [signals, setSignals] = useState<Signal[]>([]);
  const [type, setType] = useState<string>("");
  const [severity, setSeverity] = useState<string>("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    api.signals().then((d) => setSignals(d.signals)).catch(() => {});
  }, []);

  const filtered = useMemo(
    () =>
      signals.filter(
        (s) =>
          (!type || s.type === type) &&
          (!severity || s.severity === severity) &&
          (!query ||
            (s.country_name ?? "").toLowerCase().includes(query.toLowerCase()) ||
            s.title.toLowerCase().includes(query.toLowerCase())),
      ),
    [signals, type, severity, query],
  );

  return (
    <PageShell active="/signals">

      <div className="reveal reveal-1 flex items-baseline gap-3 pb-4 pt-8">
        <h1 className="display text-h2 leading-tight">Изменения</h1>
        <span className="ml-auto text-xs text-dim">
          7 дней · {filtered.length} из {signals.length}
        </span>
      </div>

      {earlyWarningRadar && <Link href="/radar" className="mb-4 inline-flex min-h-11 items-center text-sm text-accent hover:underline focus-visible:outline-2 focus-visible:outline-accent">Радар перемен →</Link>}
      <div className="mb-3 flex flex-wrap gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="страна или текст…"
          className="rounded-md border border-line bg-panel px-2.5 py-1 text-xs text-gray-300 placeholder:text-dim"
        />
        <select
          value={type}
          onChange={(e) => setType(e.target.value)}
          className="rounded-md border border-line bg-panel px-2 py-1 text-xs text-gray-300"
        >
          <option value="">Все типы</option>
          {Object.entries(SIGNAL_RU).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <select
          value={severity}
          onChange={(e) => setSeverity(e.target.value)}
          className="rounded-md border border-line bg-panel px-2 py-1 text-xs text-gray-300"
        >
          <option value="">Любая важность</option>
          {SEVERITIES.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
      </div>

      <div className="card">
        <SignalFeed signals={filtered} emptyText="Ничего не найдено" detailEnabled={signalDetail} />
      </div>
    </PageShell>
  );
}
