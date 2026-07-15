import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ArticleSearchResponse, SearchArticle } from "@/lib/types";

const navigation = vi.hoisted(() => ({
  params: "",
  push: vi.fn<(href: string) => void>(),
  replace: vi.fn<(href: string) => void>(),
}));

const apiMocks = vi.hoisted(() => ({
  searchArticles: vi.fn(),
  entitySuggestions: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: navigation.push, replace: navigation.replace }),
  useSearchParams: () => new URLSearchParams(navigation.params),
}));

vi.mock("@/lib/api", () => ({
  api: {
    searchArticles: apiMocks.searchArticles,
    entitySuggestions: apiMocks.entitySuggestions,
  },
}));

import SearchPage from "./page";

function article(id: number, title: string): SearchArticle {
  return {
    article_id: id,
    title,
    summary: "Контекст материала",
    url: `https://example.com/${id}`,
    published_at: "2026-07-14T12:30:00+00:00",
    language: "ru",
    source: { name: "Тестовый источник", country: "ES", tier: "mainstream" },
    topics: [],
    matched_entities: [],
    sentiment: null,
    action_level: null,
    story: null,
    why_included: "Совпадает с запросом",
    relevance_score: 0.8,
    confidence: 0.9,
    evidence: [{ type: "text_span", article_id: id, text: "«Совпадение»" }],
    scores: {
      lexical: 0.8,
      entity: 0,
      topic: 0,
      freshness: 0.9,
      trust: 0.8,
      story: 0,
      vector: null,
    },
  };
}

function response(
  items: SearchArticle[] = [],
  nextCursor: string | null = null,
): ArticleSearchResponse {
  return {
    query: "",
    filters: {
      country: null,
      topic: null,
      entity_id: null,
      from: null,
      to: null,
      tier: null,
      language: null,
    },
    sort: "relevance",
    limit: 25,
    semantic_search: "unavailable",
    items,
    candidate_count: items.length,
    next_cursor: nextCursor,
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
  apiMocks.searchArticles.mockReset().mockResolvedValue(response());
  apiMocks.entitySuggestions.mockReset().mockResolvedValue({
    items: [],
    limit: 8,
    offset: 0,
    has_more: false,
    next_cursor: null,
  });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("SearchPage URL and request lifecycle", () => {
  it("canonicalizes the default 90-day range into the URL before searching", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-07-15T12:00:00Z"));
    navigation.params = "q=%D0%9F%D1%83%D1%82%D0%B8%D0%BD";

    const view = render(<SearchPage />);

    await waitFor(() =>
      expect(navigation.replace).toHaveBeenCalledWith(
        "/search?q=%D0%9F%D1%83%D1%82%D0%B8%D0%BD&from=2026-04-16",
      ),
    );
    expect(apiMocks.searchArticles).not.toHaveBeenCalled();

    navigation.params = "q=%D0%9F%D1%83%D1%82%D0%B8%D0%BD&from=2026-04-16";
    view.rerender(<SearchPage />);

    expect(screen.getByLabelText("От даты")).toHaveValue("2026-04-16");
    await waitFor(() =>
      expect(apiMocks.searchArticles).toHaveBeenCalledWith(
        expect.objectContaining({ q: "Путин", from: "2026-04-16" }),
        null,
        expect.any(AbortSignal),
      ),
    );
  });

  it("keeps all-history as a durable URL state and does not send a hidden date", async () => {
    const user = userEvent.setup();
    navigation.params = "q=%D0%9F%D1%83%D1%82%D0%B8%D0%BD&from=2026-04-16";
    const firstView = render(<SearchPage />);

    await user.click(screen.getByRole("button", { name: /искать по всей истории/i }));
    await user.click(screen.getByRole("button", { name: /найти материалы/i }));

    const pushed = new URL(String(navigation.push.mock.calls.at(-1)?.[0]), "https://massaraksh.tech");
    expect(pushed.searchParams.get("range")).toBe("all");
    expect(pushed.searchParams.has("from")).toBe(false);

    firstView.unmount();
    navigation.params = "q=%D0%9F%D1%83%D1%82%D0%B8%D0%BD&range=all";
    render(<SearchPage />);

    await waitFor(() =>
      expect(apiMocks.searchArticles).toHaveBeenLastCalledWith(
        expect.objectContaining({ q: "Путин" }),
        null,
        expect.any(AbortSignal),
      ),
    );
    const allHistoryRequest = apiMocks.searchArticles.mock.calls.at(-1)?.[0];
    expect(allHistoryRequest).not.toHaveProperty("from");
    expect(navigation.replace).not.toHaveBeenCalled();
  });

  it("aborts and ignores a stale load-more response after URL history changes", async () => {
    const user = userEvent.setup();
    const stalePage = deferred<ArticleSearchResponse>();
    navigation.params = "q=old&from=2026-04-16";
    apiMocks.searchArticles.mockImplementation(
      (request: { q?: string }, cursor?: string | null) => {
        if (cursor === "old-cursor") return stalePage.promise;
        if (request.q === "new") return Promise.resolve(response([article(2, "Новый результат")]));
        return Promise.resolve(response([article(1, "Старый результат")], "old-cursor"));
      },
    );

    const view = render(<SearchPage />);
    await screen.findByRole("heading", { name: "Старый результат" });
    await user.click(screen.getByRole("button", { name: /следующую страницу/i }));
    const staleSignal = apiMocks.searchArticles.mock.calls.at(-1)?.[2] as AbortSignal;

    navigation.params = "q=new&from=2026-04-16";
    view.rerender(<SearchPage />);
    await screen.findByRole("heading", { name: "Новый результат" });
    expect(staleSignal.aborted).toBe(true);

    await act(async () => stalePage.resolve(response([article(3, "Устаревшая страница")])));
    expect(screen.queryByRole("heading", { name: "Устаревшая страница" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Новый результат" })).toBeVisible();
  });

  it("aborts an in-flight load-more request when the page unmounts", async () => {
    const user = userEvent.setup();
    const pendingPage = deferred<ArticleSearchResponse>();
    navigation.params = "q=old&from=2026-04-16";
    apiMocks.searchArticles.mockImplementation(
      (_request: unknown, cursor?: string | null) =>
        cursor ? pendingPage.promise : Promise.resolve(response([article(1, "Результат")], "cursor")),
    );

    const view = render(<SearchPage />);
    await screen.findByRole("heading", { name: "Результат" });
    await user.click(screen.getByRole("button", { name: /следующую страницу/i }));
    const signal = apiMocks.searchArticles.mock.calls.at(-1)?.[2] as AbortSignal;

    view.unmount();
    expect(signal.aborted).toBe(true);
  });
});

describe("SearchPage entity combobox", () => {
  it("uses one active-descendant keyboard pattern and clears stale labels on history navigation", async () => {
    const user = userEvent.setup();
    navigation.params = "from=2026-04-16";
    apiMocks.entitySuggestions.mockResolvedValue({
      items: [
        {
          id: "93dbeaec-c20b-44ad-aaed-46b18ea86a47",
          node_id: "person:93dbeaec-c20b-44ad-aaed-46b18ea86a47",
          kind: "person",
          label: "Владимир Путин",
          aliases: ["Путин"],
          match_explanation: "exact alias",
        },
      ],
      limit: 8,
      offset: 0,
      has_more: false,
      next_cursor: null,
    });

    const view = render(<SearchPage />);
    const combobox = screen.getByRole("combobox", { name: /персона, организация/i });
    await user.type(combobox, "Путин");
    const option = await screen.findByRole("option", { name: /Владимир Путин/i });
    expect(option).toHaveAttribute("tabindex", "-1");

    fireEvent.blur(combobox);
    expect(combobox).toHaveAttribute("aria-expanded", "false");
    combobox.focus();
    await user.keyboard("{Escape}");
    expect(combobox).toHaveAttribute("aria-expanded", "false");
    await user.keyboard("{ArrowDown}");
    expect(combobox).toHaveAttribute("aria-expanded", "true");
    expect(combobox).toHaveAttribute("aria-activedescendant", option.id);
    await user.keyboard("{Enter}");
    expect(combobox).toHaveValue("Владимир Путин");

    navigation.params = "entity_id=11111111-1111-4111-8111-111111111111&from=2026-04-16";
    view.rerender(<SearchPage />);
    await waitFor(() => expect(combobox).toHaveValue(""));
    expect(screen.getByText(/сущность 11111111…/i)).toBeVisible();
    expect(screen.queryByDisplayValue("Владимир Путин")).not.toBeInTheDocument();
  });

  it("lists only the source tiers supported by the search contract", () => {
    navigation.params = "range=all";
    render(<SearchPage />);
    const tier = screen.getByLabelText("Тип источника");
    const labels = within(tier)
      .getAllByRole("option")
      .map((option) => option.textContent);

    expect(labels).toEqual([
      "Все типы",
      "Официальный",
      "Мейнстрим",
      "Независимый",
      "Социальные медиа",
      "Внутренняя оппозиция",
      "Западный прокси",
      "Аналитика",
    ]);
    expect(labels).not.toContain("Государственный");
    expect(apiMocks.searchArticles).not.toHaveBeenCalled();
  });
});
