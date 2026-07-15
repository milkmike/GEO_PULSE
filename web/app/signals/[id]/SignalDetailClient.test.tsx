import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SignalDetail } from "@/lib/types";
import { FeatureFlagsProvider } from "@/components/FeatureFlagsProvider";
import SignalDetailClient from "./SignalDetailClient";

const apiMocks = vi.hoisted(() => ({ signalDetail: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: apiMocks }));
vi.mock("@/components/SiteHeader", () => ({ default: () => <nav>header</nav> }));
vi.mock("@/components/SignalEvidence", () => ({
  default: ({ detail, storiesEnabled }: { detail: SignalDetail; storiesEnabled: boolean }) => (
    <div>evidence {detail.id} stories {String(storiesEnabled)}</div>
  ),
}));

const detail = { id: 17 } as SignalDetail;
const flags = {
  searchNavigation: false,
  storiesNavigation: true,
  investigation: false,
  signalDetail: true,
};

function view(signalId = 17) {
  return render(
    <FeatureFlagsProvider flags={flags}>
      <SignalDetailClient signalId={signalId} />
    </FeatureFlagsProvider>,
  );
}

describe("SignalDetailClient", () => {
  beforeEach(() => apiMocks.signalDetail.mockReset().mockResolvedValue(detail));

  it("loads one evidence dossier and passes the story flag through", async () => {
    view();
    expect(screen.getByRole("status")).toHaveTextContent(/загружаем доказательства/i);
    expect(await screen.findByText("evidence 17 stories true")).toBeVisible();
    expect(apiMocks.signalDetail).toHaveBeenCalledTimes(1);
    expect(apiMocks.signalDetail.mock.calls[0][0]).toBe(17);
    expect(apiMocks.signalDetail.mock.calls[0][1]).toBeInstanceOf(AbortSignal);
  });

  it("shows an honest recoverable error", async () => {
    const user = userEvent.setup();
    apiMocks.signalDetail.mockRejectedValueOnce(new Error("503"));
    view();
    expect(await screen.findByRole("alert")).toHaveTextContent(/не удалось загрузить/i);

    apiMocks.signalDetail.mockResolvedValueOnce(detail);
    await user.click(screen.getByRole("button", { name: /повторить/i }));
    expect(await screen.findByText("evidence 17 stories true")).toBeVisible();
    expect(apiMocks.signalDetail).toHaveBeenCalledTimes(2);
  });

  it("aborts stale signal requests when the route id changes", async () => {
    let resolveFirst!: (value: SignalDetail) => void;
    const first = new Promise<SignalDetail>((resolve) => { resolveFirst = resolve; });
    apiMocks.signalDetail.mockReturnValueOnce(first).mockResolvedValueOnce({ ...detail, id: 18 });
    const { rerender } = view();
    const firstSignal = apiMocks.signalDetail.mock.calls[0][1] as AbortSignal;

    rerender(
      <FeatureFlagsProvider flags={flags}>
        <SignalDetailClient signalId={18} />
      </FeatureFlagsProvider>,
    );
    expect(await screen.findByText("evidence 18 stories true")).toBeVisible();
    expect(firstSignal.aborted).toBe(true);
    resolveFirst(detail);
    await waitFor(() => expect(screen.queryByText("evidence 17 stories true")).not.toBeInTheDocument());
  });
});
