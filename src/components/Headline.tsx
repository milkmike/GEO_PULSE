"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { temperatureColor, COUNTRY_FLAGS, type Country } from "@/lib/api";

const API = process.env.NEXT_PUBLIC_API_URL || "";

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
  const [visible, setVisible] = useState(true);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  // Check localStorage for dismiss state
  useEffect(() => {
    const dismissed = localStorage.getItem("geopulse:headline:dismissed");
    if (dismissed) {
      const ts = parseInt(dismissed, 10);
      // Auto-show again after 4 hours
      if (Date.now() - ts < 4 * 60 * 60 * 1000) {
        setVisible(false);
      } else {
        localStorage.removeItem("geopulse:headline:dismissed");
      }
    }
  }, []);

  useEffect(() => {
    if (!visible) return;
    fetch(`${API}/api/v1/headline`)
      .then((r) => r.json())
      .then((d) => {
        if (d.headline) setData(d);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [visible]);

  const dismiss = () => {
    setVisible(false);
    localStorage.setItem("geopulse:headline:dismissed", Date.now().toString());
  };

  if (!visible || loading || !data?.headline) return null;

  const country = countries.find((c) => c.code === data.country_code);
  const temp = country?.temperature ?? 0;
  const color = temperatureColor(temp);
  const flag = data.country_code ? COUNTRY_FLAGS[data.country_code] || "" : "";

  return (
    <div className="relative group">
      {/* Dismiss button */}
      <button
        onClick={dismiss}
        className="absolute top-3 right-3 z-10 text-white/20 hover:text-white/60 transition-colors text-sm"
        title="Скрыть на 4 часа"
      >
        ✕
      </button>

      {/* Clickable hero */}
      <div
        onClick={() => data.country_code && router.push(`/country/${data.country_code}`)}
        className="relative overflow-hidden rounded-2xl border border-white/[0.06] cursor-pointer transition-all duration-500 hover:border-white/[0.12]"
        style={{
          background: `linear-gradient(135deg, ${color}12 0%, rgba(9,9,11,0.98) 50%, ${color}08 100%)`,
        }}
      >
        {/* Ambient glow */}
        <div
          className="absolute -top-20 -right-20 w-60 h-60 rounded-full blur-[100px] opacity-20 transition-opacity duration-700 group-hover:opacity-30"
          style={{ backgroundColor: color }}
        />
        <div
          className="absolute -bottom-10 -left-10 w-40 h-40 rounded-full blur-[80px] opacity-10"
          style={{ backgroundColor: color }}
        />

        <div className="relative px-8 py-10 sm:px-12 sm:py-14">
          {/* Country tag */}
          {flag && (
            <div className="flex items-center gap-2 mb-4">
              <span className="text-2xl">{flag}</span>
              <span
                className="text-xs font-medium tracking-widest uppercase"
                style={{ color: `${color}99` }}
              >
                {data.type === "divergence"
                  ? "Расхождение нарративов"
                  : data.type === "anomaly"
                  ? "Аномалия"
                  : "Температура"}
              </span>
            </div>
          )}

          {/* Headline */}
          <h2
            className="text-3xl sm:text-4xl md:text-5xl font-bold tracking-tight leading-[1.1] max-w-3xl"
            style={{
              background: `linear-gradient(135deg, #ffffff 30%, ${color} 100%)`,
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
              backgroundClip: "text",
            }}
          >
            {data.headline}
          </h2>

          {/* Subline */}
          <p className="mt-4 text-base sm:text-lg text-white/50 max-w-2xl leading-relaxed">
            {data.subline}
          </p>

          {/* CTA hint */}
          <div className="mt-6 flex items-center gap-2 text-xs text-white/20 group-hover:text-white/40 transition-colors">
            <span>Подробнее →</span>
          </div>
        </div>
      </div>
    </div>
  );
}
