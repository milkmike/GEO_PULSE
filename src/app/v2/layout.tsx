"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import V2AuthGate, { useV2Auth } from "./auth";

const v2Nav = [
  { href: "/v2", label: "⚡ Пульт", exact: true },
  { href: "/v2/sources", label: "📡 Источники" },
  { href: "/v2/resonance", label: "🔥 Резонанс" },
];

function V2Inner({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { logout } = useV2Auth();

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
        <div className="ml-auto flex items-center gap-3">
          <Link
            href="/"
            className="text-xs text-muted-foreground hover:text-foreground"
          >
            ← назад к v1
          </Link>
          <button
            onClick={logout}
            className="text-xs text-muted-foreground hover:text-red-400"
            title="Выйти"
          >
            🔒
          </button>
        </div>
      </div>
      {children}
    </div>
  );
}

export default function V2Layout({ children }: { children: React.ReactNode }) {
  return (
    <V2AuthGate>
      <V2Inner>{children}</V2Inner>
    </V2AuthGate>
  );
}
