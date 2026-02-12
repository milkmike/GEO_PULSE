"use client";

import { COUNTRY_CODES, COUNTRY_FLAGS, COUNTRY_NAMES } from "@/lib/constants";

interface CountryFilterProps {
  selected: string[];
  onToggle: (code: string) => void;
  className?: string;
}

export function CountryFilter({ selected, onToggle, className = "" }: CountryFilterProps) {
  return (
    <div className={`flex flex-wrap gap-1.5 ${className}`}>
      {COUNTRY_CODES.map((code) => {
        const isActive = selected.length === 0 || selected.includes(code);
        return (
          <button
            key={code}
            onClick={() => onToggle(code)}
            className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs transition-all
              ${
                selected.includes(code)
                  ? "border-blue-500/50 bg-blue-500/20 text-blue-300"
                  : selected.length === 0
                    ? "border-border/50 bg-white/5 text-foreground hover:bg-white/10"
                    : "border-transparent bg-white/5 text-muted-foreground hover:bg-white/10"
              }`}
          >
            <span>{COUNTRY_FLAGS[code]}</span>
            <span>{COUNTRY_NAMES[code]}</span>
          </button>
        );
      })}
      {selected.length > 0 && (
        <button
          onClick={() => selected.forEach(onToggle)}
          className="rounded-full border border-transparent px-2.5 py-1 text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          ✕ Сбросить
        </button>
      )}
    </div>
  );
}
