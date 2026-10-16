import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CountrySummary, Level, MapEntry } from "@/lib/types";
import HomePage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/",
  useSearchParams: () => new URLSearchParams(),
}));

const apiMocks = vi.hoisted(() => ({
  countries: vi.fn(), signals: vi.fn(), worldBrief: vi.fn(), health: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: apiMocks, apiBase: () => "http://api.test" }));
vi.mock("@/components/SiteHeader", () => ({ default: () => <nav>header</nav> }));
vi.mock("@/components/WorldMap", () => ({
  default: ({ entries, selectedCountry, onSelectCountry }: {
    entries: MapEntry[]; selectedCountry?: string | null; onSelectCountry?: unknown;
  }) => (
    <div data-testid="map">
      {entries.length} стран на карте · выделена: {selectedCountry ?? "нет"} · {onSelectCountry ? "выбор страны" : "переход в досье"}
    </div>
  ),
}));

function country(code: string, name: string, score: number, level: Level, delta7d: number | null = 0): CountrySummary {
  return {
    code, name, name_en: name, iso3: `${code}X`, flag: "", region: "europe", tier: 2, score,
    structural: null, media: null, level, delta_24h: 0, delta_7d: delta7d,
    article_count: null, gdelt_volume: null, gdelt_tone: null, updated_at: "2026-10-15T11:40:00Z",
  };
}

const COUNTRIES = [
  country("BY", "Беларусь", 78, "ally"),
  country("BF", "Буркина-Фасо", 41.4, "partner", 16.9),
  country("RS", "Сербия", -3.7, "neutral", -11.3),
  country("BA", "Босния и Герцеговина", -25, "cooling", -41.6),
  country("DE", "Германия", -68.3, "tension", 12.7),
  country("UA", "Украина", -90, "hostile"),
];

beforeEach(() => {
  vi.clearAllMocks();
  apiMocks.countries.mockResolvedValue({ countries: COUNTRIES, total: COUNTRIES.length });
  apiMocks.signals.mockResolvedValue({ signals: [], total: 0 });
  apiMocks.worldBrief.mockRejectedValue(new Error("404"));
  apiMocks.health.mockResolvedValue({ verdict: "HEALTHY", sources_total: 10, sources_ok: 10, coverage_pct: 100, gdelt: { status: "ok" } });
});

describe("home page", () => {
  it("opens on the whole map without anchoring to a country", async () => {
    render(<HomePage />);
    expect(await screen.findByText(/6 стран на карте/)).toBeVisible();
    expect(screen.getByTestId("map")).toHaveTextContent("выделена: нет · переход в досье");
    expect(screen.queryByText(/Сербия ↔ Россия/)).not.toBeInTheDocument();
    expect(screen.queryByText("Где требуется внимание")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "лаборатории" })).toHaveAttribute("href", "/lab");
  });

  it("places every country in its thermometer band and highlights it on the map on hover", async () => {
    render(<HomePage />);
    const thermometer = await screen.findByRole("region", { name: "Термометр отношений" });
    expect(await within(thermometer).findByText("Союзники · 1")).toBeVisible();
    for (const band of ["Партнёры · 1", "Нейтралитет · 1", "Охлаждение · 1", "Напряжённость · 1", "Враждебность · 1"]) {
      expect(within(thermometer).getByText(band)).toBeVisible();
    }
    const serbia = within(thermometer).getByRole("link", { name: /Сербия/ });
    expect(serbia).toHaveAttribute("href", "/country/RS");
    fireEvent.mouseEnter(serbia);
    expect(screen.getByTestId("map")).toHaveTextContent("выделена: RS");
    fireEvent.mouseLeave(serbia);
    expect(screen.getByTestId("map")).toHaveTextContent("выделена: нет");
  });

  it("puts weekly movers in «Горячее» without a second list of all countries", async () => {
    render(<HomePage />);
    const movers = await screen.findByRole("region", { name: "Сильнее всего за неделю" });
    const links = within(movers).getAllByRole("link").filter((link) => link.getAttribute("href")?.startsWith("/country/"));
    expect(links[0]).toHaveTextContent("Босния и Герцеговина");
    expect(links[0]).toHaveAttribute("href", "/country/BA");
    expect(screen.queryByRole("heading", { name: "Все страны" })).not.toBeInTheDocument();
  });

  it("explains a failed index load instead of showing an empty page", async () => {
    apiMocks.countries.mockRejectedValue(new Error("offline"));
    render(<HomePage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить индекс стран");
  });
});
