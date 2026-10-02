import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EarlySignalsResponse } from "@/lib/earlySignalTypes";
import EarlySignalPanel from "./EarlySignalPanel";

const mocks = vi.hoisted(() => ({ earlySignals: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: mocks }));

const result: EarlySignalsResponse = {
  as_of: "2026-10-01T10:00:00Z", notice: null,
  items: [{
    id: "signal-1", status: "needs_review", headline_ru: "Обсуждается новый формат переговоров",
    observations: [{ id: "ob-1", article_id: 42, text_ru: "Ведомство предложило встречу.", quote: "proposed a meeting" }],
    interpretation_ru: "Смена формата может открыть окно для переговоров.",
    hypothesis_ru: "Возможно, стороны изучают новый канал.", opportunity_ru: null,
    counterargument_ru: "Это может быть обычной консультацией.",
    watch: [{ observation_ru: "Объявят дату встречи", effect: "strengthens", by_date: "2026-10-15" }],
    country_codes: ["RS"], horizon_date: "2026-10-15", russia_link: "unestablished",
    as_of: "2026-10-01T10:00:00Z", review_note: null,
    evidence: [{ id: 42, title: "Original report", source_name: "Gazeta", url: "javascript:alert(1)", published_at: "2026-10-01T08:00:00Z", collected_at: "2026-10-01T09:00:00Z" }],
  }],
};

beforeEach(() => { mocks.earlySignals.mockReset(); mocks.earlySignals.mockResolvedValue(result); });

describe("EarlySignalPanel", () => {
  it("keeps evidence behind a keyboard accessible disclosure and omits empty opportunity", async () => {
    render(<EarlySignalPanel country="RS" countryName="Сербия" />);
    expect(await screen.findByText(result.items[0].headline_ru)).toBeVisible();
    const panel = screen.getByRole("region", { name: "На горизонте: Сербия" });
    expect(within(panel).getByText(result.items[0].interpretation_ru)).toBeVisible();
    expect(within(panel).queryByText("Возможность для России")).not.toBeInTheDocument();
    const disclosure = within(panel).getByRole("button", { name: "Раскрыть гипотезу" });
    expect(disclosure).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(disclosure);
    expect(within(panel).getByRole("button", { name: "Свернуть гипотезу" })).toHaveAttribute("aria-expanded", "true");
    expect(within(panel).getByText("Связь с Россией пока не установлена.")).toBeVisible();
    const sources = within(panel).getByText("На чём основана гипотеза · 1 публикация").closest("details");
    expect(sources).not.toHaveAttribute("open");
    fireEvent.click(within(sources!).getByText("На чём основана гипотеза · 1 публикация"));
    expect(within(sources!).getByText("proposed a meeting", { exact: false })).toBeVisible();
    expect(within(sources!).getByText(/Gazeta · Original report/)).toBeVisible();
    expect(within(sources!).queryByRole("link", { name: "Открыть публикацию" })).not.toBeInTheDocument();
    fireEvent.click(within(panel).getByRole("button", { name: "Свернуть гипотезу" }));
    expect(within(panel).queryByText("Что может происходить")).not.toBeInTheDocument();
  });

  it("hides the previous country's signals while a new country loads and ignores the old response", async () => {
    let finishOld!: (value: EarlySignalsResponse) => void;
    mocks.earlySignals.mockImplementation((country: string | null) => country === "RS"
      ? new Promise<EarlySignalsResponse>((resolve) => { finishOld = resolve; })
      : Promise.resolve({ ...result, items: [] }));
    const view = render(<EarlySignalPanel country="RS" countryName="Сербия" />);
    view.rerender(<EarlySignalPanel country="AE" countryName="ОАЭ" />);
    expect(await screen.findByRole("status")).toHaveTextContent("Ищем ранние сигналы");
    await act(async () => finishOld(result));
    expect(screen.queryByText(result.items[0].headline_ru)).not.toBeInTheDocument();
    expect(screen.queryByText("На горизонте: ОАЭ")).not.toBeInTheDocument();
  });

  it("retains the current snapshot and offers retry after a failed refresh", async () => {
    mocks.earlySignals.mockResolvedValueOnce(result).mockRejectedValueOnce(new Error("offline"));
    const view = render(<EarlySignalPanel country={null} refreshToken={0} />);
    expect(await screen.findByText(result.items[0].headline_ru)).toBeVisible();
    view.rerender(<EarlySignalPanel country={null} refreshToken={1} />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Показан предыдущий обзор");
    expect(screen.getByText(result.items[0].headline_ru)).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(mocks.earlySignals).toHaveBeenCalledTimes(3);
  });

  it("can return to world scope after the page chooses a default country", async () => {
    render(<EarlySignalPanel country={null} availableCountry="RS" countryName="Сербия" />);
    expect(await screen.findByText("На горизонте: мир")).toBeVisible();
    expect(mocks.earlySignals).toHaveBeenCalledWith(null, expect.any(AbortSignal));
    fireEvent.click(screen.getByRole("button", { name: "Сербия" }));
    expect(await screen.findByText("На горизонте: Сербия")).toBeVisible();
    expect(mocks.earlySignals).toHaveBeenCalledWith("RS", expect.any(AbortSignal));
    fireEvent.click(screen.getByRole("button", { name: "Мир" }));
    expect(await screen.findByText("На горизонте: мир")).toBeVisible();
  });

  it("places the matching publication below its observation", async () => {
    const withSources: EarlySignalsResponse = { ...result, items: [{ ...result.items[0], evidence: [
      { ...result.items[0].evidence[0], id: 99, title: "Unrelated", url: "https://example.org/unrelated" },
      { ...result.items[0].evidence[0], id: 42, title: "Matching", url: "https://example.org/matching" },
    ] }] };
    mocks.earlySignals.mockResolvedValue(withSources);
    render(<EarlySignalPanel country="RS" />);
    await screen.findByText(withSources.items[0].headline_ru);
    fireEvent.click(screen.getByText("Раскрыть гипотезу"));
    fireEvent.click(screen.getByText("На чём основана гипотеза · 1 публикация"));
    const observation = screen.getByText("Ведомство предложило встречу.").closest("li");
    expect(within(observation!).getByText(/Gazeta · Matching/)).toBeVisible();
    expect(within(observation!).getByRole("link", { name: "Открыть публикацию" })).toHaveAttribute("href", "https://example.org/matching");
    expect(screen.queryByText(/Gazeta · Unrelated/)).not.toBeInTheDocument();
  });

  it("omits the panel when the ready scope has no signals", async () => {
    mocks.earlySignals.mockResolvedValue({ ...result, items: [] });
    render(<EarlySignalPanel country={null} availableCountry="RS" countryName="Сербия" />);
    await act(async () => {});
    expect(screen.queryByText("На горизонте: мир")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: /На горизонте/ })).not.toBeInTheDocument();
  });

  it("offers a compact route back to world signals when a country has none", async () => {
    mocks.earlySignals.mockImplementation((country: string | null) => Promise.resolve(country ? { ...result, items: [] } : result));
    render(<EarlySignalPanel country="RS" availableCountry="RS" countryName="Сербия" />);
    const worldButton = await screen.findByRole("button", { name: "Посмотреть ранние сигналы в мире" });
    expect(screen.queryByText("На горизонте: Сербия")).not.toBeInTheDocument();
    fireEvent.click(worldButton);
    expect(await screen.findByText("На горизонте: мир")).toBeVisible();
    expect(mocks.earlySignals).toHaveBeenCalledWith(null, expect.any(AbortSignal));
  });

  it("shows the API notice without claiming the publication text was verified", async () => {
    mocks.earlySignals.mockResolvedValue({ ...result, notice: "Это версии развития событий, а не прогнозы. Сопоставляйте их с источниками и новыми фактами." });
    render(<EarlySignalPanel country={null} />);
    expect(await screen.findByText(/Это версии развития событий, а не прогнозы/)).toBeVisible();
    expect(screen.queryByText(/Публикации и цитаты проверены/)).not.toBeInTheDocument();
  });

  it("dates the header from the newest dossier rather than the API read time", async () => {
    mocks.earlySignals.mockResolvedValue({
      ...result,
      as_of: "2026-10-06T12:00:00Z",
      items: [{ ...result.items[0], as_of: "2026-10-02T10:00:00Z" }],
    });
    render(<EarlySignalPanel country={null} />);
    expect(await screen.findByText("Разбор от 2 октября 2026 г.")).toBeVisible();
    expect(screen.queryByText(/6 октября 2026/)).not.toBeInTheDocument();
  });
});
