import type { ReactNode } from "react";
import SiteHeader from "@/components/SiteHeader";

export default function PageShell({ width = "page", active, children }: {
  width?: "page" | "read";
  active?: string;
  children: ReactNode;
}) {
  return <>
    <SiteHeader active={active} />
    <main className={`mx-auto min-w-0 px-4 pb-16 ${width === "read" ? "max-w-[var(--container-read)]" : "max-w-[var(--container-page)]"}`}>
      {children}
    </main>
  </>;
}
