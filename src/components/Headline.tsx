"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { temperatureColor, COUNTRY_FLAGS, API_URL, type Country } from "@/lib/api";
import InfoPopover from "@/components/InfoPopover";
import { glossary } from "@/lib/glossary";

interface HeadlineData {
  headline: string | null;
  subline: string | null;
  country_code?: string;
  type?: string;
  generated?: boolean;
}

interface HeadlineProps {
  countries: Country[];
}

export default function Headline({ countries }: HeadlineProps) {
  const [data, setData] = useState<HeadlineData | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  useEffect(() => {
    fetch(`${API_URL}/api/v1/headline`)
      .then((r) => r.json())
      .then((d) => {
        if (d.headline) setData(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  if (loading || !data?.headline) return null;

  const country = countries.find((c) => c.code === data.country_code);
  const temp = country?.temperature ?? 0;
  const color = temperatureColor(temp);
  const flag = data.country_code ? COUNTRY_FLAGS[data.country_code] || "" : "";

  return (
    <div
      onClick={() => data.country_code && router.push(`/country/${data.country_code}#narratives`)}
      className="group relative overflow-hidden rounded-2xl border border-white/[0.06] cursor-pointer transition-all duration-500 hover:border-white/[0.12]"
      style={{
        background: `linear-gradient(135deg, ${color}10 0%, rgba(9,9,11,0.98) 50%, ${color}06 100%)`,
      }}
    >
      {/* Ambient glow */}
      <div
        className="absolute -top-20 -right-20 w-60 h-60 rounded-full blur-[100px] opacity-[0.15] transition-opacity duration-700 group-hover:opacity-25 pointer-events-none"
        style={{ backgroundColor: color }}
      />

      <div className="relative px-8 py-10 sm:px-12 sm:py-14">
        {/* Country tag */}
        {flag && (
          <div className="flex items-center gap-2 mb-5">
            <span className="text-2xl">{flag}</span>
            <span
              className="text-[11px] font-semibold tracking-[0.2em] uppercase"
              style={{ color: `${color}` }}
            >
              {data.type === "divergence"
                ? "Расхождение нарративов"
                : data.type === "anomaly"
                ? "Аномалия"
                : "Температура"}
            </span>
            <InfoPopover title="Заголовок дня">{glossary.headline.detail}</InfoPopover>
          </div>
        )}

        {/* Headline */}
        <h2 className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight leading-[1.1] max-w-3xl text-white">
          {data.headline}
        </h2>

        {/* Subline */}
        <p className="mt-5 text-base sm:text-lg text-white/45 max-w-2xl leading-relaxed">
          {data.subline}
        </p>

        {/* CTA hint */}
        <div className="mt-6 text-xs text-white/20 group-hover:text-white/40 transition-colors">
          Подробнее →
        </div>
      </div>
    </div>
  );
}
