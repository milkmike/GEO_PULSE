"use client";

import { usePathname } from "next/navigation";
import { useEffect } from "react";
import { apiBase } from "@/lib/api";

/**
 * Self-hosted visitor pixel: requests /api/v1/px.gif on first load and on every
 * client-side route change, so the backend can count pageviews & daily uniques.
 * No cookies; the server hashes IP+UA with a daily salt.
 */
export default function Pixel() {
  const pathname = usePathname();
  useEffect(() => {
    try {
      const img = new Image();
      img.src = `${apiBase()}/api/v1/px.gif?p=${encodeURIComponent(
        pathname || "/"
      )}&t=${Date.now()}`;
    } catch {
      /* ignore */
    }
  }, [pathname]);
  return null;
}
