import type { ReactNode } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeatureFlagsProvider } from "@/components/FeatureFlagsProvider";
import type { FeatureFlags } from "@/lib/features";
import type { Brief, Dossier, RadarTrend } from "@/lib/types";
import CountryPage from "./[code]/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/country/rs",
  useSearchParams: () => new URLSearchParams(),
}));

const apiMocks = vi.hoisted(() => ({
  dossier: vi.fn(), topics: vi.fn(), headlines: vi.fn(), entities: vi.fn(), fx: vi.fn(),
  countryBrief: vi.fn(), unVotes: vi.fn(), trade: vi.fn(), agreements: vi.fn(),
  countryStories: vi.fn(), generateCountryBrief: vi.fn(), countryRadar: vi.fn(),
  vox: vi.fn(), tierDivergence: vi.fn(), sanctions: vi.fn(), energy: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: apiMocks, apiBase: () => "http://api.test" }));

vi.mock("@/components/SiteHeader", () => ({ default: () => <nav>header</nav> }));
vi.mock("@/components/SortableGrid", () => ({
  default: ({ items }: { items: { id: string; node: ReactNode }[] }) => <>{items.map((item) => <div key={item.id}>{item.node}</div>)}</>,
}));
vi.mock("@/components/Markdown", () => ({ default: ({ text }: { text: string }) => <div>{text}</div> }));
vi.mock("@/components/EarlyWarningPanel", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/EarlyWarningPanel")>()),
  default: ({ trends, title }: { trends?: RadarTrend[]; title: string }) => (
    <section aria-label={title} data-trends={trends?.map((trend) => trend.public_id).join(",")} />
  ),
}));
vi.mock("@/components/Plot", () => ({ default: () => null }));
vi.mock("@/components/SignalFeed", () => ({ default: () => null }));
vi.mock("@/components/InvestigationPanel", () => ({ default: () => null }));
vi.mock("@/components/AgreementsPanel", () => ({ default: () => null }));
vi.mock("@/components/TradePanel", () => ({ default: () => null }));
vi.mock("@/components/UNVotesPanel", () => ({ default: () => null }));
vi.mock("@/components/DynamicsPanel", () => ({ default: () => null }));
vi.mock("@/components/StoriesPanel", () => ({ default: () => null }));

const flags: FeatureFlags = {
  searchNavigation: false, storiesNavigation: false, investigation: false,
  signalDetail: false, earlyWarningRadar: false,
};

const dossier: Dossier = {
  country: {
    code: "RS", name: "Сербия", name_en: "Serbia", iso3: "SRB", flag: "🇷🇸", region: "europe",
    region_name: "Европа", tier: 1, memberships: [], unfriendly: false,
    sanctions_on_russia: false, war_with_russia: false, baseline_note: "",
  },
  index: {
    score: 1, level: "neutral", structural: 19, media: -26, boost: 0,
    delta_24h: 0, delta_7d: 0, details: {}, updated_at: "2026-10-15T08:00:00Z", version: "v1",
  },
  index_history: [], temperature_history: [], gdelt: [], signals: [],
};

function brief(createdAt: string | undefined): Brief {
  return {
    content: "Сербия ↔ Россия: текущее состояние",
    model: "google/gemini-3-flash-preview",
    created_at: createdAt as string,
  };
}

function trend(publicId: string, state: RadarTrend["state"]): RadarTrend {
  return {
    public_id: publicId, scope: "country", state, thesis: "Тезис", subject_key: "policy:energy",
    direction: "restrictive", confidence: 0.4, coverage_confidence: 0.4, velocity: 0.5,
    first_observed_at: null, detected_at: null, confirmed_at: null, t0_auto: null, t0_effective: null,
    country_code: "RS", country_count: 1, wave_count: 0, country_waves: [],
    contours: {
      media: { state: "emerging", status: "aligned" },
      action: { state: "insufficient", status: "aligned" },
    },
    contradiction_marker: false, evidence_preview: null, why_included: "country_scope",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function renderCountry(overrides: Partial<FeatureFlags> = {}) {
  return act(async () => {
    render(
      <FeatureFlagsProvider flags={{ ...flags, ...overrides }}>
        <CountryPage params={Promise.resolve({ code: "rs" })} />
      </FeatureFlagsProvider>,
    );
  });
}

describe("country dossier hygiene", () => {
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-15T12:00:00Z"));
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    apiMocks.dossier.mockResolvedValue(dossier);
    apiMocks.topics.mockResolvedValue({ topics: [] });
    apiMocks.headlines.mockResolvedValue({ source: "indexed", headlines: [] });
    apiMocks.entities.mockResolvedValue({ entities: [] });
    apiMocks.fx.mockRejectedValue(new Error("empty"));
    apiMocks.countryBrief.mockRejectedValue(new Error("404"));
    apiMocks.unVotes.mockResolvedValue({ data: [] });
    apiMocks.trade.mockResolvedValue({ data: [] });
    apiMocks.agreements.mockResolvedValue({ agreements: [] });
    apiMocks.vox.mockResolvedValue({ timeline: [], emotions: {}, top_topics: [], recent_comments: [] });
    apiMocks.tierDivergence.mockResolvedValue({ tiers: [] });
    apiMocks.sanctions.mockResolvedValue({ has_data: false });
    apiMocks.energy.mockResolvedValue({ has_data: false });
  });

  afterEach(() => vi.useRealTimers());

  it("shows the shared low-data hint beside the index", async () => {
    apiMocks.dossier.mockResolvedValue({ ...dossier, index: { ...dossier.index!, low_data: true, media_confidence: .05 } });
    await renderCountry();
    expect(await screen.findByText("мало данных")).toHaveAttribute("title",
      "индекс держится в основном на позиции страны: о России там почти не пишут");
  });

  it("dates a fresh AI brief and keeps the refresh action", async () => {
    apiMocks.countryBrief.mockResolvedValue(brief("2026-10-12T09:00:00+00:00"));
    await renderCountry();

    expect(await screen.findByText("Сербия ↔ Россия: текущее состояние")).toBeVisible();
    expect(screen.getByText("AI-досье")).toBeVisible();
    expect(screen.getByText("Написано ИИ · 12 октября 2026")).toHaveAttribute(
      "title",
      "google/gemini-3-flash-preview",
    );
    expect(screen.getByRole("button", { name: "обновить" })).toBeVisible();
  });

  it.each([
    ["older than seven days", "2026-06-12T20:07:47.180204+00:00"],
    ["without a creation date", undefined],
  ])("hides the AI brief text when it is %s and offers a fresh one", async (_label, createdAt) => {
    apiMocks.countryBrief.mockResolvedValue(brief(createdAt));
    await renderCountry();

    expect(await screen.findByText("Последняя сводка старше недели, её цифры устарели.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Собрать заново" })).toBeVisible();
    expect(screen.queryByText(/текущее состояние/)).not.toBeInTheDocument();
  });

  it("shows the build action only after the cached brief turns out to be missing", async () => {
    const pending = deferred<Brief>();
    apiMocks.countryBrief.mockReturnValue(pending.promise);
    await renderCountry();
    expect(await screen.findByRole("heading", { name: /Сербия/ })).toBeVisible();
    expect(screen.queryByText("AI-досье")).not.toBeInTheDocument();

    await act(async () => pending.reject(new Error("404")));
    expect(await screen.findByText("Сводка по стране ещё не собрана.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Собрать AI-досье" })).toBeVisible();
  });

  it("labels the embed toggle in Russian and keeps the snippet behaviour", async () => {
    const user = userEvent.setup();
    await renderCountry();
    const toggle = await screen.findByRole("button", { name: "Встроить" });
    expect(screen.queryByRole("button", { name: /embed/i })).not.toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "false");

    await user.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Виджет для встраивания")).toBeVisible();
    expect(screen.getByText(/\/embed\/RS/)).toBeVisible();
  });

  it("hides pure no-data panels but keeps factual empty answers", async () => {
    await renderCountry();
    expect(await screen.findByText(/не ведёт собственных санкционных списков/i)).toBeVisible();
    expect(await screen.findByText(/поставок российских нефти, газа и угля не зафиксировано/i)).toBeVisible();
    await waitFor(() => {
      expect(apiMocks.vox).toHaveBeenCalledWith("RS");
      expect(apiMocks.tierDivergence).toHaveBeenCalledWith("RS");
    });

    expect(screen.queryByText(/народные настроения/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/нет данных по комментариям/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/расхождение по тирам/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/недостаточно размеченных статей/i)).not.toBeInTheDocument();
  });

  it("does not turn a failed sanctions or energy request into a factual claim", async () => {
    apiMocks.sanctions.mockRejectedValue(new Error("503"));
    apiMocks.energy.mockRejectedValue(new Error("503"));
    await renderCountry();
    await waitFor(() => {
      expect(apiMocks.sanctions).toHaveBeenCalled();
      expect(apiMocks.energy).toHaveBeenCalled();
    });

    expect(screen.queryByText(/санкционное давление/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/не ведёт собственных санкционных списков/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/импорт ископаемых/i)).not.toBeInTheDocument();
  });

  it("shows the country radar only when it holds priority trends", async () => {
    apiMocks.countryRadar.mockResolvedValue({
      items: [trend("quiet", "emerging")], limit: 12, next_cursor: null,
    });
    await renderCountry({ earlyWarningRadar: true });
    await waitFor(() => expect(apiMocks.countryRadar).toHaveBeenCalledWith(
      "RS", { limit: 12 }, null, expect.any(AbortSignal),
    ));
    expect(screen.queryByRole("region", { name: /радар · сербия/i })).not.toBeInTheDocument();
  });

  it("pages the country radar and hands its priority trends to the panel", async () => {
    apiMocks.countryRadar
      .mockResolvedValueOnce({ items: [trend("quiet", "emerging")], limit: 12, next_cursor: "page-2" })
      .mockResolvedValueOnce({ items: [trend("confirmed-1", "confirmed")], limit: 12, next_cursor: null });
    await renderCountry({ earlyWarningRadar: true });

    expect(await screen.findByRole("region", { name: "Радар · Сербия" })).toHaveAttribute(
      "data-trends",
      "confirmed-1",
    );
    expect(apiMocks.countryRadar).toHaveBeenLastCalledWith(
      "RS", { limit: 12 }, "page-2", expect.any(AbortSignal),
    );
  });
});
