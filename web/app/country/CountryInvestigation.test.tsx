import type { ReactNode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FeatureFlagsProvider } from "@/components/FeatureFlagsProvider";
import type { Dossier } from "@/lib/types";
import CountryPage from "./[code]/page";

const navigation = vi.hoisted(() => ({
  push: vi.fn(),
  replace: vi.fn(),
  query: "foo=bar",
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: navigation.push, replace: navigation.replace }),
  usePathname: () => "/country/es",
  useSearchParams: () => new URLSearchParams(navigation.query),
}));

const apiMocks = vi.hoisted(() => ({
  dossier: vi.fn(), topics: vi.fn(), headlines: vi.fn(), entities: vi.fn(), fx: vi.fn(),
  countryBrief: vi.fn(), unVotes: vi.fn(), trade: vi.fn(), agreements: vi.fn(),
  countryStories: vi.fn(), generateCountryBrief: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: apiMocks, apiBase: () => "http://api.test" }));

vi.mock("@/components/SiteHeader", () => ({ default: () => <nav>header</nav> }));
vi.mock("@/components/SortableGrid", () => ({
  default: ({ items }: { items: { id: string; node: ReactNode }[] }) => <>{items.map((item) => <div key={item.id}>{item.node}</div>)}</>,
}));
vi.mock("@/components/Plot", () => ({
  default: ({ data, onClick }: { data: Array<{ customdata?: string[] }>; onClick?: (point: { customdata?: string }) => void }) => {
    const marker = data.find((trace) => trace.customdata?.length)?.customdata?.[0];
    return marker ? <button type="button" onClick={() => onClick?.({ customdata: marker })}>plot marker</button> : null;
  },
}));
vi.mock("@/components/InvestigationPanel", () => ({
  default: ({ open, at }: { open: boolean; at: string | null }) => open ? <div data-testid="investigation-panel">{at}</div> : null,
}));
vi.mock("@/components/AgreementsPanel", () => ({ default: () => null }));
vi.mock("@/components/Markdown", () => ({ default: () => null }));
vi.mock("@/components/SignalFeed", () => ({ default: () => null }));
vi.mock("@/components/TradePanel", () => ({ default: () => null }));
vi.mock("@/components/UNVotesPanel", () => ({ default: () => null }));
vi.mock("@/components/DynamicsPanel", () => ({ default: () => null }));
vi.mock("@/components/TierDivergencePanel", () => ({ default: () => null }));
vi.mock("@/components/SanctionsPanel", () => ({ default: () => null }));
vi.mock("@/components/EnergyPanel", () => ({ default: () => null }));
vi.mock("@/components/VoxPanel", () => ({ default: () => null }));
vi.mock("@/components/StoriesPanel", () => ({ default: () => null }));

const dossier: Dossier = {
  country: {
    code: "ES", name: "Испания", name_en: "Spain", iso3: "ESP", flag: "🇪🇸", region: "europe",
    region_name: "Европа", tier: 1, memberships: ["EU"], unfriendly: true,
    sanctions_on_russia: true, war_with_russia: false, baseline_note: "",
  },
  index: {
    score: -2, level: "neutral", structural: -3, media: -1, boost: 0,
    delta_24h: 8, delta_7d: 7, details: {}, updated_at: "2026-07-15T20:00:00Z", version: "v1",
  },
  index_history: [
    { day: "2026-07-14", time: "2026-07-14T18:00:00Z", score: -10, structural: -8, media: -12, boost: 0, version: "v1", delta_24h: -1, aggregation: "daily_last" },
    { day: "2026-07-15", time: "2026-07-15T20:00:00Z", score: -2, structural: -3, media: -1, boost: 0, version: "v1", delta_24h: 8, aggregation: "daily_last" },
  ],
  temperature_history: [], gdelt: [], signals: [],
};

function renderCountry(investigation: boolean) {
  return act(async () => {
    render(
      <FeatureFlagsProvider flags={{ searchNavigation: false, storiesNavigation: false, investigation, signalDetail: false }}>
        <CountryPage params={Promise.resolve({ code: "es" })} />
      </FeatureFlagsProvider>,
    );
  });
}

describe("country investigation flow", () => {
  beforeEach(() => {
    navigation.query = "foo=bar";
    navigation.push.mockReset();
    navigation.replace.mockReset();
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
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

  it("uses the same exact timestamp for plot and textual markers while preserving query state", async () => {
    const user = userEvent.setup();
    await renderCountry(true);
    const marker = (await screen.findAllByRole("button", { name: /15 июл.*дневными точками RRI.*\+8,0/i }))[0];
    await user.click(marker);
    expect(navigation.push).toHaveBeenLastCalledWith(
      "/country/es?foo=bar&at=2026-07-15T20%3A00%3A00Z",
    );

    await user.click(screen.getByRole("button", { name: "plot marker" }));
    expect(navigation.push).toHaveBeenLastCalledWith(
      "/country/es?foo=bar&at=2026-07-15T20%3A00%3A00Z",
    );
  });

  it("opens a durable valid at timestamp and removes only an invalid at value", async () => {
    navigation.query = "foo=bar&at=2026-07-15T20%3A00%3A00Z";
    await renderCountry(true);
    expect(await screen.findByTestId("investigation-panel")).toHaveTextContent("2026-07-15T20:00:00Z");

    navigation.query = "foo=bar&at=2026-07-15T20%3A00%3A00";
    await renderCountry(true);
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/country/es?foo=bar"));
    expect(navigation.replace).toHaveBeenCalledTimes(1);
  });

  it("does not expose controls or react to at while the server flag is off", async () => {
    navigation.query = "foo=bar&at=2026-07-15T20%3A00%3A00Z";
    await renderCountry(false);
    await waitFor(() => expect(apiMocks.dossier).toHaveBeenCalled());
    expect(screen.queryByRole("list", { name: /заметные сдвиги/i })).not.toBeInTheDocument();
    expect(screen.queryByTestId("investigation-panel")).not.toBeInTheDocument();
    expect(navigation.push).not.toHaveBeenCalled();
    expect(navigation.replace).not.toHaveBeenCalled();
  });
});
