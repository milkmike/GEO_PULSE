import type { Metadata } from "next";
import { Golos_Text, JetBrains_Mono, Piazzolla } from "next/font/google";
import "./globals.css";
import DisclaimerBanner from "@/components/DisclaimerBanner";
import Pixel from "@/components/Pixel";

const piazzolla = Piazzolla({
  subsets: ["cyrillic", "latin"],
  variable: "--font-piazzolla",
});

const golos = Golos_Text({
  subsets: ["cyrillic", "latin"],
  variable: "--font-golos",
});

const jbMono = JetBrains_Mono({
  subsets: ["cyrillic", "latin"],
  variable: "--font-jbmono",
});

export const metadata: Metadata = {
  title: "Массаракш — Мир ↔ Россия",
  description:
    "Мониторинг отношений 99 стран мира с Россией: индекс, сигналы медиаполя, AI-брифинги",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru" className={`${piazzolla.variable} ${golos.variable} ${jbMono.variable}`}>
      <body className="min-h-screen antialiased">
        <DisclaimerBanner />
        {children}
        <Pixel />
      </body>
    </html>
  );
}
