import Link from "next/link";
import type { ReactNode } from "react";

const NAV = [
  { href: "/", label: "карта" },
  { href: "/analytics", label: "аналитика" },
  { href: "/sources", label: "источники" },
  { href: "/signals", label: "сигналы" },
  { href: "/about", label: "о проекте" },
];

/** Editorial masthead: serif wordmark + tricolor thread + nav. */
export default function SiteHeader({
  active,
  right,
}: {
  active?: string;
  right?: ReactNode;
}) {
  return (
    <header className="reveal pt-4">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <Link href="/" className="flex items-baseline gap-2.5">
          <span className="live-dot relative top-[-2px]" aria-hidden="true" />
          <span className="display text-[22px] leading-none tracking-tight">
            МАССАРАКШ
          </span>
          <span className="tnum text-[10px] uppercase tracking-[0.22em] text-dim">
            мир ↔ россия
          </span>
        </Link>
        <nav className="ml-auto flex flex-wrap items-center gap-4 text-[12px]">
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              className={
                active === n.href
                  ? "text-ru-white underline decoration-ru-red decoration-2 underline-offset-4"
                  : "text-dim transition-colors hover:text-ru-white"
              }
            >
              {n.label}
            </Link>
          ))}
          {right}
        </nav>
      </div>
      <span className="tricolor tricolor-draw mt-3 opacity-80" aria-hidden="true" />
    </header>
  );
}
