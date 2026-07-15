import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { IndexExplanation } from "@/lib/types";
import InvestigationPanel from "./InvestigationPanel";

const apiMocks = vi.hoisted(() => ({ indexExplanation: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: apiMocks }));

const explanation: IndexExplanation = {
  country_code: "ES",
  from_time: "2026-07-14T20:00:00Z",
  to_time: "2026-07-15T20:00:00Z",
  rri_version: "v1",
  exact_changes: {
    from_value: -10,
    to_value: -2,
    total_delta: 8,
    structural_delta: 1,
    media_delta: 5,
    boost_delta: 1.5,
    exact_subtotal: 7.5,
    calculation_adjustment_delta: 0.4,
    rounding_residual: 0.1,
    from_time: "2026-07-14T18:13:00Z",
    to_time: "2026-07-15T19:41:00Z",
    rri_version: "v1",
    weights: { from: { structural: 0.7, media: 0.3 }, to: { structural: 0.7, media: 0.3 } },
    calculation_adjustments: { from: 0, to: 0.4, from_rule: null, to_rule: null },
    input_counts: { from_articles: 10, to_articles: 12, from_gdelt_volume: 30, to_gdelt_volume: 38 },
  },
  estimated_contributions: [{
    status: "estimated", label: "estimated", method: "leave_one_event_out",
    event_key: "port-talks", input_article_ids: [1, 2], removed_article_ids: [2],
    why_included: "Публикации объединены одним событием", relevance_score: 0.8,
    confidence: 0.74, estimated_delta: 2.3, actual_media_estimate: -2,
    counterfactual_media_estimate: -4.3,
    evidence: { input_article_ids: [1, 2], removed_article_ids: [2], relevance_basis: "event_key" },
  }],
  context: [{
    scope: "article", id: 1, label: "El País: переговоры",
    url: "https://elpais.com/mundo/talks", occurred_at: "2026-07-15T19:00:00Z",
    why_included: "Опубликовано рядом с выбранным сдвигом", relevance_score: 0.77,
    confidence: 0.8, evidence: {},
  }, {
    scope: "article", id: 2, label: "Опасная ссылка",
    url: "javascript:alert(1)", occurred_at: "2026-07-15T19:30:00Z",
    why_included: "Только временная близость", relevance_score: 0.5,
    confidence: 0.4, evidence: {},
  }],
  related_story_ids: [42], related_signal_ids: [17], evidence_completeness: "partial",
  limitations: ["contextual_proximity_is_not_causation"],
  cache: { status: "miss", input_hash: "a".repeat(64) },
};

describe("InvestigationPanel", () => {
  beforeEach(() => apiMocks.indexExplanation.mockReset().mockResolvedValue(explanation));

  it("keeps exact, estimated and contextual evidence in separately labelled regions", async () => {
    render(<InvestigationPanel open countryCode="ES" countryName="Испания" at="2026-07-15T20:00:00Z" onClose={() => {}} />);

    const exact = await screen.findByRole("region", { name: /что изменило расчёт · точно/i });
    const estimated = screen.getByRole("region", { name: /модельная оценка/i });
    const context = screen.getByRole("region", { name: /контекст, не причина/i });

    expect(exact).toHaveTextContent("+8,0");
    expect(exact).toHaveTextContent(/поправка расчёта/i);
    expect(exact).toHaveTextContent(/остаток округления/i);
    expect(within(exact).queryByText("+2,3")).not.toBeInTheDocument();
    expect(estimated).toHaveTextContent("+2,3");
    expect(context).toHaveTextContent(/не доказывает причинность/i);
    expect(screen.getByText(/частичные доказательства/i)).toBeVisible();

    const requestedWindow = screen.getByRole("region", { name: /запрошенное окно/i });
    expect(requestedWindow.querySelector('time[datetime="2026-07-14T20:00:00Z"]')).not.toBeNull();
    expect(requestedWindow.querySelector('time[datetime="2026-07-15T20:00:00Z"]')).not.toBeNull();
    expect(exact.querySelector('time[datetime="2026-07-14T18:13:00Z"]')).not.toBeNull();
    expect(exact.querySelector('time[datetime="2026-07-15T19:41:00Z"]')).not.toBeNull();
    expect(exact).not.toHaveTextContent(/ровно 24|за 24 часа/i);

    expect(screen.getByRole("link", { name: /El País: переговоры/i })).toHaveAttribute(
      "href", "https://elpais.com/mundo/talks",
    );
    expect(screen.getByText("Опасная ссылка").closest("a")).toBeNull();
  });

  it("translates live counterfactual and context codes without presenting them as user copy", async () => {
    const liveCodes: IndexExplanation = {
      ...explanation,
      estimated_contributions: [
        {
          ...explanation.estimated_contributions[0],
          status: "omitted",
          estimated_delta: undefined,
          why_included: "counterfactual_status_disclosed_for_selected_rri_window",
          reason: "media_component_not_article_temperature",
        },
        {
          ...explanation.estimated_contributions[0],
          event_key: null,
          status: "omitted",
          estimated_delta: undefined,
          why_included: "counterfactual_requested_for_event_cluster",
          reason: "article_inputs_missing",
        },
      ],
      context: [{
        ...explanation.context[0],
        why_included: "published_or_active_in_selected_window",
      }],
      limitations: [
        "contextual_proximity_is_not_causation",
        "counterfactual_requires_article_temperature_media",
        "counterfactual_reconstructs_media_window_not_historical_input_snapshot",
        "counterfactual_article_inputs_missing",
        "counterfactual_event_clusters_unavailable",
      ],
    };
    apiMocks.indexExplanation.mockResolvedValue(liveCodes);
    render(<InvestigationPanel open countryCode="ES" countryName="Испания" at="2026-07-15T20:00:00Z" onClose={() => {}} />);

    expect(await screen.findByText(/медиаслой этой точки RRI рассчитан не по публикациям/i)).toBeVisible();
    expect(screen.getByText(/для реконструкции нет сохранённых входных публикаций/i)).toBeVisible();
    expect(screen.getByText(/попал в запрошенное временное окно/i)).toBeVisible();
    expect(screen.getByText(/исторический снимок входов не сохранялся/i)).toBeVisible();
    expect(screen.queryByText("media_component_not_article_temperature")).not.toBeInTheDocument();
    expect(screen.queryByText("published_or_active_in_selected_window")).not.toBeInTheDocument();
  });

  it("aborts a stale request and never lets its response replace the current timestamp", async () => {
    let resolveFirst!: (value: IndexExplanation) => void;
    const first = new Promise<IndexExplanation>((resolve) => { resolveFirst = resolve; });
    apiMocks.indexExplanation.mockReturnValueOnce(first).mockResolvedValueOnce({
      ...explanation,
      exact_changes: { ...explanation.exact_changes, total_delta: -4 },
    });
    const { rerender } = render(
      <InvestigationPanel open countryCode="ES" countryName="Испания" at="2026-07-15T20:00:00Z" onClose={() => {}} />,
    );
    const firstSignal = apiMocks.indexExplanation.mock.calls[0][2] as AbortSignal;

    rerender(<InvestigationPanel open countryCode="ES" countryName="Испания" at="2026-07-16T20:00:00Z" onClose={() => {}} />);
    await screen.findByText("−4,0");
    expect(firstSignal.aborted).toBe(true);
    resolveFirst(explanation);
    await waitFor(() => expect(screen.queryByText("+8,0")).not.toBeInTheDocument());
  });

  it("traps focus, closes on Escape and restores the trigger", async () => {
    const user = userEvent.setup();
    const trigger = document.createElement("button");
    trigger.textContent = "сдвиг";
    document.body.append(trigger);
    trigger.focus();
    const triggerRef = { current: trigger };
    const onClose = vi.fn();
    const { rerender } = render(
      <InvestigationPanel open countryCode="ES" countryName="Испания" at="2026-07-15T20:00:00Z" triggerRef={triggerRef} onClose={onClose} />,
    );

    const dialog = screen.getByRole("dialog", { name: /единое расследование/i });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: /закрыть/i })).toHaveFocus());
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledOnce();
    rerender(<InvestigationPanel open={false} countryCode="ES" countryName="Испания" at={null} triggerRef={triggerRef} onClose={onClose} />);
    await waitFor(() => expect(trigger).toHaveFocus());
    trigger.remove();
  });

  it("keeps focus locked when a parent rerenders with a new close callback", async () => {
    const user = userEvent.setup();
    const firstClose = vi.fn();
    const latestClose = vi.fn();
    const trigger = document.createElement("button");
    document.body.append(trigger);
    trigger.focus();
    const restoreSpy = vi.spyOn(trigger, "focus");
    const triggerRef = { current: trigger };
    const { rerender } = render(
      <InvestigationPanel open countryCode="ES" countryName="Испания" at="2026-07-15T20:00:00Z" triggerRef={triggerRef} onClose={firstClose} />,
    );
    const closeButton = screen.getAllByRole("button", { name: /закрыть единое расследование/i }).at(-1)!;
    await waitFor(() => expect(closeButton).toHaveFocus());

    rerender(<InvestigationPanel open countryCode="ES" countryName="Испания" at="2026-07-15T20:00:00Z" triggerRef={triggerRef} onClose={latestClose} />);
    await waitFor(() => expect(closeButton).toHaveFocus());
    expect(restoreSpy).not.toHaveBeenCalled();
    await user.keyboard("{Escape}");
    expect(firstClose).not.toHaveBeenCalled();
    expect(latestClose).toHaveBeenCalledOnce();
    trigger.remove();
  });

  it("restores the RRI heading for a durable URL or plot opening without a DOM trigger", async () => {
    const fallback = document.createElement("h2");
    fallback.tabIndex = -1;
    fallback.textContent = "Индекс RRI";
    document.body.append(fallback);
    const nonInteractive = document.createElement("div");
    nonInteractive.tabIndex = -1;
    document.body.append(nonInteractive);
    nonInteractive.focus();
    const fallbackRef = { current: fallback };
    const { rerender } = render(
      <InvestigationPanel open countryCode="ES" countryName="Испания" at="2026-07-15T20:00:00Z" fallbackFocusRef={fallbackRef} onClose={() => {}} />,
    );
    await waitFor(() => expect(within(screen.getByRole("dialog")).getByRole("button", { name: /закрыть единое расследование/i })).toHaveFocus());

    rerender(<InvestigationPanel open={false} countryCode="ES" countryName="Испания" at={null} fallbackFocusRef={fallbackRef} onClose={() => {}} />);
    await waitFor(() => expect(fallback).toHaveFocus());
    fallback.remove();
    nonInteractive.remove();
  });

  it("falls back to the RRI heading when the original marker is disconnected", async () => {
    const trigger = document.createElement("button");
    const fallback = document.createElement("h2");
    fallback.tabIndex = -1;
    document.body.append(trigger, fallback);
    trigger.focus();
    const triggerRef = { current: trigger };
    const fallbackRef = { current: fallback };
    const { rerender } = render(
      <InvestigationPanel open countryCode="ES" countryName="Испания" at="2026-07-15T20:00:00Z" triggerRef={triggerRef} fallbackFocusRef={fallbackRef} onClose={() => {}} />,
    );
    await waitFor(() => expect(within(screen.getByRole("dialog")).getByRole("button", { name: /закрыть единое расследование/i })).toHaveFocus());
    trigger.remove();

    rerender(<InvestigationPanel open={false} countryCode="ES" countryName="Испания" at={null} triggerRef={triggerRef} fallbackFocusRef={fallbackRef} onClose={() => {}} />);
    await waitFor(() => expect(fallback).toHaveFocus());
    fallback.remove();
  });
});
