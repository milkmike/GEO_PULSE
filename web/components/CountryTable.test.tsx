import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CountrySummary, Level } from "@/lib/types";
import CountryTable from "./CountryTable";

function country(
  code: string,
  name: string,
  score: number,
  level: Level,
  delta_7d: number | null,
  extra: Partial<CountrySummary> = {},
): CountrySummary {
  return {
    code,
    name,
    name_en: name,
    iso3: `${code}X`,
    flag: "🏳️",
    region: "asia",
    tier: 2,
    score,
    structural: null,
    media: null,
    level,
    delta_24h: null,
    delta_7d,
    article_count: null,
    gdelt_volume: null,
    gdelt_tone: null,
    updated_at: "2026-10-15T00:00:00Z",
    ...extra,
  };
}

const sample: CountrySummary[] = [
  country("DE", "Германия", -40, "tension", -1.5, { region: "europe" }),
  country("CN", "Китай", 60, "ally", 2, { delta_24h: 0.4 }),
  country("IN", "Индия", 35, "partner", null),
  country("PL", "Польша", -75, "hostile", -6.2, { region: "europe" }),
  country("TR", "Турция", 5, "neutral", 0.8),
];

const regions = { asia: "Азия", europe: "Европа" };

/** Country page links in rendered row order. */
const order = () =>
  within(screen.getByRole("table")).getAllByRole("link").map((a) => a.getAttribute("href"));

describe("CountryTable", () => {
  it("sorts by index descending by default and links rows to country pages", () => {
    render(<CountryTable countries={sample} regions={regions} />);
    expect(order()).toEqual(["/country/CN", "/country/IN", "/country/TR", "/country/DE", "/country/PL"]);
    expect(screen.getByRole("link", { name: "Китай" })).toHaveAttribute("href", "/country/CN");
    expect(screen.getByText("5 стран")).toBeInTheDocument();
  });

  it("puts the strongest weekly cooling first and countries without a weekly delta last", async () => {
    const user = userEvent.setup();
    render(<CountryTable countries={sample} regions={regions} />);
    await user.selectOptions(screen.getByLabelText("Сортировка"), "Сильнее похолодели за неделю");
    expect(order()).toEqual(["/country/PL", "/country/DE", "/country/TR", "/country/CN", "/country/IN"]);

    await user.selectOptions(screen.getByLabelText("Сортировка"), "Сильнее потеплели за неделю");
    expect(order()).toEqual(["/country/CN", "/country/TR", "/country/DE", "/country/PL", "/country/IN"]);
  });

  it("filters by a case-insensitive name substring and reports when nothing matches", async () => {
    const user = userEvent.setup();
    render(<CountryTable countries={sample} regions={regions} />);
    const search = screen.getByLabelText("Найти страну");

    await user.type(search, "ИТА");
    expect(order()).toEqual(["/country/CN"]);
    expect(screen.getByText("1 страна")).toBeInTheDocument();

    await user.clear(search);
    await user.type(search, "атлантида");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText("Ничего не найдено")).toBeInTheDocument();
  });

  it("filters by region and hides the region select when regions are not provided", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<CountryTable countries={sample} regions={regions} />);
    await user.selectOptions(screen.getByLabelText("Регион"), "Европа");
    expect(order()).toEqual(["/country/DE", "/country/PL"]);
    expect(screen.getByText("2 страны")).toBeInTheDocument();
    unmount();

    render(<CountryTable countries={sample} />);
    expect(screen.queryByLabelText("Регион")).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Все регионы" })).not.toBeInTheDocument();
  });

  it("shows 25 rows first and reveals the rest only when the list is longer", async () => {
    const user = userEvent.setup();
    const many = Array.from({ length: 31 }, (_, i) =>
      country(`C${i}`, `Страна ${i}`, 50 - i, "neutral", null, { region: i < 10 ? "europe" : "asia" }));
    render(<CountryTable countries={many} regions={regions} />);

    expect(within(screen.getByRole("table")).getAllByRole("link")).toHaveLength(25);
    const more = screen.getByRole("button", { name: "Показать все 31 страну" });
    await user.click(more);
    expect(within(screen.getByRole("table")).getAllByRole("link")).toHaveLength(31);
    expect(screen.queryByRole("button", { name: /Показать все/ })).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Регион"), "Европа");
    expect(within(screen.getByRole("table")).getAllByRole("link")).toHaveLength(10);
    expect(screen.queryByRole("button", { name: /Показать все/ })).not.toBeInTheDocument();
  });

  it("labels levels in Russian and colours deltas by sign", () => {
    render(<CountryTable countries={sample} regions={regions} />);
    const poland = screen.getByRole("link", { name: "Польша" }).closest("tr")!;
    expect(within(poland).getByText("враждебность")).toBeInTheDocument();
    expect(within(poland).queryByText("hostile")).not.toBeInTheDocument();
    expect(within(poland).getByText("-6.2")).toHaveClass("text-hostile");

    const china = screen.getByRole("link", { name: "Китай" }).closest("tr")!;
    expect(within(china).getByText("союзник")).toBeInTheDocument();
    expect(within(china).getByText("+2.0")).toHaveClass("text-partner");
    expect(within(china).getByText("+0.4")).toHaveClass("text-partner");

    const india = screen.getByRole("link", { name: "Индия" }).closest("tr")!;
    expect(within(india).getAllByText("нет данных")).toHaveLength(2);
  });
});
