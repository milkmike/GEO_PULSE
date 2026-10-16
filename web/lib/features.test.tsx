import fs from "node:fs";
import path from "node:path";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import SiteHeader from "@/components/SiteHeader";
import { FeatureFlagsProvider } from "@/components/FeatureFlagsProvider";
import { readFeatureFlags } from "./features.server";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("@/components/HealthBadge", () => ({ default: () => null }));

describe("server-only feature flags", () => {
  it("defaults every missing, false, or malformed value off", () => {
    expect(readFeatureFlags({})).toEqual({
      searchNavigation: false,
      storiesNavigation: false,
      investigation: false,
      signalDetail: false,
      earlyWarningRadar: false,
    });
    expect(readFeatureFlags({
      FEATURE_SEARCH_NAVIGATION: "false",
      FEATURE_STORIES_NAVIGATION: "1",
      FEATURE_INVESTIGATION: "yes",
      FEATURE_SIGNAL_DETAIL: "TRUE ",
      FEATURE_EARLY_WARNING_RADAR: "yes",
    })).toEqual({
      searchNavigation: false,
      storiesNavigation: false,
      investigation: false,
      signalDetail: false,
      earlyWarningRadar: false,
    });
  });

  it("enables each value only for the exact literal true", () => {
    expect(readFeatureFlags({
      FEATURE_SEARCH_NAVIGATION: "true",
      FEATURE_STORIES_NAVIGATION: "true",
      FEATURE_INVESTIGATION: "true",
      FEATURE_SIGNAL_DETAIL: "true",
      FEATURE_EARLY_WARNING_RADAR: "true",
    })).toEqual({
      searchNavigation: true,
      storiesNavigation: true,
      investigation: true,
      signalDetail: true,
      earlyWarningRadar: true,
    });
  });

  it("gates only the new navigation links from a client-side snapshot", () => {
    const { rerender } = render(
      <FeatureFlagsProvider flags={{ searchNavigation: false, storiesNavigation: false, investigation: false, signalDetail: false, earlyWarningRadar: false }}>
        <SiteHeader />
      </FeatureFlagsProvider>,
    );
    expect(screen.queryByRole("button", { name: /Поиск/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Сюжеты" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "О данных" })).toHaveAttribute("href", "/about");

    rerender(
      <FeatureFlagsProvider flags={{ searchNavigation: true, storiesNavigation: true, investigation: false, signalDetail: false, earlyWarningRadar: true }}>
        <SiteHeader />
      </FeatureFlagsProvider>,
    );
    expect(screen.getByRole("button", { name: /Поиск/ })).toBeVisible();
    expect(screen.getByRole("link", { name: "Сюжеты" })).toHaveAttribute("href", "/stories");
    expect(screen.queryByRole("link", { name: /Радар/ })).not.toBeInTheDocument();
  });

  it("keeps the environment reader out of the client graph and passes all flags through the image build", () => {
    const webRoot = path.resolve(__dirname, "..");
    const repoRoot = path.resolve(webRoot, "..");
    const reader = fs.readFileSync(path.join(webRoot, "lib/features.server.ts"), "utf8");
    const header = fs.readFileSync(path.join(webRoot, "components/SiteHeader.tsx"), "utf8");
    const layout = fs.readFileSync(path.join(webRoot, "app/layout.tsx"), "utf8");
    const dockerfile = fs.readFileSync(path.join(webRoot, "Dockerfile"), "utf8");
    const compose = fs.readFileSync(path.join(repoRoot, "docker-compose.yml"), "utf8");

    expect(reader).toContain('import "server-only"');
    expect(header).not.toContain("features.server");
    expect(layout).toContain("readFeatureFlags");
    expect(layout).toContain("FeatureFlagsProvider");
    for (const flag of [
      "FEATURE_SEARCH_NAVIGATION", "FEATURE_STORIES_NAVIGATION",
      "FEATURE_INVESTIGATION", "FEATURE_SIGNAL_DETAIL",
      "FEATURE_EARLY_WARNING_RADAR",
    ]) {
      expect(dockerfile).toContain(`ARG ${flag}=false`);
      expect(compose).toContain(`${flag}: \${${flag}:-false}`);
    }
    expect(`${reader}\n${header}\n${layout}\n${dockerfile}\n${compose}`).not.toMatch(/NEXT_PUBLIC_FEATURE_/);
  });
});
