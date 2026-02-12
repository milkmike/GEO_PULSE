"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { COUNTRY_FLAGS, COUNTRY_NAMES } from "@/lib/constants";

const ROUTE_NAMES: Record<string, string> = {
  "/": "Обзор",
  "/threads": "Сюжеты",
  "/vox": "VOX",
  "/analytics": "Аналитика",
  "/sources": "Источники",
  "/country": "Страна",
  "/admin": "Админка",
  "/about": "О проекте",
};

function routeLabel(path: string): string {
  // Exact match
  if (ROUTE_NAMES[path]) return ROUTE_NAMES[path];
  // /country/KZ → "🇰🇿 Казахстан"
  const countryMatch = path.match(/^\/country\/([A-Z]{2})$/);
  if (countryMatch) {
    const code = countryMatch[1];
    return `${COUNTRY_FLAGS[code] || ""} ${COUNTRY_NAMES[code] || code}`;
  }
  // /threads/123 → "Сюжет #123"
  const threadMatch = path.match(/^\/threads\/(\d+)$/);
  if (threadMatch) return `Сюжет #${threadMatch[1]}`;
  // fallback: parent route
  const parent = path.split("/").slice(0, -1).join("/") || "/";
  return ROUTE_NAMES[parent] || path;
}

export function Breadcrumbs() {
  const searchParams = useSearchParams();
  const from = searchParams.get("from");

  if (!from) return null;

  const crumbs = [
    { href: "/", label: "🌡️ Обзор" },
  ];

  if (from !== "/") {
    crumbs.push({ href: from, label: routeLabel(from) });
  }

  return (
    <nav className="flex items-center gap-1.5 text-xs text-muted-foreground mb-4">
      {crumbs.map((crumb, i) => (
        <span key={crumb.href} className="flex items-center gap-1.5">
          {i > 0 && <span className="text-muted-foreground/50">›</span>}
          <Link href={crumb.href} className="hover:text-foreground transition-colors">
            {crumb.label}
          </Link>
        </span>
      ))}
      <span className="text-muted-foreground/50">›</span>
      <span className="text-foreground">текущая страница</span>
    </nav>
  );
}
