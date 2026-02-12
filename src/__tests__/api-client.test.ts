import { describe, it, expect, vi, beforeEach } from "vitest";

// Mock fetch globally
const mockFetch = vi.fn();
vi.stubGlobal("fetch", mockFetch);

// Must import after mocking
import { api, ApiError } from "@/lib/api-client";

describe("api-client", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("calls fetch with correct URL for SSR (absolute)", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ data: "test" }),
    });

    const result = await api("/api/v1/countries", { days: 30 });

    expect(result).toEqual({ data: "test" });
    expect(mockFetch).toHaveBeenCalledOnce();

    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toContain("/api/v1/countries");
    expect(calledUrl).toContain("days=30");
  });

  it("appends search params correctly", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({}),
    });

    await api("/api/v1/threads", { limit: 50, sort: "importance" });

    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toContain("limit=50");
    expect(calledUrl).toContain("sort=importance");
  });

  it("skips undefined/null params", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({}),
    });

    await api("/api/v1/test", { keep: "yes", skip: undefined, also_skip: null });

    const calledUrl = mockFetch.mock.calls[0][0];
    expect(calledUrl).toContain("keep=yes");
    expect(calledUrl).not.toContain("skip");
  });

  it("throws ApiError on non-ok response", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 404,
      statusText: "Not Found",
    });

    await expect(api("/api/v1/missing")).rejects.toThrow(ApiError);
  });

  it("retries on 5xx errors", async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: false, status: 500, statusText: "Internal Server Error" })
      .mockResolvedValueOnce({ ok: true, json: () => Promise.resolve({ retried: true }) });

    const result = await api("/api/v1/flaky", undefined, { retries: 1 });
    expect(result).toEqual({ retried: true });
    expect(mockFetch).toHaveBeenCalledTimes(2);
  });

  it("does not retry on 4xx errors", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      statusText: "Bad Request",
    });

    await expect(api("/api/v1/bad")).rejects.toThrow(ApiError);
    expect(mockFetch).toHaveBeenCalledOnce();
  });
});
