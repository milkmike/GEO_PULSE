import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

  it("keeps retained trends visible when load-more fails and retries locally", async () => {
    const nextTrend = { ...baseTrend, public_id: "next-page", thesis: "Тренд со следующей страницы" };
    apiMocks.radar
      .mockResolvedValueOnce({ items: [baseTrend], limit: 25, next_cursor: "next" })
      .mockRejectedValueOnce(new Error("page unavailable"))
      .mockResolvedValueOnce({ items: [nextTrend], limit: 25, next_cursor: null });
    const user = userEvent.setup();

    renderPage();
    expect(await screen.findByText(baseTrend.thesis)).toBeVisible();
    await user.click(screen.getByRole("button", { name: /следующие тренды/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/следующую страницу трендов загрузить не удалось/i);
    expect(screen.getByText(baseTrend.thesis)).toBeVisible();
    expect(screen.queryByText(/не удалось загрузить радар/i)).not.toBeInTheDocument();

    await user.click(within(alert).getByRole("button", { name: /повторить/i }));
    expect(await screen.findByText(nextTrend.thesis)).toBeVisible();
    expect(screen.getByText(baseTrend.thesis)).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
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

  it("paginates compact relation placements until it finds enough priority trends", async () => {
    const ordinary = { ...baseTrend, public_id: "ordinary-page", state: "emerging" as const, velocity: 0.4, thesis: "Обычная волна" };
    apiMocks.radar
      .mockResolvedValueOnce({ items: [ordinary], limit: 12, next_cursor: "compact-next" })
      .mockResolvedValueOnce({ items: [baseTrend], limit: 12, next_cursor: null });

    render(<FeatureFlagsProvider flags={enabled}><EarlyWarningPanel filters={{ storyId: 42 }} limit={1} /></FeatureFlagsProvider>);

    expect(await screen.findByText(baseTrend.thesis)).toBeVisible();
    expect(apiMocks.radar).toHaveBeenCalledTimes(2);
    expect(apiMocks.radar.mock.calls[0][0]).toMatchObject({ storyId: 42, limit: 12 });
    expect(apiMocks.radar.mock.calls[0][1]).toBeNull();
    expect(apiMocks.radar.mock.calls[1][0]).toMatchObject({ storyId: 42, limit: 12 });
    expect(apiMocks.radar.mock.calls[1][1]).toBe("compact-next");
    expect(screen.getByRole("link", { name: /весь радар/i })).toHaveAttribute("href", "/radar?story_id=42");
  });

  it("distinguishes capped partial compact searches from exhausted searches", async () => {
    const ordinary = { ...baseTrend, public_id: "ordinary", state: "emerging" as const, velocity: 0.4, thesis: "Неприоритетная волна" };
    apiMocks.radar
      .mockResolvedValueOnce({ items: [ordinary], limit: 12, next_cursor: "page-2" })
      .mockResolvedValueOnce({ items: [{ ...ordinary, public_id: "ordinary-2" }], limit: 12, next_cursor: "page-3" })
      .mockResolvedValueOnce({ items: [{ ...ordinary, public_id: "ordinary-3" }], limit: 12, next_cursor: "page-4" });

    const partial = render(<FeatureFlagsProvider flags={enabled}><EarlyWarningPanel filters={{ signalId: 17 }} limit={1} /></FeatureFlagsProvider>);
    expect(await screen.findByText(/в проверенной части радара приоритетных трендов пока нет/i)).toBeVisible();
    expect(apiMocks.radar).toHaveBeenCalledTimes(3);
    for (const call of apiMocks.radar.mock.calls) expect(call[0]).toMatchObject({ signalId: 17, limit: 12 });
    expect(screen.getByRole("link", { name: /весь радар/i })).toHaveAttribute("href", "/radar?signal_id=17");
    partial.unmount();

    apiMocks.radar.mockReset().mockResolvedValueOnce({ items: [ordinary], limit: 12, next_cursor: null });
    render(<FeatureFlagsProvider flags={enabled}><EarlyWarningPanel filters={{ signalId: 17 }} limit={1} /></FeatureFlagsProvider>);
    expect(await screen.findByText(/среди всех сохранённых трендов приоритетных сейчас нет/i)).toBeVisible();
    expect(apiMocks.radar).toHaveBeenCalledOnce();
  });

  it("does not restart a compact relation request when an equivalent filter object rerenders", async () => {
    const pending = new Promise<never>(() => {});
    apiMocks.radar.mockReturnValue(pending);
    const { rerender } = render(<FeatureFlagsProvider flags={enabled}><EarlyWarningPanel filters={{ storyId: 42 }} /></FeatureFlagsProvider>);
    await waitFor(() => expect(apiMocks.radar).toHaveBeenCalledOnce());

    rerender(<FeatureFlagsProvider flags={enabled}><EarlyWarningPanel filters={{ storyId: 42 }} /></FeatureFlagsProvider>);
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(apiMocks.radar).toHaveBeenCalledOnce();
    expect((apiMocks.radar.mock.calls[0][2] as AbortSignal).aborted).toBe(false);
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
