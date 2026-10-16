import { fmt } from "@/lib/format";

export default function Delta({ value, period = "week" }: {
  value: number | null;
  period?: "week" | "day";
}) {
  if (value == null || !Number.isFinite(value) || Math.abs(value) < 1) return null;
  const label = period === "day" ? "за сутки" : "за неделю";
  return <span className={`relative tnum shrink-0 whitespace-nowrap text-xs ${value > 0 ? "text-partner" : "text-hostile"}`} title={`${label}: ${fmt(value)}`}>
    <span aria-hidden="true">{value > 0 ? "▲ " : "▼ "}</span>
    {fmt(value)}<span className="sr-only"> {label}</span>
  </span>;
}
