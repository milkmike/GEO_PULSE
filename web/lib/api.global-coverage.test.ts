import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("global coverage API client", () => {
  it("reads the public coverage snapshot without a country filter", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ status: "not_started", countries: [] }) });
    vi.stubGlobal("fetch", fetchMock);
    await api.globalCoverage();
    const url = new URL(fetchMock.mock.calls[0][0]);
    expect(url.pathname).toBe("/api/v2/early-signals/coverage");
    expect(url.search).toBe("");
    expect(fetchMock.mock.calls[0][1].cache).toBe("no-store");
  });
});
