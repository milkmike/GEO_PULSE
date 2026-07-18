import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FeatureFlagsProvider } from "@/components/FeatureFlagsProvider";
import EarlyWarningPanel from "@/components/EarlyWarningPanel";
import type { FeatureFlags } from "@/lib/features";
import type { RadarTrend } from "@/lib/types";
import RadarPage from "./page";

const navigation = vi.hoisted(() => ({ query: "", replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/radar",
  useRouter: () => ({ replace: navigation.replace }),
  useSearchParams: () => new URLSearchParams(navigation.query),
}));

const apiMocks = vi.hoisted(() => ({ radar: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: apiMocks }));
vi.mock("@/components/SiteHeader", () => ({ default: () => <nav>header</nav> }));

const baseTrend: RadarTrend = {
  public_id: "e7313c19-8f24-4a06-938b-7d5f8ce741e2",
  scope: "meta",
  state: "confirmed",
  thesis: "Энергетическая политика меняется в регионе",
  subject_key: "policy:energy",
  direction: "restrictive",
  confidence: 0.88,
  coverage_confidence: 0.82,
  velocity: 2.5,
  first_observed_at: "2026-07-15T10:00:00Z",
  detected_at: "2026-07-16T10:00:00Z",
  confirmed_at: "2026-07-17T10:00:00Z",
  t0_auto: "2026-07-15T10:00:00Z",
  t0_effective: "2026-07-15T10:00:00Z",
  country_code: null,
  country_waves: [],
  contours: {
    media: { state: "confirmed", status: "aligned" },
    action: { state: "emerging", status: "aligned" },
  },
  contradiction_marker: false,
  evidence_preview: { public_id: "ev-1", role: "trigger", title: "Первое подтверждение", url: "https://example.test/evidence" },
  why_included: "prioritized_by_state_and_velocity",
};

const enabled: FeatureFlags = {
  searchNavigation: false,
  storiesNavigation: false,
  investigation: false,
  signalDetail: false,
  earlyWarningRadar: true,
};

function renderPage(flags: FeatureFlags = enabled) {
  return render(<FeatureFlagsProvider flags={flags}><RadarPage /></FeatureFlagsProvider>);
}

describe("Radar list page", () => {
  beforeEach(() => {
    navigation.query = "";
    navigation.replace.mockReset();
    apiMocks.radar.mockReset().mockResolvedValue({ items: [baseTrend], limit: 25, next_cursor: null });
  });

  it("fails closed and does not request radar data", () => {
    renderPage({ ...enabled, earlyWarningRadar: false });
    expect(screen.getByText(/раздел раннего предупреждения отключён/i)).toBeVisible();
    expect(apiMocks.radar).not.toHaveBeenCalled();
  });

  it("loads URL-backed filters and aborts its request on replacement", async () => {
    navigation.query = "state=confirmed&contour=media&country=es";
    const first = new Promise<never>(() => {});
    apiMocks.radar.mockReturnValueOnce(first).mockResolvedValueOnce({ items: [], limit: 25, next_cursor: null });
    const { rerender } = renderPage();
    await waitFor(() => expect(apiMocks.radar).toHaveBeenCalledOnce());
    const firstSignal = apiMocks.radar.mock.calls[0][2] as AbortSignal;
    expect(apiMocks.radar.mock.calls[0][0]).toMatchObject({ state: "confirmed", contour: "media", country: "ES" });

    navigation.query = "state=emerging&contour=action";
    rerender(<FeatureFlagsProvider flags={enabled}><RadarPage /></FeatureFlagsProvider>);
    await waitFor(() => expect(apiMocks.radar).toHaveBeenCalledTimes(2));
    expect(firstSignal.aborted).toBe(true);
    expect(apiMocks.radar.mock.calls[1][0]).toMatchObject({ state: "emerging", contour: "action" });
  });

  it("aborts a stale load-more request and never appends its old-filter page", async () => {
    let resolveOldPage!: (value: { items: RadarTrend[]; limit: number; next_cursor: null }) => void;
    const oldPage = new Promise<{ items: RadarTrend[]; limit: number; next_cursor: null }>((resolve) => { resolveOldPage = resolve; });
    const oldTrend = { ...baseTrend, public_id: "old-page", thesis: "Старая страница" };
    const freshTrend = { ...baseTrend, public_id: "fresh-page", thesis: "Новый фильтр" };
    apiMocks.radar
      .mockResolvedValueOnce({ items: [baseTrend], limit: 25, next_cursor: "next" })
      .mockReturnValueOnce(oldPage)
      .mockResolvedValueOnce({ items: [freshTrend], limit: 25, next_cursor: null });

    const user = userEvent.setup();
    const { rerender } = renderPage();
    await screen.findByText(baseTrend.thesis);
    await user.click(screen.getByRole("button", { name: /следующие тренды/i }));
    const loadMoreSignal = apiMocks.radar.mock.calls[1][2] as AbortSignal;

    navigation.query = "state=emerging";
    rerender(<FeatureFlagsProvider flags={enabled}><RadarPage /></FeatureFlagsProvider>);
    expect(await screen.findByText(freshTrend.thesis)).toBeVisible();
    expect(loadMoreSignal.aborted).toBe(true);

    resolveOldPage({ items: [oldTrend], limit: 25, next_cursor: null });
    await waitFor(() => expect(screen.queryByText(oldTrend.thesis)).not.toBeInTheDocument());
  });

  it("writes filter changes to a shareable URL and presents honest empty and error states", async () => {
    apiMocks.radar.mockResolvedValueOnce({ items: [], limit: 25, next_cursor: null });
    const user = userEvent.setup();
    const { unmount } = renderPage();
    expect(await screen.findByText(/по выбранным фильтрам трендов нет/i)).toBeVisible();
    await user.selectOptions(screen.getByLabelText(/состояние/i), "confirmed");
    expect(navigation.replace).toHaveBeenCalledWith("/radar?state=confirmed");
    unmount();

    apiMocks.radar.mockRejectedValueOnce(new Error("offline"));
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent(/не удалось загрузить радар/i);
  });

  it("keeps ordinary emerging trends off compact placements", () => {
    const ordinaryEmerging = { ...baseTrend, public_id: "ordinary", state: "emerging" as const, velocity: 0.8, thesis: "Обычный ранний сигнал" };
    const criticalEmerging = { ...baseTrend, public_id: "critical", state: "emerging" as const, velocity: 3.1, confidence: 0.9, thesis: "Исключительный ранний сигнал" };
    render(<EarlyWarningPanel trends={[ordinaryEmerging, criticalEmerging, baseTrend]} />);
    expect(screen.queryByText(ordinaryEmerging.thesis)).not.toBeInTheDocument();
    expect(screen.getByText(criticalEmerging.thesis)).toBeVisible();
    expect(screen.getByText(baseTrend.thesis)).toBeVisible();
  });

  it("supports arrow-key navigation across filter controls", async () => {
    renderPage();
    const state = screen.getByLabelText(/состояние/i);
    const contour = screen.getByLabelText(/контур/i);
    state.focus();
    fireEvent.keyDown(state, { key: "ArrowRight" });
    expect(contour).toHaveFocus();
  });

  it("drops unsafe evidence links", async () => {
    apiMocks.radar.mockResolvedValue({
      items: [{ ...baseTrend, evidence_preview: { ...baseTrend.evidence_preview!, url: "javascript:alert(1)" } }],
      limit: 25,
      next_cursor: null,
    });
    renderPage();
    expect(await screen.findByText("Первое подтверждение")).toBeVisible();
    expect(screen.queryByRole("link", { name: /первоисточник/i })).not.toBeInTheDocument();
  });
});
