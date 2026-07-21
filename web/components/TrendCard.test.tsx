import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RadarCountryWave, RadarTrend } from "@/lib/types";
import TrendCard from "./TrendCard";

function wave(country_code: string, index: number): RadarCountryWave {
  return {
    public_id: `wave-${index}`,
    country_code,
    contour: "media",
    state: "confirmed",
    confidence: 0.8,
    coverage_confidence: 0.8,
    velocity: 0.4,
    first_observed_at: "2026-07-15T10:00:00Z",
    detected_at: "2026-07-16T10:00:00Z",
    confirmed_at: "2026-07-17T10:00:00Z",
    t0_auto: "2026-07-15T10:00:00Z",
    t0_effective: "2026-07-15T10:00:00Z",
  };
}

describe("TrendCard", () => {
  it("summarizes countries and translates contour evidence", () => {
    const countries = ["ES", "PT", "FR", "DE", "IT", "PL", "FI", "SE", "NO", "DK", "CZ", "AT"];
    const trend: RadarTrend = {
      public_id: "trend-1",
      scope: "meta",
      state: "confirmed",
      thesis: "Торговые связи с Россией ослабевают",
      subject_key: "economy:trade:russia",
      direction: "decrease",
      confidence: 0.91,
      coverage_confidence: 0.82,
      velocity: -0.7,
      first_observed_at: "2026-07-15T10:00:00Z",
      detected_at: "2026-07-16T10:00:00Z",
      confirmed_at: "2026-07-17T10:00:00Z",
      t0_auto: "2026-07-15T10:00:00Z",
      t0_effective: "2026-07-15T10:00:00Z",
      country_code: null,
      country_count: 12,
      wave_count: 14,
      country_waves: countries.slice(0, 8).map(wave),
      contours: {
        media: { state: "emerging", status: "insufficient" },
        action: { state: "confirmed", status: "aligned" },
      },
      contradiction_marker: false,
      evidence_preview: null,
      why_included: "quality_gate",
    };

    render(<TrendCard trend={trend} />);

    expect(screen.getByText("12 стран")).toBeVisible();
    expect(screen.getByText("+7 стран")).toBeVisible();
    expect(screen.queryByText(/ES → PT →/)).not.toBeInTheDocument();
    expect(screen.getByText("медиа пока не подтверждает")).toBeVisible();
    expect(screen.getByText("действия подтверждают")).toBeVisible();
  });
});
