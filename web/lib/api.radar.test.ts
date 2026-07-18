import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("radar API client", () => {
  it("serializes exact story and signal relation filters", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [], next_cursor: null }) });
    vi.stubGlobal("fetch", fetchMock);

    await api.radar({ storyId: 42, signalId: 17, limit: 12 });

    const url = new URL(fetchMock.mock.calls[0][0]);
    expect(url.pathname).toBe("/api/v2/radar");
    expect(Object.fromEntries(url.searchParams)).toEqual({ story_id: "42", signal_id: "17", limit: "12" });
  });
});
