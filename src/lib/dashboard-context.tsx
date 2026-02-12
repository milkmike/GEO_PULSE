"use client";

import { createContext, useContext, useCallback, useMemo, type ReactNode } from "react";
import { useSearchParams, useRouter, usePathname } from "next/navigation";

// ── Types ──────────────────────────────────────────────

export interface DashboardFilters {
  /** Selected country codes (empty = all) */
  countries: string[];
  /** Time period in days */
  days: number;
  /** Thread status filter */
  status: string;
  /** Source tier filter */
  tier: string;
  /** Navigation breadcrumb trail */
  from: string;
}

interface DashboardContextValue {
  filters: DashboardFilters;
  /** Set one or more filters — merges with existing, updates URL */
  setFilters: (patch: Partial<DashboardFilters>) => void;
  /** Toggle a country in the selection */
  toggleCountry: (code: string) => void;
  /** Clear all filters to defaults */
  clearFilters: () => void;
  /** Navigate to another page preserving current filters in URL */
  navigateWithFilters: (path: string, extra?: Record<string, string>) => void;
  /** Build a URL with current filters + extra params */
  buildFilteredUrl: (path: string, extra?: Record<string, string>) => string;
}

const DEFAULTS: DashboardFilters = {
  countries: [],
  days: 30,
  status: "",
  tier: "",
  from: "",
};

// ── URL ↔ State sync ───────────────────────────────────

function parseFiltersFromUrl(params: URLSearchParams): DashboardFilters {
  const countries = params.get("country")?.split(",").filter(Boolean) || [];
  const days = parseInt(params.get("days") || "30", 10) || 30;
  const status = params.get("status") || "";
  const tier = params.get("tier") || "";
  const from = params.get("from") || "";
  return { countries, days, status, tier, from };
}

function filtersToParams(filters: DashboardFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (filters.countries.length > 0) p.set("country", filters.countries.join(","));
  if (filters.days !== 30) p.set("days", String(filters.days));
  if (filters.status) p.set("status", filters.status);
  if (filters.tier) p.set("tier", filters.tier);
  if (filters.from) p.set("from", filters.from);
  return p;
}

// ── Context ────────────────────────────────────────────

const DashboardContext = createContext<DashboardContextValue | null>(null);

export function DashboardProvider({ children }: { children: ReactNode }) {
  const searchParams = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();

  const filters = useMemo(() => parseFiltersFromUrl(searchParams), [searchParams]);

  const updateUrl = useCallback(
    (newFilters: DashboardFilters) => {
      const params = filtersToParams(newFilters);
      const qs = params.toString();
      const url = qs ? `${pathname}?${qs}` : pathname;
      router.replace(url, { scroll: false });
    },
    [pathname, router],
  );

  const setFilters = useCallback(
    (patch: Partial<DashboardFilters>) => {
      const merged = { ...filters, ...patch };
      updateUrl(merged);
    },
    [filters, updateUrl],
  );

  const toggleCountry = useCallback(
    (code: string) => {
      const current = filters.countries;
      const next = current.includes(code) ? current.filter((c) => c !== code) : [...current, code];
      setFilters({ countries: next });
    },
    [filters.countries, setFilters],
  );

  const clearFilters = useCallback(() => {
    updateUrl(DEFAULTS);
  }, [updateUrl]);

  const buildFilteredUrl = useCallback(
    (path: string, extra?: Record<string, string>) => {
      const params = filtersToParams({ ...filters, from: pathname });
      if (extra) Object.entries(extra).forEach(([k, v]) => params.set(k, v));
      const qs = params.toString();
      return qs ? `${path}?${qs}` : path;
    },
    [filters, pathname],
  );

  const navigateWithFilters = useCallback(
    (path: string, extra?: Record<string, string>) => {
      router.push(buildFilteredUrl(path, extra));
    },
    [router, buildFilteredUrl],
  );

  const value = useMemo<DashboardContextValue>(
    () => ({ filters, setFilters, toggleCountry, clearFilters, navigateWithFilters, buildFilteredUrl }),
    [filters, setFilters, toggleCountry, clearFilters, navigateWithFilters, buildFilteredUrl],
  );

  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>;
}

// ── Hook ───────────────────────────────────────────────

export function useDashboard(): DashboardContextValue {
  const ctx = useContext(DashboardContext);
  if (!ctx) throw new Error("useDashboard must be used within <DashboardProvider>");
  return ctx;
}
