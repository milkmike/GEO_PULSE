"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/** One site-wide caveat instead of a banner and per-block disclaimers. */
export default function SiteFooter() {
  const pathname = usePathname();
  // Embedded widgets live inside other sites and carry no site chrome.
  if (pathname?.startsWith("/embed")) return null;
  return (
    <footer className="mx-auto max-w-[var(--container-page)] px-4 pb-8">
      <p className="border-t border-line pt-4 text-xs leading-5 text-dim">
        Индекс считает алгоритм, сводки и брифинги пишет ИИ: воспринимайте оценки как ориентир
        и проверяйте по первоисточникам.{" "}
        <Link href="/about" className="text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-accent">
          Как считается индекс
        </Link>
      </p>
    </footer>
  );
}
