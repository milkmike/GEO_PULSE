import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin", "cyrillic"],
});

export const metadata: Metadata = {
  title: "GeoPulse — Геополитическая температура",
  description: "Аналитический дашборд геополитической температуры стран СНГ",
};

const navItems = [
  { href: "/", label: "🌡️ Обзор" },
  { href: "/country", label: "🏳️ Страна" },
  { href: "/threads", label: "🧵 Сюжеты" },
  { href: "/analytics", label: "📊 Аналитика" },
  { href: "/sources", label: "📡 Источники" },
  { href: "/about", label: "ℹ️ О проекте" },
  { href: "/v2", label: "🚀 v2" },
];

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ru">
      <body className={`${inter.variable} font-sans antialiased`}>
        {/* Navigation */}
        <header className="sticky top-0 z-50 border-b border-border bg-[#0a0a0f]/80 backdrop-blur-md">
          <div className="mx-auto flex h-14 max-w-7xl items-center px-4">
            {/* Logo */}
            <Link href="/" className="mr-8 flex items-center gap-2">
              <span className="text-lg font-bold tracking-wider text-blue-500">GEO</span>
              <span className="text-lg font-bold tracking-wider text-white">PULSE</span>
            </Link>

            {/* Nav links */}
            <nav className="flex gap-1 overflow-x-auto">
              {navItems.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="whitespace-nowrap rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
                >
                  {item.label}
                </Link>
              ))}
            </nav>
          </div>
        </header>

        {/* Content */}
        <main className="mx-auto max-w-7xl px-4 py-6">
          {children}
        </main>
      </body>
    </html>
  );
}
