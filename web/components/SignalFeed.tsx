"use client";

import { motion, useReducedMotion } from "motion/react";
import { fmtDate, SIGNAL_RU } from "@/lib/format";
import type { Signal } from "@/lib/types";

const SEV_BORDER: Record<string, string> = {
  info: "border-l-line",
  warning: "border-l-cooling",
  critical: "border-l-hostile",
};

const containerVariants = {
  show: { transition: { staggerChildren: 0.035 } },
};
const itemVariants = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0 },
};

export default function SignalFeed({
  signals,
  emptyText = "Активных сигналов нет",
}: {
  signals: Signal[];
  emptyText?: string;
}) {
  const reduce = useReducedMotion();
  if (!signals.length) {
    return <div className="px-4 py-3 text-xs text-dim">{emptyText}</div>;
  }

  if (reduce) {
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

  return (
    <motion.div
      key={`${signals.length}-${signals[0]?.id ?? ""}`}
      className="space-y-1.5 px-3 pb-3"
      variants={containerVariants}
      initial="hidden"
      animate="show"
    >
      {signals.map((s) => (
        <motion.div
          key={s.id}
          className={`rounded-r-md border-l-2 bg-panel2 px-3 py-2 ${SEV_BORDER[s.severity] ?? "border-l-line"}`}
          variants={itemVariants}
          whileHover={{ x: 3 }}
          transition={{ duration: 0.15, ease: "easeOut" }}
        >
          <div className="text-[13px] font-semibold leading-snug">{s.title}</div>
          {s.description && (
            <div className="mt-0.5 text-xs leading-snug text-dim">{s.description}</div>
          )}
          <div className="mt-1 text-[11px] text-dim">
            {SIGNAL_RU[s.type] ?? s.type} · {fmtDate(s.created_at)}
            {s.active && <span className="ml-2 text-ally">● активен</span>}
          </div>
        </motion.div>
      ))}
    </motion.div>
  );
}
