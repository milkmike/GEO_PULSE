"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const v2Nav = [
  { href: "/v2", label: "⚡ Пульт", exact: true },
  { href: "/v2/sources", label: "📡 Источники" },
  { href: "/v2/resonance", label: "🔥 Резонанс" },
];

export default function V2Layout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();

  return (
    <div>
      {/* V2 sub-navigation */}
      <div className="mb-6 flex items-center gap-2 border-b border-border pb-3">
        <span className="mr-2 rounded bg-blue-500/20 px-2 py-0.5 text-xs font-bold uppercase tracking-widest text-blue-400">
          v2
        </span>
        {v2Nav.map((item) => {
          const active = item.exact
            ? pathname === item.href
            : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`rounded-md px-3 py-1.5 text-sm transition-colors ${
                active
                  ? "bg-accent text-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
        <div className="ml-auto">
          <Link
            href="/"
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            ← назад к v1
          </Link>
        </div>
      </div>
      {children}
    </div>
  );
}
