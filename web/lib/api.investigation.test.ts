import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

describe("investigation API client", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({}),
    }));
  });

  it("builds the exact RRI explanation query with URLSearchParams", async () => {
    const signal = new AbortController().signal;
    await api.indexExplanation(" es ", {
      at: "2026-07-15T23:59:59.999Z",
      windowHours: 24,
      rriVersion: "v1",
    }, signal);

    const [url, init] = vi.mocked(fetch).mock.calls[0];
    const parsed = new URL(String(url));
    expect(parsed.pathname).toBe("/api/v2/countries/ES/index-explanation");
    expect(Object.fromEntries(parsed.searchParams)).toEqual({
      at: "2026-07-15T23:59:59.999Z",
      window_hours: "24",
      rri_version: "v1",
    });
    expect(init).toMatchObject({ signal });
  });

  it("loads signal detail and thermometer methodology without write parameters", async () => {
    await api.signalDetail(17);
    await api.temperatureMethodology();

    expect(vi.mocked(fetch).mock.calls.map(([url]) => new URL(String(url)).pathname)).toEqual([
      "/api/v2/signals/17",
      "/api/v2/methodology/temperature",
    ]);
  });
});
