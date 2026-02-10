"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import AdminAuthGate, { useAdminAuth } from "./auth";

const adminNav = [
  { href: "/admin", label: "⚡ Пульт", exact: true },
  { href: "/admin/sources", label: "📡 Источники" },
  { href: "/admin/resonance", label: "🔥 Резонанс" },
  { href: "/admin/costs", label: "💰 API & Косты" },
];

function AdminInner({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { logout } = useAdminAuth();

  return (
    <div>
      {/* Admin sub-navigation */}
      <div className="mb-6 flex items-center gap-2 border-b border-border pb-3">
        <span className="mr-2 rounded bg-blue-500/20 px-2 py-0.5 text-xs font-bold uppercase tracking-widest text-blue-400">
          admin
        </span>
        {adminNav.map((item) => {
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
            ← на главную
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

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <AdminAuthGate>
      <AdminInner>{children}</AdminInner>
    </AdminAuthGate>
  );
}
