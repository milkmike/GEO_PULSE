"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Sanctions = Awaited<ReturnType<typeof api.sanctions>>;

export default function SanctionsPanel({ code }: { code: string }) {
  const [s, setS] = useState<Sanctions | null>(null);

  useEffect(() => {
    api.sanctions(code).then(setS).catch(() => setS({ has_data: false }));
  }, [code]);

  if (!s) return null;

  return (
    <section className="card">
      <div className="card-title px-4 pb-1 pt-3">Санкционное давление на РФ</div>
      {!s.has_data ? (
        <div className="px-4 pb-3 text-xs text-dim">
          Эта страна не ведёт собственных санкционных списков против России.
        </div>
      ) : (
        <div className="px-4 pb-3">
          <div className="flex gap-6">
            <div>
              <div className="tnum text-2xl text-ru-white">{s.target_count}</div>
              <div className="text-[11px] uppercase tracking-wide text-dim">целей под санкциями</div>
            </div>
            <div>
              <div className="tnum text-2xl text-ru-white">{s.lists_count}</div>
              <div className="text-[11px] uppercase tracking-wide text-dim">программ</div>
            </div>
            {!!s.delta && s.delta > 0 && (
              <div>
                <div className="tnum text-2xl" style={{ color: "var(--color-hostile)" }}>
                  +{s.delta}
                </div>
                <div className="text-[11px] uppercase tracking-wide text-dim">недавно добавлено</div>
              </div>
            )}
          </div>
          {!!s.programs?.length && (
            <ul className="mt-3 space-y-1 text-[12px]">
              {s.programs
                .slice()
                .sort((a, b) => b.targets - a.targets)
                .slice(0, 6)
                .map((p) => (
                  <li key={p.name} className="flex justify-between gap-3">
                    <span className="truncate text-fg">{p.title || p.name}</span>
                    <span className="tnum text-dim">{p.targets}</span>
                  </li>
                ))}
            </ul>
          )}
          {s.last_change && (
            <div className="mt-2 text-[11px] text-dim">обновление списков: {s.last_change.slice(0, 10)}</div>
          )}
        </div>
      )}
    </section>
  );
}
