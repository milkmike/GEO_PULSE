import { render, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Plot, { type PlotClickPoint } from "./Plot";

const plotly = vi.hoisted(() => ({
  click: null as null | ((event: { points?: PlotClickPoint[] }) => void),
  react: vi.fn(async (element: HTMLElement) => {
    Object.assign(element, {
      on: (_event: string, callback: (event: { points?: PlotClickPoint[] }) => void) => { plotly.click = callback; },
      removeAllListeners: () => { plotly.click = null; },
    });
  }),
  purge: vi.fn(),
  resize: vi.fn(async () => {}),
}));

vi.mock("plotly.js-dist-min", () => ({ default: { react: plotly.react, purge: plotly.purge, Plots: { resize: plotly.resize } } }));
afterEach(() => vi.unstubAllGlobals());

describe("Plot click contract", () => {
  it("resizes after a disclosure changes its container and disconnects on unmount", async () => {
    let resized: ResizeObserverCallback;
    const observe = vi.fn();
    const disconnect = vi.fn();
    vi.stubGlobal("ResizeObserver", class {
      constructor(callback: ResizeObserverCallback) { resized = callback; }
      observe = observe;
      disconnect = disconnect;
    });
    const { unmount } = render(<Plot data={[]} layout={{}} />);
    await waitFor(() => expect(observe).toHaveBeenCalled());
    resized!([{ contentRect: { width: 400, height: 280 } } as ResizeObserverEntry], {} as ResizeObserver);
    expect(plotly.resize).toHaveBeenCalledWith(observe.mock.calls[0][0]);
    unmount();
    expect(disconnect).toHaveBeenCalled();
  });
  it("preserves map locations and investigation customdata", async () => {
    const onClick = vi.fn((point: PlotClickPoint) => point.customdata);
    render(<Plot data={[]} layout={{}} onClick={onClick} />);
    await waitFor(() => expect(plotly.click).not.toBeNull());

    plotly.click?.({ points: [{ location: "ESP", customdata: "2026-07-15T20:00:00Z", x: "2026-07-15", y: -2, pointIndex: 3 }] });

    expect(onClick).toHaveBeenCalledWith(expect.objectContaining({
      location: "ESP",
      customdata: "2026-07-15T20:00:00Z",
    }));
  });
});
