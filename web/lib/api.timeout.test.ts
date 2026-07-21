import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

describe("public API timeout", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("keeps slow dossier requests alive but aborts a stall after 30 seconds", async () => {
    vi.useFakeTimers();
    let aborted = false;
    vi.stubGlobal("fetch", vi.fn((_url, options: RequestInit) => new Promise((_resolve, reject) => {
      options.signal?.addEventListener("abort", () => {
        aborted = true;
        reject(options.signal?.reason);
      }, { once: true });
    })));

    const pending = api.meta();
    const rejection = expect(pending).rejects.toMatchObject({ name: "TimeoutError" });
    await vi.advanceTimersByTimeAsync(15_000);
    expect(aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(15_000);

    await rejection;
  });
});
