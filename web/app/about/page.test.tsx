import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TemperatureMethodology } from "@/lib/types";
import AboutPage from "./page";

const apiMocks = vi.hoisted(() => ({
  sources: vi.fn(),
  meta: vi.fn(),
  temperatureMethodology: vi.fn(),
}));
vi.mock("@/lib/api", () => ({ api: apiMocks }));
vi.mock("@/components/SiteHeader", () => ({ default: () => <nav>header</nav> }));

const methodology: TemperatureMethodology = {
  methodology_version: "dynamic-test",
  name: "Тестовая температура",
  plain_language: [{ id: "meaning", title: "Смысл", body: "Текст методики приходит с сервера." }],
  technical: {
    input_eligibility: {}, window_days: 3,
    time_decay: { kind: "none", tau_seconds: 0, formula: "1" },
    source_weights: { field: "tier", default: 1, cluster_order: "before" },
    event_type_weights: {}, action_level_weights: {},
    reprint_importance: { base: 1, formula: "1" },
    event_clustering: { key: "event", minimum_raw_key_length: 1, normalization_after_gate: "none" },
    cluster_diminishing: { base: 1, formula: "1", unclustered_weight: 1 },
    aggregation: { numerator: "n", denominator: "d", raw_sentiment: "n/d" },
    normalization: { factor: 1, formula: "raw", temperature_round_digits: 1, raw_sentiment_round_digits: 1, component_round_digits: 1 },
    trend: {}, anomaly: {}, upstream_analysis: {},
  },
  worked_example: { articles: [], weighted_numerator: 0, weighted_denominator: 0, temperature: 0 },
  limitations: ["Ограничение с сервера."],
};

describe("About temperature methodology", () => {
  beforeEach(() => {
    apiMocks.sources.mockReset().mockResolvedValue({ sources: [] });
    apiMocks.meta.mockReset().mockResolvedValue({ countries: [] });
    apiMocks.temperatureMethodology.mockReset().mockResolvedValue(methodology);
  });

  it("loads the public methodology contract with an abortable request", async () => {
    const { unmount } = render(<AboutPage />);

    expect(await screen.findByRole("heading", { name: "Тестовая температура" })).toBeVisible();
    expect(screen.getByText("Текст методики приходит с сервера.")).toBeVisible();
    expect(apiMocks.temperatureMethodology).toHaveBeenCalledOnce();
    const requestSignal = apiMocks.temperatureMethodology.mock.calls[0][0] as AbortSignal;
    expect(requestSignal).toBeInstanceOf(AbortSignal);
    expect(requestSignal.aborted).toBe(false);

    unmount();
    expect(requestSignal.aborted).toBe(true);
  });

  it("keeps the RRI methodology visible alongside temperature methodology", async () => {
    render(<AboutPage />);
    expect(screen.getByRole("heading", { name: /как работает «градусник отношений»/i })).toBeVisible();
    expect(await screen.findByRole("heading", { name: "Тестовая температура" })).toBeVisible();
  });
});
