import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { CountrySummary } from "@/lib/types";
import CountriesPage from "./page";

const mocks = vi.hoisted(() => ({ countries: vi.fn(), meta: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: mocks }));
vi.mock("@/components/SiteHeader", () => ({ default: () => <header>Шапка</header> }));
const country = (code: string, name: string, score: number, region: string, delta: number | null, low_data = false): CountrySummary => ({
  code, name, score, region, delta_7d: delta, low_data, level: "neutral", flag: "🏳️", name_en: name,
  iso3: code, tier: 2, structural: null, media: null, delta_24h: null, article_count: null,
  gdelt_volume: null, gdelt_tone: null, updated_at: "2026-10-15T14:00:00Z",
});
const countries = [country("RS", "Сербия", 5, "balkans", 1), country("CN", "Китай", 45, "east_asia", null), country("BA", "Босния и Герцеговина", -10, "balkans", -4, true)];
const rowNames = () => screen.getAllByRole("row").slice(1).map((row) => within(row).getByRole("link").textContent);

beforeEach(() => {
  mocks.countries.mockReset().mockResolvedValue({ countries });
  mocks.meta.mockReset().mockResolvedValue({ regions: { balkans: "Балканы", east_asia: "Восточная Азия" } });
});

describe("CountriesPage", () => {
  it("lists dossiers by descending index and reverses sorting accessibly", async () => {
    const user = userEvent.setup();
    render(<CountriesPage />);
    expect(await screen.findByRole("link", { name: "Китай" })).toHaveAttribute("href", "/country/CN");
    expect(rowNames()).toEqual(["🏳️Китай", "🏳️Сербия", "🏳️Босния и Герцеговина"]);
    expect(screen.getByRole("columnheader", { name: "Индекс" })).toHaveAttribute("aria-sort", "descending");
    await user.click(screen.getByRole("button", { name: "Индекс" }));
    expect(rowNames()).toEqual(["🏳️Босния и Герцеговина", "🏳️Сербия", "🏳️Китай"]);
    expect(screen.getByRole("columnheader", { name: "Индекс" })).toHaveAttribute("aria-sort", "ascending");
    await user.click(screen.getByRole("button", { name: "Страна" }));
    expect(rowNames()[0]).toBe("🏳️Босния и Герцеговина");
    await user.click(screen.getByRole("button", { name: "За неделю" }));
    expect(rowNames()).toEqual(["🏳️Сербия", "🏳️Босния и Герцеговина", "🏳️Китай"]);
    await user.click(screen.getByRole("button", { name: "Мало данных" }));
    expect(rowNames()[0]).toBe("🏳️Босния и Герцеговина");
    expect(screen.getByText("мало данных")).toBeVisible();
    expect(screen.getAllByText("нейтралитет")).toHaveLength(3);
  });

  it("orders levels by their index meaning", async () => {
    const user = userEvent.setup();
    mocks.countries.mockResolvedValue({ countries: [
      { ...countries[0], level: "hostile" },
      { ...countries[1], level: "ally" },
      countries[2],
    ] });
    render(<CountriesPage />);
    await screen.findByRole("link", { name: "Сербия" });
    await user.click(screen.getByRole("button", { name: "Уровень" }));
    expect(rowNames()).toEqual(["🏳️Китай", "🏳️Босния и Герцеговина", "🏳️Сербия"]);
    await user.click(screen.getByRole("button", { name: "Уровень" }));
    expect(rowNames()).toEqual(["🏳️Сербия", "🏳️Босния и Герцеговина", "🏳️Китай"]);
  });

  it("shows all 99 countries without truncating the directory", async () => {
    mocks.countries.mockResolvedValue({ countries: Array.from({ length: 99 }, (_, index) => ({ ...countries[0], code: `C${index}`, name: `Страна ${index + 1}` })) });
    render(<CountriesPage />);
    await screen.findByRole("link", { name: /^Страна 99$/ });
    expect(screen.getAllByRole("row")).toHaveLength(100);
  });

  it("filters by metadata regions and restores all countries", async () => {
    const user = userEvent.setup();
    render(<CountriesPage />);
    await screen.findByRole("link", { name: "Сербия" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Регион" }), "balkans");
    expect(screen.queryByRole("link", { name: "Китай" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("row")).toHaveLength(3);
    await user.selectOptions(screen.getByRole("combobox", { name: "Регион" }), "");
    expect(screen.getByRole("link", { name: "Китай" })).toBeVisible();
  });

  it("uses country regions if metadata is unavailable", async () => {
    mocks.meta.mockRejectedValue(new Error("offline"));
    render(<CountriesPage />);
    await screen.findByRole("link", { name: "Сербия" });
    expect(screen.getByRole("option", { name: "Восточная Азия" })).toHaveValue("east_asia");
    expect(screen.getAllByRole("option")).toHaveLength(3);
  });

  it("distinguishes an unavailable list from an empty list and retries", async () => {
    const user = userEvent.setup();
    mocks.countries.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce({ countries: [] });
    render(<CountriesPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить страны");
    await user.click(screen.getByRole("button", { name: "Повторить загрузку" }));
    await waitFor(() => expect(screen.getByText("В этом регионе стран нет.")).toBeVisible());
  });
});
