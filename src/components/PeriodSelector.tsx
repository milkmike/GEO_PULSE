"use client";

import { PERIOD_DAYS } from "@/lib/api";

interface PeriodSelectorProps {
  selected: string;
  onSelect: (period: string) => void;
}

const periods = Object.keys(PERIOD_DAYS);

export default function PeriodSelector({ selected, onSelect }: PeriodSelectorProps) {
  return (
    <div className="flex gap-1">
      {periods.map((period) => (
        <button
          key={period}
          onClick={() => onSelect(period)}
          className={`rounded-full px-3 py-1 text-xs font-medium transition-all ${
            selected === period
              ? "bg-blue-500/20 text-blue-400 shadow-[0_0_12px_rgba(59,130,246,0.3)]"
              : "text-muted-foreground hover:bg-white/5 hover:text-foreground"
          }`}
        >
          {period}
        </button>
      ))}
    </div>
  );
}
