import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FeatureFlagsProvider } from "@/components/FeatureFlagsProvider";
import type { FeatureFlags } from "@/lib/features";
import type { RadarCoverage, RadarEvidencePage, RadarMethodology, RadarTimeline, RadarTrend } from "@/lib/types";
import RadarTrendPage from "./page";

const navigation = vi.hoisted(() => ({ query: "", replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  usePathname: () => "/radar/trend-1",
  useRouter: () => ({ replace: navigation.replace }),
  useSearchParams: () => new URLSearchParams(navigation.query),
}));
vi.mock("@/components/SiteHeader", () => ({ default: () => <nav>header</nav> }));
vi.mock("@/components/Plot", () => ({ default: () => <div data-testid="plot" /> }));

const apiMocks = vi.hoisted(() => ({
  radarTrend: vi.fn(), radarTimeline: vi.fn(), radarEvidence: vi.fn(), radarCoverage: vi.fn(), radarMethodology: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: apiMocks }));

const trend: RadarTrend = {
  public_id: "trend-1", scope: "meta", state: "confirmed", thesis: "Политика меняется волной", subject_key: "policy:energy",
  direction: "restrictive", confidence: 0.88, coverage_confidence: 0.48, velocity: 2.5,
  first_observed_at: "2026-07-14T00:00:00Z", detected_at: "2026-07-15T00:00:00Z", confirmed_at: "2026-07-16T00:00:00Z",
  t0_auto: "2026-07-14T00:00:00Z", t0_effective: "2026-07-13T00:00:00Z", country_code: null,
  country_waves: [
    { public_id: "wave-es", country_code: "ES", contour: "media", state: "confirmed", confidence: 0.9, coverage_confidence: 0.8, velocity: 2.2, first_observed_at: "2026-07-14T00:00:00Z", detected_at: "2026-07-15T00:00:00Z", confirmed_at: "2026-07-16T00:00:00Z", t0_auto: "2026-07-14T00:00:00Z", t0_effective: "2026-07-14T00:00:00Z" },
    { public_id: "wave-pt", country_code: "PT", contour: "action", state: "emerging", confidence: 0.75, coverage_confidence: 0.7, velocity: 1.3, first_observed_at: "2026-07-16T00:00:00Z", detected_at: "2026-07-17T00:00:00Z", confirmed_at: null, t0_auto: "2026-07-16T00:00:00Z", t0_effective: "2026-07-16T00:00:00Z" },
  ],
  contours: { media: { state: "confirmed", status: "divergent" }, action: { state: "emerging", status: "divergent" } },
  contradiction_marker: true,
  evidence_preview: { public_id: "preview", role: "trigger", title: "Стартовая публикация", url: "https://example.test/start" },
  why_included: "prioritized_by_state_and_velocity",
};
const timeline: RadarTimeline = { trend, items: [{ kind: "state", at: "2026-07-16T00:00:00Z", state: "confirmed", contour: "media", evidence: {} }] };
const evidence: RadarEvidencePage = { items: [
  { public_id: "safe", role: "trigger", contribution: 0.8, title: "Надёжное доказательство", url: "https://example.test/safe", evidence: {}, why_included: "trigger_evidence" },
  { public_id: "contra", role: "contradiction", contribution: -0.2, title: "Противоречащий материал", url: "javascript:alert(1)", evidence: {}, why_included: "contradiction_evidence" },
], limit: 25, next_cursor: null };
const coverage: RadarCoverage = { updated_at: "2026-07-18T00:00:00Z", coverage_source: "temporary trend-derived proxy; not collection-health snapshots", countries: [{ country_code: "ES", coverage_confidence: 0.48, state: "critical", blind_spots: ["regional_press"] }] };
const methodology: RadarMethodology = { detector_version: "radar-wave-1", updated_at: "2026-07-18T00:00:00Z", baseline: { window_days: 90, acceleration_days: 7 }, lifecycle_gates: { media: {}, action: {} }, t0_fields: ["t0_auto", "t0_effective"], confidence_factors: ["semantic_shift", "coverage_health"], coverage_hard_gate: "coverage_confidence <= 0.5 suppresses confirmation", action_independence: "media classification never confirms action", evidence_roles: ["trigger", "support", "context", "contradiction"], limitations: ["Coverage proxy"] };
const flags: FeatureFlags = { searchNavigation: false, storiesNavigation: false, investigation: false, signalDetail: false, earlyWarningRadar: true };

async function renderPage() {
  let result!: ReturnType<typeof render>;
  await act(async () => {
    result = render(<FeatureFlagsProvider flags={flags}><RadarTrendPage params={Promise.resolve({ id: "trend-1" })} /></FeatureFlagsProvider>);
  });
  return result;
}

describe("Radar investigation page", () => {
  beforeEach(() => {
    navigation.query = ""; navigation.replace.mockReset();
    apiMocks.radarTrend.mockReset().mockResolvedValue(trend);
    apiMocks.radarTimeline.mockReset().mockResolvedValue(timeline);
    apiMocks.radarEvidence.mockReset().mockResolvedValue(evidence);
    apiMocks.radarCoverage.mockReset().mockResolvedValue(coverage);
    apiMocks.radarMethodology.mockReset().mockResolvedValue(methodology);
  });

  it("renders only propagation questions and the fetched timeline in propagation view", async () => {
    await renderPage();
    const article = await screen.findByRole("article");
    const headings = within(article).getAllByRole("heading", { level: 2 }).map((heading) => heading.textContent);
    expect(headings).toEqual([
      "01 · Что меняется?", "02 · Где началось и куда распространяется?", "03 · Что произошло в медиаконтуре?",
      "04 · Что произошло в контуре действий?",
    ]);
    expect(screen.getByRole("list", { name: /хронология тренда/i })).toHaveTextContent(/confirmed.*media/i);
    expect(screen.queryByText("Надёжное доказательство")).not.toBeInTheDocument();
    expect(screen.queryByText(/критический пробел покрытия/i)).not.toBeInTheDocument();
  });

  it("separates contours, country waves and effective T0", async () => {
    await renderPage();
    expect((await screen.findAllByText("медиаконтур", { exact: false })).length).toBeGreaterThanOrEqual(2);
    expect(screen.getAllByText("контур действий", { exact: false }).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("ES", { exact: true })).toBeVisible();
    expect(screen.getByText("PT", { exact: true })).toBeVisible();
    expect(screen.getAllByText(/эффективный T0/i).length).toBeGreaterThanOrEqual(1);
  });

  it("makes evidence, coverage and method views materially distinct", async () => {
    navigation.query = "view=evidence";
    await renderPage();
    expect(await screen.findByText("Противоречащий материал")).toBeVisible();
    expect(screen.getByRole("link", { name: /Надёжное доказательство/i })).toHaveAttribute("href", "https://example.test/safe");
    expect(screen.queryByRole("link", { name: /Противоречащий материал/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/критический пробел покрытия/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/90 дней/i)).not.toBeInTheDocument();
  });

  it("shows coverage limitations only in coverage and baseline/T0 only in method", async () => {
    navigation.query = "view=coverage";
    const coverageView = await renderPage();
    expect(await screen.findByText(/критический пробел покрытия/i)).toBeVisible();
    expect(screen.getByText("Coverage proxy")).toBeVisible();
    expect(screen.queryByText(/T0 авто/i)).not.toBeInTheDocument();
    coverageView.unmount();

    navigation.query = "view=method";
    await renderPage();
    expect(await screen.findByText(/T0 авто/i)).toBeVisible();
    expect(screen.getByText(/90 дней/i)).toBeVisible();
    expect(screen.queryByText(/критический пробел покрытия/i)).not.toBeInTheDocument();
    expect(screen.queryByText("Надёжное доказательство")).not.toBeInTheDocument();
  });

  it("does not claim contradictions are globally absent before all evidence pages load", async () => {
    const firstPage = { items: [evidence.items[0]], limit: 1, next_cursor: "evidence-next" };
    const finalPage = { items: [evidence.items[1]], limit: 1, next_cursor: null };
    apiMocks.radarEvidence.mockReset().mockResolvedValueOnce(firstPage).mockResolvedValueOnce(finalPage);
    navigation.query = "view=evidence";
    const user = userEvent.setup();
    await renderPage();

    expect(await screen.findByText(/в загруженных доказательствах противоречий/i)).toBeVisible();
    expect(screen.queryByText(/сохранённых противоречий нет/i)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /загрузить ещё доказательства/i }));
    expect(await screen.findByText("Противоречащий материал")).toBeVisible();
    expect(apiMocks.radarEvidence).toHaveBeenLastCalledWith("trend-1", "evidence-next", 25, expect.any(AbortSignal));
  });

  it("keeps the selected view in the URL and supports keyboard tab navigation", async () => {
    await renderPage();
    const propagation = await screen.findByRole("tab", { name: /распространение/i });
    const evidenceTab = screen.getByRole("tab", { name: /доказательства/i });
    propagation.focus();
    fireEvent.keyDown(propagation, { key: "ArrowRight" });
    expect(evidenceTab).toHaveFocus();
    fireEvent.click(evidenceTab);
    expect(navigation.replace).toHaveBeenCalledWith("/radar/trend-1?view=evidence");

    fireEvent.click(propagation);
    expect(navigation.replace).toHaveBeenCalledWith("/radar/trend-1?view=propagation");
  });

  it("aborts every investigation request on unmount", async () => {
    const { unmount } = await renderPage();
    await waitFor(() => expect(apiMocks.radarMethodology).toHaveBeenCalledOnce());
    const signals = Object.values(apiMocks).map((mock) => mock.mock.calls[0].at(-1) as AbortSignal);
    unmount();
    expect(signals.every((signal) => signal.aborted)).toBe(true);
  });
});
