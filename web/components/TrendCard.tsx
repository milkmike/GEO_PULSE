import Link from "next/link";
import { ArrowUpRight, CircleAlert, RadioTower } from "lucide-react";
import type { RadarContour, RadarTrend, RadarTrendState } from "@/lib/types";
import { safeHttpUrl } from "@/lib/urls";

const STATE_LABEL: Record<RadarTrendState | "insufficient", string> = {
  candidate: "кандидат", emerging: "зарождается", confirmed: "подтверждён",
  cooling: "затухает", resolved: "завершён", rejected: "отклонён", insufficient: "недостаточно данных",
};

const STATE_COLOR: Record<RadarTrendState | "insufficient", string> = {
  candidate: "text-dim border-line", emerging: "text-cooling border-cooling/40",
  confirmed: "text-ru-red border-ru-red/50", cooling: "text-accent border-accent/40",
  resolved: "text-dim border-line", rejected: "text-dim border-line", insufficient: "text-dim border-line",
};

const CONTOUR_LABEL: Record<RadarContour, string> = { media: "медиа", action: "действия" };

function percent(value: number | null) {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

export default function TrendCard({ trend, compact = false }: { trend: RadarTrend; compact?: boolean }) {
  const source = safeHttpUrl(trend.evidence_preview?.url);
  const countries = [...new Set(trend.country_waves.map((wave) => wave.country_code))];
  return (
    <article className={`group border-l-2 px-4 py-4 ${trend.state === "confirmed" ? "border-l-ru-red" : "border-l-cooling"}`}>
      <div className="flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-[0.12em] text-dim">
        <span className={`rounded-full border px-2 py-0.5 ${STATE_COLOR[trend.state]}`}>{STATE_LABEL[trend.state]}</span>
        <span className="tnum">уверенность {percent(trend.confidence)}</span>
        <span className="tnum">скорость {trend.velocity == null ? "—" : trend.velocity.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}</span>
        {trend.contradiction_marker && <span className="inline-flex items-center gap-1 text-cooling"><CircleAlert size={11} aria-hidden="true" /> есть противоречия</span>}
      </div>
      <h3 className={`${compact ? "text-[16px]" : "text-[20px]"} display mt-2 leading-snug`}>
        <Link href={`/radar/${trend.public_id}`} className="rounded-sm transition-colors hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent">
          {trend.thesis}
        </Link>
      </h3>
      <p className="tnum mt-1 text-[10px] uppercase tracking-[0.1em] text-dim">
        {trend.direction} · {countries.length ? countries.join(" → ") : trend.country_code || "межстрановой тренд"}
      </p>
      <dl className="mt-3 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line text-[11px]">
        {(["media", "action"] as const).map((contour) => (
          <div key={contour} className="bg-panel2 px-3 py-2">
            <dt className="flex items-center gap-1 text-dim"><RadioTower size={11} aria-hidden="true" />{CONTOUR_LABEL[contour]}</dt>
            <dd className="mt-0.5 text-fg">{STATE_LABEL[trend.contours[contour].state]} · {trend.contours[contour].status}</dd>
          </div>
        ))}
      </dl>
      {!compact && trend.evidence_preview?.title && (
        <div className="mt-3 border-t border-dashed border-line pt-3 text-xs text-dim">
          <p>{trend.evidence_preview.title}</p>
          {source && <a href={source} target="_blank" rel="noopener noreferrer" className="mt-1 inline-flex min-h-11 items-center gap-1 text-accent hover:text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent sm:min-h-0">первоисточник <ArrowUpRight size={11} aria-hidden="true" /></a>}
        </div>
      )}
    </article>
  );
}
