import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FeatureFlagsProvider } from "@/components/FeatureFlagsProvider";
import { DEFAULT_FEATURE_FLAGS } from "@/lib/features";
import SignalsPage from "./page";

vi.mock("@/components/SiteHeader", () => ({ default: () => <header>Шапка</header> }));
vi.mock("@/components/SignalFeed", () => ({ default: () => null }));
vi.mock("@/lib/api", () => ({ api: { signals: async () => ({ signals: [] }) } }));

describe("SignalsPage navigation", () => {
  it("keeps the radar reachable from changes only while its feature is enabled", async () => {
    const { rerender } = render(<FeatureFlagsProvider flags={DEFAULT_FEATURE_FLAGS}><SignalsPage /></FeatureFlagsProvider>);
    expect(screen.getByRole("heading", { level: 1, name: "Изменения" })).toBeVisible();
    expect(screen.queryByRole("link", { name: /Радар перемен/ })).not.toBeInTheDocument();
    rerender(<FeatureFlagsProvider flags={{ ...DEFAULT_FEATURE_FLAGS, earlyWarningRadar: true }}><SignalsPage /></FeatureFlagsProvider>);
    expect(screen.getByRole("link", { name: /Радар перемен/ })).toHaveAttribute("href", "/radar");
  });
});
