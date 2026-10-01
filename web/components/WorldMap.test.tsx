import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import WorldMap from "./WorldMap";
import type { PlotProps } from "./Plot";

const mocks = vi.hoisted(() => ({ push: vi.fn(), data: [] as unknown[] }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("./Plot", () => ({ default: (props: PlotProps) => {
  mocks.data = props.data;
  return <button onClick={() => props.onClick?.({ location: "SRB" })}>Выбрать Сербию на карте</button>;
} }));
const entries = [{ iso3: "SRB", code: "RS", name: "Сербия", score: 4, level: "neutral" as const, delta_24h: 1 }];

describe("WorldMap country selection", () => {
  beforeEach(() => mocks.push.mockReset());

  it("selects in the workspace without leaving the page when a handler is provided", () => {
    const select = vi.fn();
    render(<WorldMap entries={entries} selectedCountry="RS" onSelectCountry={select} />);
    fireEvent.click(screen.getByRole("button", { name: "Выбрать Сербию на карте" }));
    expect(select).toHaveBeenCalledWith("RS");
    expect(mocks.push).not.toHaveBeenCalled();
    expect(mocks.data).toEqual(expect.arrayContaining([expect.objectContaining({
      locations: ["SRB"], showscale: false, marker: { line: { color: "#f0eee8", width: 2 } },
    })]));
  });

  it("retains dossier navigation on existing standalone maps", () => {
    render(<WorldMap entries={entries} />);
    fireEvent.click(screen.getByRole("button", { name: "Выбрать Сербию на карте" }));
    expect(mocks.push).toHaveBeenCalledWith("/country/RS");
  });
});
