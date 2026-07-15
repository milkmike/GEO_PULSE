import type { ReactNode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Dossier, StoryListItem } from "@/lib/types";
import HomePage from "./page";
import CountryPage from "./country/[code]/page";
import { FeatureFlagsProvider } from "@/components/FeatureFlagsProvider";

const apiMocks = vi.hoisted(() => ({
  countries: vi.fn(), signals: vi.fn(), worldBrief: vi.fn(), meta: vi.fn(),
  worldHeadlines: vi.fn(), topicCountries: vi.fn(), topicBrief: vi.fn(), stories: vi.fn(),
  dossier: vi.fn(), topics: vi.fn(), headlines: vi.fn(), entities: vi.fn(), fx: vi.fn(),
  countryBrief: vi.fn(), unVotes: vi.fn(), trade: vi.fn(), agreements: vi.fn(),
  countryStories: vi.fn(), generateCountryBrief: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMocks, apiBase: () => "http://api.test" }));
vi.mock("@/components/SiteHeader", () => ({ default: () => <nav>header</nav> }));
vi.mock("@/components/SortableGrid", () => ({
  default: ({ items }: { items: { id: string; node: ReactNode }[] }) => <>{items.map((item) => <div key={item.id}>{item.node}</div>)}</>,
}));

vi.mock("@/components/CountryRanking", () => ({ default: () => null }));
vi.mock("@/components/HealthBadge", () => ({ default: () => null }));
vi.mock("@/components/HeadlinesFeed", () => ({ default: () => null }));
vi.mock("@/components/Markdown", () => ({ default: () => null }));
vi.mock("@/components/RadarPanel", () => ({ default: () => null }));
vi.mock("@/components/SignalFeed", () => ({ default: () => null }));
vi.mock("@/components/WorldMap", () => ({ default: () => null }));
vi.mock("@/components/AgreementsPanel", () => ({ default: () => null }));
vi.mock("@/components/Plot", () => ({ default: () => null }));
vi.mock("@/components/TradePanel", () => ({ default: () => null }));
vi.mock("@/components/UNVotesPanel", () => ({ default: () => null }));
vi.mock("@/components/SparklineStrip", () => ({ default: () => null }));
vi.mock("@/components/DynamicsPanel", () => ({ default: () => null }));
vi.mock("@/components/TierDivergencePanel", () => ({ default: () => null }));
vi.mock("@/components/SanctionsPanel", () => ({ default: () => null }));
vi.mock("@/components/EnergyPanel", () => ({ default: () => null }));
vi.mock("@/components/VoxPanel", () => ({ default: () => null }));
vi.mock("@/components/Filters", () => ({ default: () => null }));

function story(id: number): StoryListItem {
  return {
    id, slug: `story-${id}`, title_ru: `Сюжет ${id}`, title_en: null, summary: null,
    lifecycle: "developing", first_seen: "2026-07-13T09:00:00Z", last_seen: "2026-07-15T12:00:00Z",
    article_count: 8, source_count: 4, country_count: 2, highest_action_level: 3,
    clustering_confidence: 0.84, generated_at: null, countries: ["ES", "RU"], primary_url: null,
    why_included: [], relevance_score: 0.8, confidence: 0.84, evidence: {}, linked_signal_count: 0,
    linked_signals: [], latest_rri_shift: null,
  };
}

const dossier: Dossier = {
  country: {
    code: "ES", name: "Испания", name_en: "Spain", iso3: "ESP", flag: "🇪🇸", region: "europe",
    region_name: "Европа", tier: 1, memberships: ["EU"], unfriendly: true,
    sanctions_on_russia: true, war_with_russia: false, baseline_note: "",
  },
  index: {
    score: -12, level: "cooling", structural: -10, media: -14, boost: 0,
    delta_24h: -2, delta_7d: -4, details: {}, updated_at: "2026-07-15T12:00:00Z", version: "v1",
  },
  index_history: [], temperature_history: [], gdelt: [], signals: [],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

describe("story placements", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    apiMocks.countries.mockResolvedValue({ countries: [], total: 0 });
    apiMocks.signals.mockResolvedValue({ signals: [] });
    apiMocks.worldBrief.mockRejectedValue(new Error("empty"));
    apiMocks.meta.mockResolvedValue({ countries: [], topics: {}, regions: {}, levels: [] });
    apiMocks.worldHeadlines.mockResolvedValue({ headlines: [], total: 0 });
    apiMocks.dossier.mockResolvedValue(dossier);
    apiMocks.topics.mockResolvedValue({ topics: [] });
    apiMocks.headlines.mockResolvedValue({ source: "indexed", headlines: [] });
    apiMocks.entities.mockResolvedValue({ entities: [] });
    apiMocks.fx.mockRejectedValue(new Error("empty"));
    apiMocks.countryBrief.mockRejectedValue(new Error("empty"));
    apiMocks.unVotes.mockResolvedValue({ data: [] });
    apiMocks.trade.mockResolvedValue({ data: [] });
    apiMocks.agreements.mockResolvedValue({ agreements: [] });
  });

  afterEach(() => vi.useRealTimers());

  it("shows exactly six latest stories on the home dashboard", async () => {
    apiMocks.stories.mockResolvedValue({ stories: Array.from({ length: 8 }, (_, index) => story(index + 1)), next_cursor: null });
    render(
      <FeatureFlagsProvider flags={{ searchNavigation: false, storiesNavigation: true, investigation: false, signalDetail: false }}>
        <HomePage />
      </FeatureFlagsProvider>,
    );

    expect(await screen.findByRole("region", { name: /главные межстрановые сюжеты/i })).toBeVisible();
    expect(screen.getByRole("link", { name: "Сюжет 6" })).toHaveAttribute("href", "/stories/6");
    expect(screen.queryByRole("link", { name: "Сюжет 7" })).not.toBeInTheDocument();
    expect(apiMocks.stories).toHaveBeenCalledWith({ limit: 6 });
  });

  it("does not query or expose story panels while the server snapshot is off", async () => {
    render(
      <FeatureFlagsProvider flags={{ searchNavigation: false, storiesNavigation: false, investigation: false, signalDetail: false }}>
        <HomePage />
      </FeatureFlagsProvider>,
    );

    await waitFor(() => expect(apiMocks.countries).toHaveBeenCalled());
    expect(apiMocks.stories).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: /главные межстрановые сюжеты/i })).not.toBeInTheDocument();
  });

  it("keeps an explicit story panel on a country page even when it is empty", async () => {
    apiMocks.countryStories.mockResolvedValue({ country: "ES", name: "Испания", stories: [], next_cursor: null });
    await act(async () => {
      render(
        <FeatureFlagsProvider flags={{ searchNavigation: false, storiesNavigation: true, investigation: false, signalDetail: false }}>
          <CountryPage params={Promise.resolve({ code: "es" })} />
        </FeatureFlagsProvider>,
      );
    });

    expect(await screen.findByRole("region", { name: /сюжеты с участием страны: Испания/i })).toBeVisible();
    expect(screen.getByText(/пока нет межстрановых сюжетов/i)).toBeVisible();
    await waitFor(() => expect(apiMocks.countryStories).toHaveBeenCalledWith(
      "ES",
      { limit: 6 },
      null,
      expect.any(AbortSignal),
    ));
  });

  it("aborts and ignores an ES story response after navigation to FR", async () => {
    const es = deferred<{ country: string; name: string; stories: StoryListItem[]; next_cursor: null }>();
    const fr = deferred<{ country: string; name: string; stories: StoryListItem[]; next_cursor: null }>();
    apiMocks.countryStories.mockImplementation((code: string) => (
      code === "ES" ? es.promise : fr.promise
    ));
    const flags = { searchNavigation: false, storiesNavigation: true, investigation: false, signalDetail: false };
    let view!: ReturnType<typeof render>;
    await act(async () => {
      view = render(
        <FeatureFlagsProvider flags={flags}>
          <CountryPage params={Promise.resolve({ code: "es" })} />
        </FeatureFlagsProvider>,
      );
    });
    await waitFor(() => expect(apiMocks.countryStories).toHaveBeenCalledTimes(1));
    const esSignal = apiMocks.countryStories.mock.calls[0][3] as AbortSignal;

    await act(async () => {
      view.rerender(
        <FeatureFlagsProvider flags={flags}>
          <CountryPage params={Promise.resolve({ code: "fr" })} />
        </FeatureFlagsProvider>,
      );
    });
    await waitFor(() => expect(apiMocks.countryStories).toHaveBeenCalledTimes(2));
    expect(esSignal.aborted).toBe(true);

    await act(async () => fr.resolve({ country: "FR", name: "Франция", stories: [story(50)], next_cursor: null }));
    expect(await screen.findByRole("link", { name: "Сюжет 50" })).toBeVisible();
    await act(async () => es.resolve({ country: "ES", name: "Испания", stories: [story(42)], next_cursor: null }));
    expect(screen.queryByRole("link", { name: "Сюжет 42" })).not.toBeInTheDocument();
  });
});
