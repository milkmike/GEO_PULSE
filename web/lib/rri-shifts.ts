import type { RriHistoryPoint } from "./types";

export interface RriShiftMarker {
  fromDay: string;
  day: string;
  fromTime: string;
  time: string;
  fromScore: number;
  score: number;
  dailyDelta: number;
  at: string;
}

type MarkerInput = Pick<RriHistoryPoint, "day" | "time" | "score">;

function median(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

export function deriveRriShiftMarkers(history: MarkerInput[]): RriShiftMarker[] {
  const byDay = new Map<string, MarkerInput>();
  for (const point of history) {
    if (!point.day || !point.time || !Number.isFinite(point.score)) continue;
    const parsed = Date.parse(point.time);
    if (!Number.isFinite(parsed)) continue;
    const previous = byDay.get(point.day);
    if (!previous || Date.parse(previous.time) < parsed) byDay.set(point.day, point);
  }
  const points = [...byDay.values()].sort((a, b) =>
    a.day.localeCompare(b.day) || Date.parse(a.time) - Date.parse(b.time),
  );
  const candidates: RriShiftMarker[] = [];
  for (let index = 1; index < points.length; index += 1) {
    const previous = points[index - 1];
    const current = points[index];
    candidates.push({
      fromDay: previous.day,
      day: current.day,
      fromTime: previous.time,
      time: current.time,
      fromScore: previous.score,
      score: current.score,
      dailyDelta: current.score - previous.score,
      at: current.time,
    });
  }
  const magnitudes = candidates.map((marker) => Math.abs(marker.dailyDelta));
  if (!magnitudes.length) return [];
  const center = median(magnitudes);
  const mad = median(magnitudes.map((value) => Math.abs(value - center)));
  const threshold = Math.max(3, center + 2 * mad);
  return candidates
    .filter((marker) => Math.abs(marker.dailyDelta) >= threshold)
    .sort((a, b) => Math.abs(b.dailyDelta) - Math.abs(a.dailyDelta) || a.day.localeCompare(b.day))
    .slice(0, 6)
    .sort((a, b) => a.day.localeCompare(b.day));
}

export function validateInvestigationAt(value: string | null, history: MarkerInput[]): boolean {
  if (!value || !/(?:Z|[+-]\d{2}:\d{2})$/u.test(value)) return false;
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return false;
  const times = history
    .map((point) => Date.parse(point.time))
    .filter(Number.isFinite);
  if (times.length < 2) return false;
  return timestamp >= Math.min(...times) && timestamp <= Math.max(...times);
}
