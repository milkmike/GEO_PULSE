import { render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
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
}));

vi.mock("plotly.js-dist-min", () => ({ default: { react: plotly.react, purge: plotly.purge } }));

describe("Plot click contract", () => {
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
