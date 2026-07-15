import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  readFeatureFlags: vi.fn(),
  notFound: vi.fn(() => { throw new Error("NEXT_NOT_FOUND"); }),
}));

vi.mock("@/lib/features.server", () => ({ readFeatureFlags: mocks.readFeatureFlags }));
vi.mock("next/navigation", () => ({ notFound: mocks.notFound }));
vi.mock("./SignalDetailClient", () => ({
  default: ({ signalId }: { signalId: number }) => <div>signal client {signalId}</div>,
}));

import SignalDetailPage from "./page";

const enabled = {
  searchNavigation: false,
  storiesNavigation: false,
  investigation: false,
  signalDetail: true,
};

describe("signal detail server gate", () => {
  beforeEach(() => {
    mocks.notFound.mockClear();
    mocks.readFeatureFlags.mockReset().mockReturnValue(enabled);
  });

  it("fails closed when the server-only signal flag is disabled", async () => {
    mocks.readFeatureFlags.mockReturnValue({ ...enabled, signalDetail: false });

    await expect(SignalDetailPage({ params: Promise.resolve({ id: "17" }) }))
      .rejects.toThrow("NEXT_NOT_FOUND");
    expect(mocks.notFound).toHaveBeenCalledOnce();
  });

  it("rejects malformed ids before rendering a client route", async () => {
    await expect(SignalDetailPage({ params: Promise.resolve({ id: "17oops" }) }))
      .rejects.toThrow("NEXT_NOT_FOUND");
    expect(mocks.notFound).toHaveBeenCalledOnce();
  });

  it("renders the client detail only for an enabled positive numeric id", async () => {
    render(await SignalDetailPage({ params: Promise.resolve({ id: "17" }) }));
    expect(screen.getByText("signal client 17")).toBeVisible();
    expect(mocks.notFound).not.toHaveBeenCalled();
  });
});
