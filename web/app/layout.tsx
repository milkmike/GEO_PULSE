import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "GEO PULSE — Мир ↔ Россия",
  description:
    "Мониторинг отношений 99 стран мира с Россией: индекс, сигналы медиаполя, AI-брифинги",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
