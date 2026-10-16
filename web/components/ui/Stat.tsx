import type { ReactNode } from "react";
import Delta from "./Delta";

export default function Stat({ label, value, delta, hint }: {
  label: string;
  value: ReactNode;
  delta?: number | null;
  hint?: string;
}) {
  return <div>
    <div className="text-xs text-dim">{label}</div>
    <div className="mt-1 flex items-baseline gap-2"><span className="tnum text-h2">{value}</span>{delta != null && <Delta value={delta} />}</div>
    {hint && <p className="mt-1 text-xs text-dim">{hint}</p>}
  </div>;
}
