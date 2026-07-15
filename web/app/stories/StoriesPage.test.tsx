import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StoriesListResponse, StoryListItem } from "@/lib/types";

const navigation = vi.hoisted(() => ({
  params: "",
  push: vi.fn<(href: string) => void>(),
  replace: vi.fn<(href: string) => void>(),
}));

const apiMocks = vi.hoisted(() => ({
  stories: vi.fn(),
  entitySuggestions: vi.fn(),
  meta: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: navigation.push, replace: navigation.replace }),
  useSearchParams: () => new URLSearchParams(navigation.params),
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));

import StoriesPage from "./page";

function story(id: number, title: string): StoryListItem {
  return {
    id,
    slug: `story-${id}`,
    title_ru: title,
    title_en: null,
    summary: "Межстрановой сюжет",
    lifecycle: "developing",
    first_seen: "2026-07-13T09:00:00+00:00",
    last_seen: "2026-07-15T12:00:00+00:00",
    article_count: 4,
    source_count: 3,
    country_count: 2,
    highest_action_level: 3,
    clustering_confidence: 0.82,
    generated_at: null,
    countries: ["ES", "FR"],
    primary_url: `https://example.com/${id}`,
    why_included: ["cross_country"],
    relevance_score: 0.8,
    confidence: 0.82,
    evidence: { topics: ["diplomacy"] },
    linked_signal_count: 0,
    linked_signals: [],
    latest_rri_shift: null,
  };
}

function response(
  stories: StoryListItem[] = [],
  nextCursor: string | null = null,
): StoriesListResponse {
  return {
    stories,
    next_cursor: nextCursor,
    consistency: {
      ranking_at: "2026-07-15T12:00:00+00:00",
      membership_generation: 12,
      mode: "membership_generation_live_filters",
      frozen_features: ["membership", "article_count", "countries", "primary_url"],
      live_filters: ["lifecycle", "topic", "entity_id", "merge_state"],
      limitation: "Состав зафиксирован, но фильтры используют текущее состояние.",
    },
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

beforeEach(() => {
  navigation.params = "";
  navigation.push.mockReset();
  navigation.replace.mockReset();
  apiMocks.stories.mockReset().mockResolvedValue(response());
  apiMocks.entitySuggestions.mockReset().mockResolvedValue({ items: [] });
  apiMocks.meta.mockReset().mockResolvedValue({
    countries: [{ code: "ES", name: "Испания" }],
    topics: { diplomacy: "Дипломатия" },
  });
});

afterEach(() => vi.useRealTimers());

describe("StoriesPage durable filters", () => {
  it("canonicalizes the default period before loading stories", async () => {
    navigation.params = "country=ES";

    render(<StoriesPage />);

    await waitFor(() =>
      expect(navigation.replace).toHaveBeenCalledWith("/stories?country=ES&period=30d"),
    );
    expect(apiMocks.stories).not.toHaveBeenCalled();
  });

  it("maps country, topic, lifecycle, entity, and period from the URL to the API", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-07-15T12:00:00Z"));
    navigation.params = [
      "country=ES",
      "topic=diplomacy",
      "lifecycle=resolved",
      "entity_id=entity-putin",
      "entity_label=%D0%92%D0%BB%D0%B0%D0%B4%D0%B8%D0%BC%D0%B8%D1%80+%D0%9F%D1%83%D1%82%D0%B8%D0%BD",
      "period=30d",
    ].join("&");
    apiMocks.stories.mockResolvedValue(response([story(42, "Испания и Россия: портовые переговоры")]));

    render(<StoriesPage />);

    await waitFor(() =>
      expect(apiMocks.stories).toHaveBeenCalledWith(
        expect.objectContaining({
          country: "ES",
          topic: "diplomacy",
          lifecycle: "resolved",
          entity_id: "entity-putin",
          date_from: "2026-06-15T00:00:00.000Z",
          date_to: "2026-07-15T23:59:59.999Z",
          limit: 20,
        }),
        null,
        expect.any(AbortSignal),
      ),
    );
    expect(
      await screen.findByRole("link", {
        name: "Испания и Россия: портовые переговоры",
      }),
    ).toHaveAttribute("href", "/stories/42");
    expect(screen.getByRole("combobox", { name: /сущность/i })).toHaveValue(
      "Владимир Путин",
    );
    const consistency = screen.getByRole("status", { name: /режим выдачи/i });
    expect(consistency).toHaveTextContent(/состав сюжетов зафиксирован/i);
    expect(consistency).toHaveAttribute(
      "title",
      "Состав зафиксирован, но фильтры используют текущее состояние.",
    );
  });

  it("supports active-descendant keyboard selection for entity suggestions", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    navigation.params = "period=all";
    apiMocks.entitySuggestions.mockResolvedValue({
      items: [{
        id: "person-putin",
        node_id: "entity:person-putin",
        kind: "person",
        label: "Владимир Путин",
        aliases: ["Путин"],
        match_explanation: "alias",
      }],
      limit: 8,
      offset: 0,
      has_more: false,
      next_cursor: null,
    });

    render(<StoriesPage />);
    const combobox = await screen.findByRole("combobox", { name: /сущность/i });
    expect(combobox).toHaveAttribute("aria-autocomplete", "list");
    await user.type(combobox, "Путин");
    await act(async () => vi.advanceTimersByTime(300));
    const option = await screen.findByRole("option", { name: /Владимир Путин/i });
    expect(option).toHaveAttribute("tabindex", "-1");

    await user.keyboard("{ArrowDown}");
    expect(combobox).toHaveAttribute("aria-activedescendant", option.id);
    await user.keyboard("{Enter}");
    expect(combobox).toHaveValue("Владимир Путин");
    expect(combobox).toHaveAttribute("aria-expanded", "false");

    combobox.focus();
    await user.keyboard("{Escape}");
    expect(combobox).not.toHaveAttribute("aria-activedescendant");
    fireEvent.blur(combobox);
    expect(combobox).toHaveAttribute("aria-expanded", "false");
  });

  it("shows the selected and actually indexed coverage in an empty result", async () => {
    navigation.params = "period=7d";
    apiMocks.stories.mockResolvedValue({
      ...response(),
      coverage: {
        selected_from: "2026-07-08T00:00:00+00:00",
        selected_to: "2026-07-15T23:59:59.999000+00:00",
        available_from: "2026-01-10T08:00:00+00:00",
        available_to: "2026-07-15T11:30:00+00:00",
      },
    });

    render(<StoriesPage />);

    expect(await screen.findByText(/выбранный период: последние 7 дней/i)).toBeVisible();
    expect(screen.getByText(/проиндексированное покрытие:/i)).toHaveTextContent(
      "10.01.2026–15.07.2026",
    );
  });

  it("appends a cursor page without reordering existing stories", async () => {
    const user = userEvent.setup();
    navigation.params = "period=all";
    apiMocks.stories
      .mockResolvedValueOnce(response([story(42, "Первый сюжет")], "next-page"))
      .mockResolvedValueOnce(response([story(43, "Второй сюжет")], null));

    render(<StoriesPage />);
    await screen.findByRole("link", { name: "Первый сюжет" });
    await user.click(screen.getByRole("button", { name: /следующую страницу/i }));
    await screen.findByRole("link", { name: "Второй сюжет" });

    const headings = screen.getAllByRole("heading", { level: 2 });
    expect(headings.map((heading) => heading.textContent)).toEqual([
      "Первый сюжет",
      "Второй сюжет",
    ]);
    expect(apiMocks.stories).toHaveBeenLastCalledWith(
      expect.objectContaining({ limit: 20 }),
      "next-page",
      expect.any(AbortSignal),
    );
  });

  it("aborts and ignores stale cursor results when browser history changes", async () => {
    const user = userEvent.setup();
    const stalePage = deferred<StoriesListResponse>();
    navigation.params = "country=ES&period=all";
    apiMocks.stories.mockImplementation(
      (request: { country?: string }, cursor?: string | null) => {
        if (cursor) return stalePage.promise;
        if (request.country === "FR") return Promise.resolve(response([story(50, "Французский сюжет")]));
        return Promise.resolve(response([story(42, "Испанский сюжет")], "stale-cursor"));
      },
    );

    const view = render(<StoriesPage />);
    await screen.findByRole("link", { name: "Испанский сюжет" });
    await user.click(screen.getByRole("button", { name: /следующую страницу/i }));
    const staleSignal = apiMocks.stories.mock.calls.at(-1)?.[2] as AbortSignal;

    navigation.params = "country=FR&period=all";
    view.rerender(<StoriesPage />);
    await screen.findByRole("link", { name: "Французский сюжет" });
    expect(staleSignal.aborted).toBe(true);

    await act(async () => stalePage.resolve(response([story(51, "Устаревший сюжет")])));
    expect(screen.queryByRole("link", { name: "Устаревший сюжет" })).not.toBeInTheDocument();
  });
});
