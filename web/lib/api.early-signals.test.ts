import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("early signals API client", () => {
  it("requests six world items or a normalized country scope and forwards abort", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ as_of: "", items: [], notice: null }) });
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();

    await api.earlySignals(null);
    await api.earlySignals(" rs ", controller.signal);

    const world = new URL(fetchMock.mock.calls[0][0]);
    const country = new URL(fetchMock.mock.calls[1][0]);
    expect(world.pathname).toBe("/api/v2/early-signals");
    expect(Object.fromEntries(world.searchParams)).toEqual({ limit: "6" });
    expect(Object.fromEntries(country.searchParams)).toEqual({ limit: "6", country: "RS" });
    expect(fetchMock.mock.calls[1][1].signal).toBeInstanceOf(AbortSignal);
  });
});
