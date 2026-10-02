import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { GlobalCoverageResponse } from "@/lib/globalMonitoringTypes";
import GlobalCoverageDisclosure from "./GlobalCoverageDisclosure";

const mocks = vi.hoisted(() => ({ globalCoverage: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: mocks }));

const coverage: GlobalCoverageResponse = {
  as_of: "2026-10-06T09:00:00Z", status: "ok", scope_count: 3,
  countries: [
    { code: "RS", name_ru: "Сербия", configured_sources: 4, working_direct_publishers: 2, sampled_articles_7d: 7, latest_local_published_at: "2026-10-05T08:00:00Z", latest_local_collected_at: "2026-10-06T08:00:00Z", coverage_state: "sampled", work: {} },
    { code: "AE", name_ru: "Объединённые Арабские Эмираты", configured_sources: 2, working_direct_publishers: 1, sampled_articles_7d: 0, latest_local_published_at: null, latest_local_collected_at: null, coverage_state: "quiet", work: {} },
    { code: "PW", name_ru: "Палау", configured_sources: 0, working_direct_publishers: 0, sampled_articles_7d: 0, latest_local_published_at: null, latest_local_collected_at: null, coverage_state: "no_sources", work: {} },
  ],
  limits: { per_country: 40, window_days: 7, counts_are_bounded: true },
  screening: { status: "blocked", reason: "provider_error" }, writer: { status: "disabled" },
  source_research: { status: "ok", queue: { lead_count: 3, countries: { AD: { queued: 3 } }, totals: { queued: 3 } } },
  notice: "Выборка показывает только часть местных публикаций.",
};

beforeEach(() => { mocks.globalCoverage.mockReset(); mocks.globalCoverage.mockResolvedValue(coverage); });

describe("GlobalCoverageDisclosure", () => {
  it("shows a bounded collection summary and searchable country gaps inside the disclosure", async () => {
    render(<GlobalCoverageDisclosure />);
    const summary = await screen.findByText("Какой мир мы видим");
    expect(summary.closest("summary")).toHaveTextContent("1 из 3 стран и территорий · пробелы по 2");
    const disclosure = summary.closest("details")!;
    expect(disclosure).not.toHaveAttribute("open");
    fireEvent.click(summary);
    expect(within(disclosure).getByText(/Срез сбора/)).toHaveTextContent("мск");
    expect(within(disclosure).getByText("Автоматический разбор новых материалов приостановлен. Сбор источников продолжается.")).toBeVisible();
    expect(within(disclosure).getByText("Поиск местных источников: найдено 3 кандидата для проверки.")).toBeVisible();
    expect(within(disclosure).getByRole("columnheader", { name: "Публикаций в выборке (до 40)" })).toBeVisible();
    expect(within(disclosure).getByRole("columnheader", { name: "Местных СМИ доступно" })).toBeVisible();
    expect(within(disclosure).getByText("Доступность — успешный опрос за последние 72 часа; публикации могут выходить реже.")).toBeVisible();
    fireEvent.change(within(disclosure).getByRole("searchbox", { name: "Найти страну или территорию" }), { target: { value: "палау" } });
    expect(within(disclosure).getByRole("rowheader", { name: "Палау" })).toBeVisible();
    expect(within(disclosure).getByText("Источники не настроены")).toBeVisible();
    expect(within(disclosure).queryByRole("rowheader", { name: "Сербия" })).not.toBeInTheDocument();
    fireEvent.change(within(disclosure).getByRole("searchbox", { name: "Найти страну или территорию" }), { target: { value: "" } });
    fireEvent.click(within(disclosure).getByRole("checkbox", { name: "Показать пробелы" }));
    expect(within(disclosure).queryByRole("rowheader", { name: "Сербия" })).not.toBeInTheDocument();
    expect(within(disclosure).getAllByRole("rowheader")).toHaveLength(2);
    fireEvent.change(within(disclosure).getByRole("searchbox", { name: "Найти страну или территорию" }), { target: { value: "Сербия" } });
    expect(within(disclosure).getByText("Среди пробелов по этому запросу стран нет.")).toBeVisible();
  });

  it("offers a country-specific signals action and reports monitoring names", async () => {
    const onSelect = vi.fn();
    const onCountriesLoaded = vi.fn();
    render(<GlobalCoverageDisclosure onSelectSignalCountry={onSelect} onCountriesLoaded={onCountriesLoaded} />);
    fireEvent.click(await screen.findByText("Какой мир мы видим"));
    expect(onCountriesLoaded).toHaveBeenCalledWith(coverage.countries);
    fireEvent.click(screen.getByRole("button", { name: "Ранние сигналы: Палау" }));
    expect(onSelect).toHaveBeenCalledWith("PW");
  });

  it("hides a not-started snapshot and offers a small retry when the first request fails", async () => {
    mocks.globalCoverage.mockResolvedValueOnce({ ...coverage, status: "not_started", countries: [] }).mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(coverage);
    const view = render(<GlobalCoverageDisclosure />);
    await act(async () => {});
    expect(screen.queryByText("Какой мир мы видим")).not.toBeInTheDocument();
    view.rerender(<GlobalCoverageDisclosure refreshToken={1} />);
    await act(async () => {});
    expect(screen.queryByText("Какой мир мы видим")).not.toBeInTheDocument();
    // A failed refresh of a not-started snapshot stays quiet until the next periodic check.
    view.unmount();
    mocks.globalCoverage.mockReset();
    mocks.globalCoverage.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(coverage);
    render(<GlobalCoverageDisclosure />);
    fireEvent.click(await screen.findByRole("button", { name: "Повторить" }));
    expect(await screen.findByText("Какой мир мы видим")).toBeVisible();
  });

  it("retains a stale snapshot when refresh fails", async () => {
    mocks.globalCoverage.mockResolvedValueOnce({ ...coverage, stale: true }).mockRejectedValueOnce(new Error("offline"));
    const view = render(<GlobalCoverageDisclosure refreshToken={0} />);
    const summary = await screen.findByText("Какой мир мы видим");
    fireEvent.click(summary);
    expect(screen.getByText(/Показан прежний срез сбора/)).toBeVisible();
    view.rerender(<GlobalCoverageDisclosure refreshToken={1} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Показан предыдущий срез");
    expect(screen.getByText("Какой мир мы видим")).toBeVisible();
  });
});
