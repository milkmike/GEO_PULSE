import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("stories API client", () => {
  it("sends every durable story filter and cursor without changing its meaning", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ stories: [], next_cursor: null }),
    });
    vi.stubGlobal("fetch", fetchMock);

    await api.stories(
      {
        country: "ES",
        topic: "diplomacy",
        lifecycle: "resolved",
        entity_id: "entity-putin",
        date_from: "2026-06-15T00:00:00.000Z",
        date_to: "2026-07-15T23:59:59.999Z",
        limit: 20,
      },
      "opaque-cursor",
    );

    const url = new URL(fetchMock.mock.calls[0][0]);
    expect(url.pathname).toBe("/api/v2/stories");
    expect(Object.fromEntries(url.searchParams)).toEqual({
      country: "ES",
      topic: "diplomacy",
      lifecycle: "resolved",
      entity_id: "entity-putin",
      date_from: "2026-06-15T00:00:00.000Z",
      date_to: "2026-07-15T23:59:59.999Z",
      limit: "20",
      cursor: "opaque-cursor",
    });
  });

  it("loads global, country, and cursor-paginated story detail endpoints", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
    vi.stubGlobal("fetch", fetchMock);

    await api.countryStories("es", { limit: 6 });
    await api.story(42, "article-cursor", 25);

    expect(new URL(fetchMock.mock.calls[0][0]).pathname).toBe(
      "/api/v2/countries/ES/stories",
    );
    const detail = new URL(fetchMock.mock.calls[1][0]);
    expect(detail.pathname).toBe("/api/v2/stories/42");
    expect(Object.fromEntries(detail.searchParams)).toEqual({
      article_limit: "25",
      article_cursor: "article-cursor",
    });
  });
});
