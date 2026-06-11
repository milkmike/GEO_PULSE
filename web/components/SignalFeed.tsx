"use client";

import { fmtDate, SIGNAL_RU } from "@/lib/format";
import type { Signal } from "@/lib/types";

const SEV_BORDER: Record<string, string> = {
  info: "border-l-line",
  warning: "border-l-cooling",
  critical: "border-l-hostile",
};

export default function SignalFeed({
  signals,
  emptyText = "Активных сигналов нет",
}: {
  signals: Signal[];
  emptyText?: string;
}) {
  if (!signals.length) {
    return <div className="px-4 py-3 text-xs text-dim">{emptyText}</div>;
  }
  return (
    <div className="space-y-1.5 px-3 pb-3">
      {signals.map((s) => (
        <div
          key={s.id}
          className={`rounded-r-md border-l-2 bg-panel2 px-3 py-2 ${SEV_BORDER[s.severity] ?? "border-l-line"}`}
        >
          <div className="text-[13px] font-semibold leading-snug">{s.title}</div>
          {s.description && (
            <div className="mt-0.5 text-xs leading-snug text-dim">{s.description}</div>
          )}
          <div className="mt-1 text-[11px] text-dim">
            {SIGNAL_RU[s.type] ?? s.type} · {fmtDate(s.created_at)}
            {s.active && <span className="ml-2 text-ally">● активен</span>}
          </div>
        </div>
      ))}
    </div>
  );
}
