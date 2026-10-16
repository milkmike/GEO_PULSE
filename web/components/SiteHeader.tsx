"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { Search } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import TowerLogo from "./TowerLogo";
import HealthBadge from "./HealthBadge";
import { useFeatureFlags } from "./FeatureFlagsProvider";
import { api } from "@/lib/api";
import type { CountrySummary } from "@/lib/types";

const NAV = [
  { href: "/", label: "Обзор" },
  { href: "/countries", label: "Страны" },
  { href: "/stories", label: "Сюжеты", feature: "storiesNavigation" as const },
  { href: "/signals", label: "Изменения" },
  { href: "/about", label: "О данных" },
];

// Shared across route changes; unsuccessful requests can be retried on reopening.
let countryCache: Promise<CountrySummary[]> | undefined;
function searchCountries() {
  if (!countryCache) {
    countryCache = api.countries().then((data) => data.countries).catch((error) => {
      countryCache = undefined;
      throw error;
    });
  }
  return countryCache;
}

export default function SiteHeader({ active }: { active?: string }) {
  const flags = useFeatureFlags();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [mac, setMac] = useState(false);
  const [query, setQuery] = useState("");
  const [countries, setCountries] = useState<CountrySummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const [selected, setSelected] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const id = useId();
  const close = useCallback(() => setOpen(false), []);
  const show = useCallback(() => {
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    setQuery("");
    setSelected(0);
    setOpen(true);
  }, []);

  useEffect(() => { setMac(/Mac|iPhone|iPad/.test(navigator.platform)); }, []);
  useEffect(() => {
    if (!flags.searchNavigation) { setOpen(false); return; }
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (open) close(); else show();
      }
    };
    document.addEventListener("keydown", shortcut);
    return () => document.removeEventListener("keydown", shortcut);
  }, [flags.searchNavigation, open, show, close]);

  useEffect(() => {
    if (!open) return;
    let live = true;
    input.current?.focus();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    setLoading(true);
    setFailed(false);
    searchCountries().then((data) => { if (live) setCountries(data); })
      .catch(() => { if (live) setFailed(true); })
      .finally(() => { if (live) setLoading(false); });
    return () => {
      live = false;
      document.body.style.overflow = overflow;
      previousFocus.current?.focus();
    };
  }, [open]);

  const normalized = query.trim().toLocaleLowerCase("ru");
  const matches = normalized ? countries.filter((country) =>
    country.name.toLocaleLowerCase("ru").includes(normalized)
    || country.code.toLowerCase() === normalized).slice(0, 8) : [];
  const optionCount = normalized ? matches.length + 1 : 0;
  const selectedIndex = Math.min(selected, optionCount - 1);
  const navigate = (href: string) => { close(); router.push(href); };

  return <>
    <header className="sticky top-0 z-40 bg-bg/95 pt-3 backdrop-blur-sm">
      <div className="mx-auto grid max-w-[var(--container-page)] grid-cols-[1fr_auto] items-center gap-x-2 px-4 sm:grid-cols-[auto_1fr_auto]">
        <Link href="/" className="group flex min-h-11 items-end gap-2 py-2">
          <TowerLogo />
          <span className="display text-xl leading-none tracking-tight">МАССАРАКШ</span>
          <span className="hidden text-xs text-dim xl:inline">мир ↔ россия</span>
        </Link>
        <nav aria-label="Основная навигация" className="order-3 col-span-2 flex min-w-0 items-center gap-4 overflow-x-auto whitespace-nowrap text-sm sm:order-none sm:col-span-1 sm:justify-self-center">
          {NAV.filter((item) => !item.feature || flags[item.feature]).map((item) =>
            <Link key={item.href} href={item.href} aria-current={active === item.href ? "page" : undefined}
              className={`inline-flex min-h-11 shrink-0 items-center focus-visible:outline-2 focus-visible:outline-accent ${active === item.href ? "text-accent underline decoration-accent decoration-2 underline-offset-4" : "text-dim hover:text-fg"}`}>
              {item.label}
            </Link>)}
        </nav>
        <div className="flex items-center gap-2">
          {flags.searchNavigation && <button type="button" onClick={show} aria-haspopup="dialog" className="flex min-h-11 items-center gap-1.5 text-sm text-accent focus-visible:outline-2 focus-visible:outline-accent">
            <Search size={16} aria-hidden="true" /><span>Поиск <span className="hidden text-xs text-dim sm:inline">{mac ? "⌘K" : "Ctrl K"}</span></span>
          </button>}
          <HealthBadge />
        </div>
      </div>
      <span className="tricolor opacity-80" aria-hidden="true" />
    </header>
    {open && flags.searchNavigation && <div className="fixed inset-0 z-50 overflow-y-auto bg-black/70 px-4 py-16" onMouseDown={(event) => { if (event.target === event.currentTarget) close(); }}>
      <div ref={dialog} role="dialog" aria-modal="true" aria-labelledby={`${id}-title`} className="mx-auto max-w-[var(--container-read)] rounded-lg border border-line bg-panel p-4 shadow-xl"
        onKeyDown={(event) => {
          if (event.key === "Escape") { event.preventDefault(); close(); }
          if (event.key === "Tab") {
            const focusable = dialog.current?.querySelectorAll<HTMLElement>('input, button:not([tabindex="-1"]), a[href]');
            const first = focusable?.[0];
            const last = focusable?.[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
          }
        }}>
        <h2 id={`${id}-title`} className="mb-3 text-xl font-semibold">Поиск</h2>
        <form onSubmit={(event) => {
          event.preventDefault();
          if (!normalized) return;
          const country = matches[selectedIndex];
          navigate(country ? `/country/${country.code}` : `/search?q=${encodeURIComponent(query.trim())}`);
        }}>
          <label htmlFor={`${id}-input`} className="mb-2 block text-sm text-dim">Страна или запрос по публикациям</label>
          <input ref={input} id={`${id}-input`} role="combobox" autoComplete="off" aria-expanded={optionCount > 0} aria-autocomplete="list" aria-controls={`${id}-results`} aria-activedescendant={optionCount ? `${id}-${selectedIndex}` : undefined}
            value={query} onChange={(event) => { setQuery(event.target.value); setSelected(0); }}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown" && optionCount) { event.preventDefault(); setSelected((selectedIndex + 1) % optionCount); }
              if (event.key === "ArrowUp" && optionCount) { event.preventDefault(); setSelected((selectedIndex <= 0 ? optionCount : selectedIndex) - 1); }
            }}
            className="min-h-11 w-full rounded border border-line bg-bg px-3 text-base focus-visible:outline-2 focus-visible:outline-accent" />
          <ul id={`${id}-results`} role="listbox" aria-label="Результаты поиска" className="mt-3 divide-y divide-line">
            {matches.map((country, index) => <li key={country.code} role="none">
              <button type="button" role="option" id={`${id}-${index}`} aria-selected={index === selectedIndex} tabIndex={-1} onClick={() => navigate(`/country/${country.code}`)} className={`min-h-11 w-full px-3 text-left text-sm hover:bg-panel2 ${index === selectedIndex ? "bg-panel2" : ""}`}>{country.flag} {country.name}</button>
            </li>)}
            {normalized && <li role="none">
              <button type="button" role="option" id={`${id}-${matches.length}`} aria-selected={selectedIndex === matches.length} tabIndex={-1} onClick={() => navigate(`/search?q=${encodeURIComponent(query.trim())}`)} className={`min-h-11 w-full px-3 text-left text-sm hover:bg-panel2 ${selectedIndex === matches.length ? "bg-panel2" : ""}`}>Искать «{query.trim()}» в публикациях</button>
            </li>}
          </ul>
          <p role="status" className="my-3 text-sm text-dim">{loading ? "Загружаем страны…" : failed ? "Подсказки стран недоступны. Поиск по публикациям работает по Enter." : "Стрелками выберите страну или поиск в публикациях. Enter — открыть выбранное."}</p>
          <div className="flex justify-end gap-4">
            <button type="button" onClick={close} className="min-h-11 px-2 text-sm text-dim focus-visible:outline-2 focus-visible:outline-accent">Закрыть</button>
            <button type="submit" className="min-h-11 px-2 text-sm text-accent focus-visible:outline-2 focus-visible:outline-accent">Найти</button>
          </div>
        </form>
      </div>
    </div>}
  </>;
}
