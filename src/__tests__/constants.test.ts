import { describe, it, expect } from "vitest";
import {
  COUNTRY_CODES,
  COUNTRY_FLAGS,
  COUNTRY_NAMES,
  TIER_CHART_COLORS,
  TIER_BADGE_CLASSES,
  TIER_LABELS,
  PHASE_CONFIG,
  PHASE_ORDER,
  sentimentColor,
  sentimentLabel,
  formatDate,
  temperatureColor,
} from "@/lib/constants";

describe("constants", () => {
  it("has 10 countries", () => {
    expect(COUNTRY_CODES).toHaveLength(10);
  });

  it("every country code has a flag and name", () => {
    for (const code of COUNTRY_CODES) {
      expect(COUNTRY_FLAGS[code]).toBeDefined();
      expect(COUNTRY_NAMES[code]).toBeDefined();
      expect(COUNTRY_FLAGS[code]).toMatch(/./u); // emoji
    }
  });

  it("TIER_CHART_COLORS has hex values", () => {
    for (const [, color] of Object.entries(TIER_CHART_COLORS)) {
      expect(color).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("TIER_BADGE_CLASSES has tailwind classes", () => {
    for (const [, cls] of Object.entries(TIER_BADGE_CLASSES)) {
      expect(cls).toContain("bg-");
      expect(cls).toContain("text-");
    }
  });

  it("TIER_LABELS maps to Russian strings", () => {
    expect(Object.keys(TIER_LABELS).length).toBeGreaterThan(5);
    expect(TIER_LABELS.official).toBe("Официальные");
  });

  it("PHASE_CONFIG has 5 phases", () => {
    expect(Object.keys(PHASE_CONFIG)).toHaveLength(5);
    expect(PHASE_ORDER).toHaveLength(5);
    for (const phase of PHASE_ORDER) {
      expect(PHASE_CONFIG[phase]).toBeDefined();
      expect(PHASE_CONFIG[phase].emoji).toBeTruthy();
      expect(PHASE_CONFIG[phase].label).toBeTruthy();
    }
  });

  it("sentimentColor returns correct colors", () => {
    expect(sentimentColor(0.5)).toBe("#22c55e");  // positive
    expect(sentimentColor(0)).toBe("#94a3b8");     // neutral
    expect(sentimentColor(-0.5)).toBe("#ef4444");  // negative
  });

  it("sentimentLabel returns Russian labels", () => {
    expect(sentimentLabel(0.5)).toBe("Позитивный");
    expect(sentimentLabel(0)).toBe("Нейтральный");
    expect(sentimentLabel(-0.5)).toBe("Негативный");
  });

  it("formatDate handles valid dates", () => {
    const result = formatDate("2025-06-15T12:00:00Z");
    expect(result).toContain("15");
    expect(result).toContain("2025");
  });

  it("formatDate handles null/undefined", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate(undefined)).toBe("—");
  });

  it("temperatureColor returns valid hex", () => {
    expect(temperatureColor(80)).toMatch(/^#[0-9a-f]{6}$/i);
    expect(temperatureColor(20)).toMatch(/^#[0-9a-f]{6}$/i);
  });
});
