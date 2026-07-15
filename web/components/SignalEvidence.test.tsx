import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SignalDetail } from "@/lib/types";
import SignalEvidence from "./SignalEvidence";

vi.mock("@/components/Plot", () => ({ default: () => <div data-testid="signal-chart">chart</div> }));

const complete: SignalDetail = {
  id: 17,
  type: "index_shift",
  severity: "warning",
  summary: {
    headline: "Индекс Испании изменился на 8 пунктов",
    description: "Сохранённый сдвиг RRI.",
    what_changed: "RRI: −10 → −2 за 24 часа",
  },
  rule: {
    detector: "index_shift",
    version: "2.1",
    description: "Срабатывает при заметном изменении индекса.",
    threshold: { delta: 6, enabled: false },
    current_rule_reference: { delta: 7 },
  },
  values: {
    observed: { delta: 8, article_count: 0 },
    baseline: { delta: 1 },
    window: {
      start: "2026-07-14T20:00:00Z",
      end: "2026-07-15T20:00:00Z",
      basis: "persisted_trigger_window",
      status: "exact",
    },
  },
  chart_points: [
    { time: "2026-07-14T20:00:00Z", score: -10 },
    { time: "2026-07-15T20:00:00Z", score: -2 },
    { time: "bad", score: "no" },
  ],
  articles: [{
    id: 1, title: "El País: переговоры", url: "https://elpais.com/mundo/talks",
    published_at: "2026-07-15T19:00:00Z", source_name: "El País", country_code: "ES",
    sentiment: -0.5, action_level: 3, event_key: "talks",
  }],
  articles_page: { total: 1, returned: 1, limit: 100, truncated: false, has_more: false },
  related_story: {
    id: 42, slug: "talks", title: "Переговоры в Испании", summary: "Межстрановой сюжет.",
    lifecycle: "developing", last_seen: "2026-07-15T20:00:00Z", confidence: 0.84,
  },
  countries: [{ code: "ES", name: "Испания", article_count: 3, media_tone: -1.2 }],
  state: { created_at: "2026-07-15T20:00:00Z", expires_at: "2026-07-20T20:00:00Z", active: true, status: "active" },
  confidence: 0.82,
  evidence_completeness: "complete",
  evidence_ids: ["signal:17", "article:1"],
  evidence: { article_ids: [1], story_ids: [42], rri_points: [] },
  evidence_truncation: {
    evidence_ids: { total: 2, returned: 2, truncated: false },
    article_ids: { total: 1, returned: 1, truncated: false },
    story_ids: { total: 1, returned: 1, truncated: false },
    rri_points: { total: 2, returned: 2, truncated: false },
    countries: { total: 1, returned: 1, truncated: false },
  },
  limitations: ["contextual_proximity_is_not_causation"],
};

describe("SignalEvidence", () => {
  it("renders concrete rule, values, exact window, chart and primary evidence", () => {
    render(<SignalEvidence detail={complete} storiesEnabled />);

    expect(screen.getByRole("heading", { name: complete.summary.headline })).toBeVisible();
    expect(screen.getByText(/RRI: −10 → −2/i)).toBeVisible();
    expect(screen.getByText(/уверенность 82%/i)).toBeVisible();
    expect(screen.getByText(/активен/i)).toBeVisible();
    expect(screen.getByTestId("signal-chart")).toBeVisible();

    const observed = screen.getByRole("region", { name: /наблюдаемое значение/i });
    expect(within(observed).getByText("delta").closest("li")).toHaveTextContent("8");
    expect(within(observed).getByText("article count").closest("li")).toHaveTextContent("0");
    const threshold = screen.getByRole("region", { name: /порог срабатывания/i });
    expect(within(threshold).getByText("enabled").closest("li")).toHaveTextContent("нет");
    expect(screen.getByText(/текущее правило, не исторический порог/i)).toBeVisible();

    const article = screen.getByRole("link", { name: /El País: переговоры/i });
    expect(article).toHaveAttribute("href", "https://elpais.com/mundo/talks");
    expect(article).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(screen.getByRole("link", { name: /Переговоры в Испании/i })).toHaveAttribute("href", "/stories/42");
    expect(screen.getByText(/не доказывает причинность/i)).toBeVisible();
  });

  it("keeps partial missing inputs honest and never exposes unsafe links or fake controls", () => {
    const partial: SignalDetail = {
      ...complete,
      evidence_completeness: "partial",
      rule: { ...complete.rule, threshold: {}, current_rule_reference: null },
      values: {
        observed: { value: 0, enabled: false },
        baseline: {},
        window: { start: null, end: null, basis: "not_persisted", status: "unknown" },
      },
      chart_points: [{ time: "2026-07-15T20:00:00Z", score: -2 }],
      articles: [{ ...complete.articles[0], title: "Опасная ссылка", url: "https://reader:secret@example.com/private" }],
      articles_page: { total: 140, returned: 1, limit: 100, truncated: true, has_more: true },
    };
    render(<SignalEvidence detail={partial} storiesEnabled={false} />);

    expect(screen.getByText(/частичные доказательства/i)).toBeVisible();
    expect(screen.getAllByText(/не сохранено/i).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/график доказательства не сохранён/i)).toBeVisible();
    expect(screen.getByText("Опасная ссылка").closest("a")).toBeNull();
    expect(screen.getByText(/показана 1 из 140/i)).toBeVisible();
    expect(screen.queryByRole("button", { name: /загрузить ещё/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Переговоры в Испании/i })).not.toBeInTheDocument();
    expect(screen.getByText("Переговоры в Испании")).toBeVisible();
    expect(within(screen.getByRole("region", { name: /наблюдаемое значение/i })).getByText(/value/i).closest("li")).toHaveTextContent("0");
  });

  it("distinguishes a not-applicable baseline from missing evidence", () => {
    render(<SignalEvidence detail={{ ...complete, values: { ...complete.values, baseline: { type: "not_applicable" } } }} storiesEnabled />);
    expect(screen.getByRole("region", { name: /базовое значение/i })).toHaveTextContent("сравнение не требуется");
  });
});
