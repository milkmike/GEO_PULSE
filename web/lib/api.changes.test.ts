import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";
import nextConfig from "../next.config";

afterEach(() => vi.unstubAllGlobals());

describe("changes API and redirects", () => {
  it("requests 30 rows for seven days and serializes filters and cursor", async () => {
    const fetcher = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    vi.stubGlobal("fetch", fetcher);
    await api.changes();
    expect(String(fetcher.mock.calls[0][0])).toContain("/api/v2/changes?days=7&limit=30");
    await api.changes({ type: "tone", country: "gb", important: true }, "a+b/c=");
    const url = new URL(fetcher.mock.calls[1][0]);
    expect(Object.fromEntries(url.searchParams)).toEqual({ days: "7", limit: "30", type: "tone", country: "GB", important: "1", cursor: "a+b/c=" });
  });
  it("temporarily redirects list addresses while preserving detail routes", async () => {
    expect(await nextConfig.redirects?.()).toEqual([
      { source: "/signals", destination: "/changes", permanent: false },
      { source: "/radar", destination: "/changes?type=waves", permanent: false },
    ]);
  });
});
