import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CountrySummary } from "@/lib/types";
import RelationsThermometer from "./RelationsThermometer";

const bosnia: CountrySummary = {
  code: "BA", name: "Босния и Герцеговина", name_en: "Bosnia", iso3: "BIH", flag: "🇧🇦",
  region: "europe", tier: 2, score: 3.96, structural: 5.4, media: -66.7, level: "neutral",
  delta_24h: -1.44, delta_7d: -1.44, article_count: 1, gdelt_volume: null, gdelt_tone: null,
  updated_at: "2026-10-15T12:00:00Z", low_data: true, media_confidence: .05,
};

describe("RelationsThermometer evidence", () => {
  it("marks sparse evidence with an explanation and a muted value", () => {
    render(<RelationsThermometer countries={[bosnia]} />);
    expect(screen.getByText("мало данных")).toHaveAttribute("title",
      "индекс держится в основном на позиции страны: о России там почти не пишут");
    expect(screen.getByText("+4.0")).toHaveStyle({ color: "var(--color-dim)" });
    expect(screen.getByRole("link", { name: /Босния/ })).toHaveAttribute("href", "/country/BA");
  });

  it("leaves full evidence and older API responses without a badge", () => {
    render(<RelationsThermometer countries={[{ ...bosnia, low_data: undefined }]} />);
    expect(screen.queryByText("мало данных")).not.toBeInTheDocument();
  });
});
