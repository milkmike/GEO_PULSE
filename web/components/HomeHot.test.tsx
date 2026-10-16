import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Brief, CountrySummary, Signal } from "@/lib/types";
import HomeHot, { briefHighlights, hotSignals, weeklyMovers } from "./HomeHot";

const NOW = Date.parse("2026-10-15T12:00:00Z");

function country(code: string, name: string, delta7d: number | null, score = 0, extra: Partial<CountrySummary> = {}): CountrySummary {
  return {
    code, name, name_en: name, iso3: `${code}X`, flag: "", region: "europe", tier: 2, score,
    structural: null, media: null, level: "neutral", delta_24h: 0, delta_7d: delta7d,
    article_count: null, gdelt_volume: null, gdelt_tone: null, updated_at: "2026-10-15T11:00:00Z", ...extra,
  };
}

function signal(id: number, title: string, createdAt: string, severity: Signal["severity"] = "critical", extra: Partial<Signal> = {}): Signal {
  return {
    id, type: "tone_shift", country_code: "GB", country_name: "Великобритания", severity, confidence: 0.9,
    title, description: null, payload: null, created_at: createdAt, expires_at: null, active: true, ...extra,
  };
}

const brief: Brief = {
  content: "## Главное\n\n* **Босния и Герцеговина:** индекс −41,6 за сутки.\n\n## Эскалация\n\n* **Япония:** арест журналиста.",
  model: "qwen/qwen3.7-plus",
  created_at: "2026-10-15T11:59:42Z",
};

describe("HomeHot helpers", () => {
  it("takes the «Главное» section of the world brief", () => {
    expect(briefHighlights(brief.content)).toBe("## Главное\n\n* **Босния и Герцеговина:** индекс −41,6 за сутки.");
    expect(briefHighlights("Просто текст без разделов")).toBe("Просто текст без разделов");
  });

  it("keeps the newest copy of repeated signals, drops expired ones and ranks by severity", () => {
    const result = hotSignals([
      signal(1, "Сдвиг тона о России: Великобритания (похолодел)", "2026-10-13T00:14:00Z"),
      signal(2, "Сдвиг тона о России: Великобритания (похолодел)", "2026-10-15T00:21:00Z"),
      signal(3, "Всплеск внимания к России: Сербия", "2026-10-15T09:00:00Z", "warning", { country_code: "RS" }),
      signal(4, "Старый сигнал", "2026-10-10T00:00:00Z"),
      signal(5, "Истёкший сигнал", "2026-10-15T08:00:00Z", "critical", { active: false }),
    ], NOW);
    expect(result.map((item) => item.id)).toEqual([2, 3]);
  });

  it("splits weekly movers into cooled and warmed, strongest first", () => {
    const { cooled, warmed } = weeklyMovers([
      country("BA", "Босния и Герцеговина", -41.6), country("RS", "Сербия", -11.3),
      country("ID", "Индонезия", 18.2), country("MA", "Марокко", 13.3), country("FR", "Франция", 0.4),
      country("XX", "Без данных", null),
    ]);
    expect(cooled.map((c) => c.code)).toEqual(["BA", "RS"]);
    expect(warmed.map((c) => c.code)).toEqual(["ID", "MA"]);
  });

  it("uses the API low_data flag before the older article and volume heuristics", () => {
    const { warmed, cooled } = weeklyMovers([
      country("BA", "Босния", -10, 4, { low_data: true, article_count: 20 }),
      country("BR", "Бразилия", 10, 4, { low_data: false, article_count: 1 }),
      country("NZ", "Новая Зеландия", 12, 4, { low_data: false, media: 0, gdelt_volume: 0 }),
    ]);
    expect(cooled).toEqual([]);
    expect(warmed.map((c) => c.code)).toEqual(["NZ", "BR"]);
  });

  it("leaves out moves whose media layer rests on one or two articles or an empty GDELT reading", () => {
    const { cooled, warmed } = weeklyMovers([
      country("BA", "Босния и Герцеговина", -45, -28.4, { media: -66.7, article_count: 1 }),
      country("BR", "Бразилия", -19, -4.9, { media: -33.3, article_count: 2 }),
      country("AE", "ОАЭ", -13, 20.1, { media: 20.6, article_count: 3 }),
      country("NZ", "Новая Зеландия", 12.6, -18.8, { media: 0, gdelt_volume: 0, gdelt_tone: 0 }),
      country("VN", "Вьетнам", 8, 12.7, { media: 9.3, gdelt_volume: 0.4, gdelt_tone: 0.8 }),
      country("MN", "Монголия", 5, 5.8),
    ]);
    expect(cooled.map((c) => c.code)).toEqual(["AE"]);
    expect(warmed.map((c) => c.code)).toEqual(["VN", "MN"]);
  });
});

describe("HomeHot", () => {
  it("shows the day's highlights with their date and expands the whole brief", () => {
    render(<HomeHot brief={brief} countries={[country("BA", "Босния и Герцеговина", -41.6)]} signals={[]} now={NOW} />);
    const card = screen.getByRole("region", { name: "Главное за сутки" });
    expect(within(card).getByText(/индекс −41,6 за сутки/)).toBeVisible();
    expect(within(card).queryByText(/арест журналиста/)).not.toBeInTheDocument();
    expect(within(card).getByText(/Написано ИИ/)).toHaveAttribute("title", "qwen/qwen3.7-plus");
    fireEvent.click(within(card).getByRole("button", { name: "Вся сводка" }));
    expect(within(card).getByText(/арест журналиста/)).toBeVisible();
  });

  it("hides a brief older than a week and links movers and signals to their pages", () => {
    render(
      <HomeHot
        brief={{ ...brief, created_at: "2026-06-12T20:07:47Z" }}
        countries={[country("BA", "Босния и Герцеговина", -41.6, -25)]}
        signals={[signal(7, "Сдвиг тона о России: Великобритания (похолодел)", "2026-10-15T00:21:00Z")]}
        signalDetail
        now={NOW}
      />,
    );
    expect(screen.queryByRole("region", { name: "Главное за сутки" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Босния и Герцеговина/ })).toHaveAttribute("href", "/country/BA");
    expect(screen.getByRole("link", { name: /Великобритания \(похолодел\)/ })).toHaveAttribute("href", "/signals/7");
  });

  it("renders nothing when there is nothing hot", () => {
    const { container } = render(<HomeHot brief={null} countries={[country("FR", "Франция", 0.2)]} signals={[]} now={NOW} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders nothing when the only weekly moves rest on a single article", () => {
    const { container } = render(
      <HomeHot brief={null} countries={[country("BA", "Босния и Герцеговина", -45, -28.4, { media: -66.7, article_count: 1 })]} signals={[]} now={NOW} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
