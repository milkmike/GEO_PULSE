"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import { useRouter } from "next/navigation";
import { temperatureColor, COUNTRY_FLAGS, getHeadline, type Country } from "@/lib/api";

interface Slide {
  headline: string;
  subline: string;
  country_code?: string;
  type?: string;
  emoji?: string;
}

interface HeadlineProps {
  countries: Country[];
}

export default function Headline({ countries }: HeadlineProps) {
  const [slides, setSlides] = useState<Slide[]>([]);
  const [current, setCurrent] = useState(0);
  const [direction, setDirection] = useState<"left" | "right">("right");
  const [isAnimating, setIsAnimating] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const timerRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined);
  const router = useRouter();

  useEffect(() => {
    getHeadline()
      .then((d: any) => {
        if (d.slides?.length) {
          setSlides(d.slides);
        } else if (d.headline) {
          setSlides([{ headline: d.headline, subline: d.subline, country_code: d.country_code, type: d.type }]);
        }
      })
      .catch(() => {});
  }, []);

  const goTo = useCallback(
    (idx: number, dir: "left" | "right") => {
      if (isAnimating || slides.length < 2) return;
      setIsAnimating(true);
      setDirection(dir);
      setCurrent(idx);
      setTimeout(() => setIsAnimating(false), 700);
    },
    [isAnimating, slides.length]
  );

  const next = useCallback(() => goTo((current + 1) % slides.length, "right"), [current, slides.length, goTo]);
  const prev = useCallback(() => goTo((current - 1 + slides.length) % slides.length, "left"), [current, slides.length, goTo]);

  // Auto-advance
  useEffect(() => {
    if (slides.length < 2 || isPaused) {
      clearInterval(timerRef.current);
      return;
    }
    timerRef.current = setInterval(next, 10_000);
    return () => clearInterval(timerRef.current);
  }, [slides.length, isPaused, next]);

  if (!slides.length) return null;

  const slide = slides[current] || slides[0];
  const country = countries.find((c) => c.code === slide.country_code);
  const temp = country?.temperature ?? 0;
  const color = temperatureColor(temp);
  const flag = slide.country_code ? COUNTRY_FLAGS[slide.country_code] || "" : "";

  const typeLabels: Record<string, string> = {
    anomaly: "Аномалия",
    hot: "Температура",
    cold: "Температура",
    swing: "Динамика",
    thread: "Главная тема",
    temperature: "Температура",
  };

  return (
    <div
      className="group relative overflow-hidden rounded-2xl border border-white/[0.06] select-none"
      style={{
        background: `linear-gradient(135deg, ${color}10 0%, rgba(9,9,11,0.98) 50%, ${color}06 100%)`,
      }}
      onMouseEnter={() => setIsPaused(true)}
      onMouseLeave={() => setIsPaused(false)}
    >
      {/* Ambient glow — animated on slide change */}
      <div
        className="absolute -top-20 -right-20 w-72 h-72 rounded-full blur-[120px] pointer-events-none transition-all duration-1000"
        style={{ backgroundColor: color, opacity: 0.18 }}
        key={`glow-${current}`}
      />
      <div
        className="absolute -bottom-16 -left-16 w-48 h-48 rounded-full blur-[100px] pointer-events-none transition-all duration-1000"
        style={{ backgroundColor: color, opacity: 0.08 }}
        key={`glow2-${current}`}
      />

      {/* Arrow buttons */}
      {slides.length > 1 && (
        <>
          <button
            onClick={(e) => { e.stopPropagation(); prev(); }}
            className="absolute left-3 top-1/2 -translate-y-1/2 z-20 w-10 h-10 rounded-full bg-white/[0.05] hover:bg-white/[0.12] border border-white/[0.08] hover:border-white/[0.2] backdrop-blur-sm flex items-center justify-center opacity-0 group-hover:opacity-100 transition-all duration-300 cursor-pointer"
            aria-label="Previous"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-white/60">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); next(); }}
            className="absolute right-3 top-1/2 -translate-y-1/2 z-20 w-10 h-10 rounded-full bg-white/[0.05] hover:bg-white/[0.12] border border-white/[0.08] hover:border-white/[0.2] backdrop-blur-sm flex items-center justify-center opacity-0 group-hover:opacity-100 transition-all duration-300 cursor-pointer"
            aria-label="Next"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-white/60">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </button>
        </>
      )}

      {/* Slide content with fade+slide animation */}
      <div
        onClick={() => slide.country_code && router.push(`/country/${slide.country_code}#narratives`)}
        className="relative px-8 py-10 sm:px-12 sm:py-14 cursor-pointer min-h-[220px] flex flex-col justify-center"
        key={`slide-${current}`}
        style={{
          animation: slides.length > 1 ? `slideIn${direction === "right" ? "Right" : "Left"} 0.6s cubic-bezier(0.16, 1, 0.3, 1) both` : undefined,
        }}
      >
        {/* Country tag + type badge */}
        <div className="flex items-center gap-3 mb-5">
          {flag && <span className="text-2xl">{flag}</span>}
          {slide.emoji && <span className="text-lg">{slide.emoji}</span>}
          <span
            className="text-[11px] font-semibold tracking-[0.2em] uppercase"
            style={{ color }}
          >
            {typeLabels[slide.type || "temperature"] || "Сигнал"}
          </span>
          {/* Slide counter */}
          {slides.length > 1 && (
            <span className="text-[10px] text-white/20 ml-auto tabular-nums">
              {current + 1} / {slides.length}
            </span>
          )}
        </div>

        {/* Headline */}
        <h2 className="text-3xl sm:text-4xl md:text-[2.75rem] font-bold tracking-tight leading-[1.1] max-w-3xl text-white">
          {slide.headline}
        </h2>

        {/* Subline */}
        <p className="mt-5 text-base sm:text-lg text-white/40 max-w-2xl leading-relaxed">
          {slide.subline}
        </p>

        {/* CTA hint */}
        <div className="mt-6 text-xs text-white/15 group-hover:text-white/35 transition-colors">
          Подробнее →
        </div>
      </div>

      {/* Dot indicators + progress bar */}
      {slides.length > 1 && (
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 flex items-center gap-2 z-10">
          {slides.map((_, i) => (
            <button
              key={i}
              onClick={(e) => {
                e.stopPropagation();
                goTo(i, i > current ? "right" : "left");
              }}
              className="relative w-8 h-1 rounded-full overflow-hidden cursor-pointer transition-all duration-300"
              style={{
                backgroundColor: i === current ? `${color}40` : "rgba(255,255,255,0.08)",
              }}
            >
              {i === current && !isPaused && (
                <span
                  className="absolute inset-0 rounded-full"
                  style={{
                    backgroundColor: color,
                    animation: "progressFill 10s linear both",
                  }}
                />
              )}
              {i === current && isPaused && (
                <span
                  className="absolute inset-0 rounded-full"
                  style={{ backgroundColor: color, opacity: 0.8 }}
                />
              )}
            </button>
          ))}
        </div>
      )}

      {/* CSS animations */}
      <style jsx>{`
        @keyframes slideInRight {
          0% { opacity: 0; transform: translateX(40px); }
          100% { opacity: 1; transform: translateX(0); }
        }
        @keyframes slideInLeft {
          0% { opacity: 0; transform: translateX(-40px); }
          100% { opacity: 1; transform: translateX(0); }
        }
        @keyframes progressFill {
          0% { width: 0%; }
          100% { width: 100%; }
        }
      `}</style>
    </div>
  );
}
