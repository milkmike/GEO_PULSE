import { describe, expect, it } from "vitest";
import { deriveRriShiftMarkers, validateInvestigationAt } from "./rri-shifts";

describe("deriveRriShiftMarkers", () => {
  it("returns no marker for a stable series and never manufactures a largest event", () => {
    const history = [
      { day: "2026-07-10", time: "2026-07-10T18:00:00Z", score: 1 },
      { day: "2026-07-11", time: "2026-07-11T18:00:00Z", score: 2 },
      { day: "2026-07-12", time: "2026-07-12T18:00:00Z", score: 1.5 },
    ];

    expect(deriveRriShiftMarkers(history)).toEqual([]);
  });

  it("deduplicates days, ignores invalid values, caps robust outliers and restores chronology", () => {
    const scores = [0, 1, 2, 18, 19, 3, 4, 22, 23, 5, 30, 31];
    const history = scores.map((score, index) => ({
      day: `2026-07-${String(index + 1).padStart(2, "0")}`,
      time: `2026-07-${String(index + 1).padStart(2, "0")}T20:00:00Z`,
      score,
    }));
    history.push({ day: "2026-07-03", time: "2026-07-03T23:00:00Z", score: 2.5 });
    history.push({ day: "2026-07-13", time: "2026-07-13T20:00:00Z", score: Number.NaN });

    const markers = deriveRriShiftMarkers(history);

    expect(markers.length).toBeLessThanOrEqual(6);
    expect(markers.map((marker) => marker.day)).toEqual(
      [...markers.map((marker) => marker.day)].sort(),
    );
    expect(markers.every((marker) => Math.abs(marker.dailyDelta) >= 3)).toBe(true);
    expect(markers.every((marker) => marker.at === marker.time)).toBe(true);
    expect(markers.some((marker) => marker.day === "2026-07-04")).toBe(true);
  });

  it("uses the backend daily-last timestamp instead of synthesizing day end", () => {
    const markers = deriveRriShiftMarkers([
      { day: "2026-07-14", time: "2026-07-14T21:12:03.456Z", score: -10 },
      { day: "2026-07-15", time: "2026-07-15T19:45:11.123Z", score: -2 },
    ]);

    expect(markers[0]?.at).toBe("2026-07-15T19:45:11.123Z");
  });

  it("accepts only timezone-bearing timestamps inside the loaded exact history", () => {
    const history = [
      { day: "2026-07-14", time: "2026-07-14T18:00:00Z", score: -10 },
      { day: "2026-07-15", time: "2026-07-15T20:00:00+00:00", score: -2 },
    ];
    expect(validateInvestigationAt("2026-07-15T19:45:11.123Z", history)).toBe(true);
    expect(validateInvestigationAt("2026-07-15T19:45:11", history)).toBe(false);
    expect(validateInvestigationAt("2026-06-15T19:45:11Z", history)).toBe(false);
    expect(validateInvestigationAt("not-a-date", history)).toBe(false);
  });
});
