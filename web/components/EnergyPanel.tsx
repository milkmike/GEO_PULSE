"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

type Energy = Awaited<ReturnType<typeof api.energy>>;

const GROUP_RU: Record<string, string> = {
  crude_oil: "Сырая нефть",
  oil: "Нефть",
  oil_products: "Нефтепродукты",
  natural_gas: "Трубопроводный газ",
  pipeline_gas: "Трубопроводный газ",
  gas: "Газ",
  lng: "СПГ",
  coal: "Уголь",
};

/** Compact EUR for cumulative sums (billions/millions). */
function eur(v: number): string {
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)} млрд €`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(0)} млн €`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(0)} тыс. €`;
  return `${Math.round(v)} €`;
}

export default function EnergyPanel({ code }: { code: string }) {
  const [e, setE] = useState<Energy | null>(null);

  useEffect(() => {
    api.energy(code).then(setE).catch(() => setE({ has_data: false }));
  }, [code]);

  if (!e) return null;

  return (
    <section className="card">
      <div className="card-title px-4 pb-1 pt-3">Импорт ископаемых из РФ</div>
      {!e.has_data ? (
        <div className="px-4 pb-3 text-xs text-dim">
          Поставок российских нефти, газа и угля не зафиксировано.
        </div>
      ) : (
        <div className="px-4 pb-3">
          <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
            <div>
              <div className="tnum text-2xl text-ru-white">{eur(e.total_eur ?? 0)}</div>
              <div className="text-[11px] uppercase tracking-wide text-dim">заплачено России с 2022</div>
            </div>
            {!!e.world_rank && (
              <div>
                <div className="tnum text-2xl" style={{ color: "var(--color-hostile)" }}>
                  #{e.world_rank}
                </div>
                <div className="text-[11px] uppercase tracking-wide text-dim">покупатель в мире</div>
              </div>
            )}
          </div>

          {!!e.commodities?.length && (() => {
            const items = e.commodities.filter((c) => c.value_eur > 0).slice(0, 5);
            const max = Math.max(...items.map((c) => c.value_eur), 1);
            return (
              <ul className="mt-3 space-y-1.5 text-[12px]">
                {items.map((c) => (
                  <li key={c.group} className="flex items-center gap-2">
                    <span className="w-28 shrink-0 truncate text-dim">
                      {GROUP_RU[c.group] ?? c.name}
                    </span>
                    <div className="relative h-2 flex-1 rounded bg-panel2">
                      <div
                        className="absolute left-0 top-0 h-full rounded"
                        style={{ width: `${(c.value_eur / max) * 100}%`, background: "var(--color-hostile)" }}
                      />
                    </div>
                    <span className="tnum w-16 text-right text-fg">{eur(c.value_eur)}</span>
                  </li>
                ))}
              </ul>
            );
          })()}

          {e.period_from && (
            <div className="mt-2 text-[11px] text-dim">
              накопительно с {e.period_from.slice(0, 4)} года · данные CREA
            </div>
          )}
        </div>
      )}
    </section>
  );
}
