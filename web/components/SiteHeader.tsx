import Link from "next/link";
import { Search } from "lucide-react";
import type { ReactNode } from "react";
import TowerLogo from "./TowerLogo";

const NAV = [
  { href: "/", label: "карта" },
  { href: "/search", label: "поиск новостей", search: true },
  { href: "/stories", label: "сюжеты" },
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
    <header className="reveal sticky top-0 z-40 bg-bg/85 pb-2 pt-4 backdrop-blur-sm">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-2">
        <Link href="/" className="group flex min-h-11 items-end gap-2 sm:min-h-0">
          <TowerLogo />
          <span className="display text-[22px] leading-none tracking-tight">
            МАССАРАКШ
          </span>
          <span className="tnum text-[10px] uppercase tracking-[0.22em] text-dim">
            мир ↔ россия
          </span>
        </Link>
        <nav
          aria-label="Основная навигация"
          className="ml-auto flex flex-wrap items-center gap-x-4 gap-y-2 text-[12px]"
        >
          {NAV.map((n) => (
            <Link
              key={n.href}
              href={n.href}
              className={`inline-flex min-h-11 items-center gap-1 focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent sm:min-h-0 ${
                active === n.href
                  ? "text-ru-white underline decoration-ru-red decoration-2 underline-offset-4"
                  : "text-dim transition-colors hover:text-ru-white"
              }`}
            >
              {n.search && <Search aria-hidden="true" size={12} strokeWidth={1.8} />}
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
