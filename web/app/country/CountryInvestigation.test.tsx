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
const panelState = vi.hoisted(() => ({ last: null as null | {
  open: boolean;
  fromTime: string | null;
  toTime: string | null;
  triggerRef: { current: HTMLElement | null };
  fallbackFocusRef: { current: HTMLElement | null };
} }));
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
  default: (props: {
    open: boolean;
    fromTime: string | null;
    toTime: string | null;
    triggerRef: { current: HTMLElement | null };
    fallbackFocusRef: { current: HTMLElement | null };
  }) => {
    panelState.last = props;
    return props.open ? <div data-testid="investigation-panel">{props.fromTime} → {props.toTime}</div> : null;
  },
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

const shortIntervalDossier: Dossier = {
  ...dossier,
  index_history: [
    { ...dossier.index_history[0], day: "2026-07-13", time: "2026-07-13T18:00:00Z", score: -18 },
    { ...dossier.index_history[0], day: "2026-07-14", time: "2026-07-14T22:30:00Z", score: -10 },
    { ...dossier.index_history[1], day: "2026-07-15", time: "2026-07-15T20:00:00Z", score: -2 },
  ],
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
    panelState.last = null;
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
    expect(panelState.last?.triggerRef.current).toBe(marker);
    expect(navigation.push).toHaveBeenLastCalledWith(
      "/country/es?foo=bar&at=2026-07-15T20%3A00%3A00Z",
    );

    await user.click(screen.getByRole("button", { name: "plot marker" }));
    expect(panelState.last?.triggerRef.current).toBeNull();
    expect(navigation.push).toHaveBeenLastCalledWith(
      "/country/es?foo=bar&at=2026-07-15T20%3A00%3A00Z",
    );

    navigation.query = "foo=bar&at=2026-07-15T20%3A00%3A00Z";
    await renderCountry(true);
    expect(panelState.last).toMatchObject({
      open: true,
      fromTime: "2026-07-14T18:00:00Z",
      toTime: "2026-07-15T20:00:00Z",
    });
  });

  it("restores the exact adjacent points for a durable shift shorter than 24 hours", async () => {
    apiMocks.dossier.mockResolvedValue(shortIntervalDossier);
    navigation.query = "foo=bar&at=2026-07-15T20%3A00%3A00Z";
    await renderCountry(true);
    expect(await screen.findByTestId("investigation-panel")).toHaveTextContent(
      "2026-07-14T22:30:00Z → 2026-07-15T20:00:00Z",
    );
    expect(panelState.last).toMatchObject({
      fromTime: "2026-07-14T22:30:00Z",
      toTime: "2026-07-15T20:00:00Z",
    });
    expect(panelState.last?.triggerRef.current).toBeNull();
    expect(panelState.last?.fallbackFocusRef.current).toHaveTextContent(/индекс и термометр/i);
  });

  it.each([
    "2026-07-15T20:00:00",
    "2026-07-14T18:00:00Z",
  ])("removes an invalid or non-marker at value while preserving other query state: %s", async (at) => {
    navigation.query = `foo=bar&at=${encodeURIComponent(at)}`;
    await renderCountry(true);
    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith("/country/es?foo=bar"));
    expect(navigation.replace).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("investigation-panel")).not.toBeInTheDocument();
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
