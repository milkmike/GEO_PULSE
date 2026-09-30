import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { AgendaItem, AgendasResponse } from "@/lib/types";
import AgendaHighlights from "./AgendaHighlights";

const mocks = vi.hoisted(() => ({ agendas: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: mocks }));
const NOW = new Date("2026-09-30T12:00:00Z");

function item(id: number, last_seen: string | null, title = `Повестка ${id}`): AgendaItem {
  return {
    id, title, last_seen, updated_at: NOW.toISOString(), first_seen: "2026-06-01T09:00:00Z",
    article_count: 2, source_count: 2, countries: ["RU", "AE"], same_event_count: 1, development_count: 0, model: "typesafe/jev-1.13",
    articles: [
      { id: 10, title: "Исходный материал", url: "https://example.com/source", source_name: "Газета", country_code: "RU", published_at: last_seen, collected_at: last_seen, relation: "seed", confidence: null },
      { id: 11, title: "Дополнительный материал", url: "javascript:alert(1)", source_name: "Агентство", country_code: "AE", published_at: last_seen, collected_at: last_seen, relation: "same_event", confidence: .9 },
    ],
  };
}
function response(items: AgendaItem[] = [], status: AgendasResponse["coverage"]["status"] = "ok"): AgendasResponse {
  return { items, has_more: false, coverage: { status, last_run_at: NOW.toISOString(), articles_scanned: 200, candidate_groups: 30, decisions: 40, accepted: 10, remaining_budget_usd: 1 } };
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
  mocks.agendas.mockReset().mockResolvedValue(response());
});
afterEach(() => vi.useRealTimers());

describe("AgendaHighlights", () => {
  it("uses collection recency rather than an updated archive timestamp and retains inspectable evidence", async () => {
    mocks.agendas.mockResolvedValue(response([
      item(1, "2026-06-01T10:00:00Z", "Июньский архив"),
      item(2, "2026-09-30T11:00:00Z", "Свежая повестка"),
      item(3, "2026-10-01T11:00:00Z", "Будущая повестка"),
      item(4, "not-a-date", "Некорректная дата"),
      item(5, null, "Дата отсутствует"),
    ]));
    render(<AgendaHighlights />);
    expect(await screen.findByText("Свежая повестка")).toBeVisible();
    for (const title of ["Июньский архив", "Будущая повестка", "Некорректная дата", "Дата отсутствует"]) expect(screen.queryByText(title)).not.toBeInTheDocument();
    expect(mocks.agendas).toHaveBeenCalledWith({ limit: 6 }, expect.any(AbortSignal));
    expect(screen.getByText(/Последняя публикация собрана:/)).toHaveTextContent("14:00 МСК");
    expect(screen.queryByText(/Обновлено:/)).not.toBeInTheDocument();
    expect(screen.getByText(/Страны издателей/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Все повестки и архив сюжетов" })).toHaveAttribute("href", "/stories");
    fireEvent.click(screen.getByRole("button", { name: "Показать публикации" }));
    expect(screen.getByRole("link", { name: "Исходный материал" })).toHaveAttribute("href", "https://example.com/source");
    expect(screen.queryByRole("link", { name: "Дополнительный материал" })).not.toBeInTheDocument();
    expect(screen.getByText("То же событие")).toBeVisible();
  });

  it("includes the exact 72-hour boundary and excludes older collection times", async () => {
    mocks.agendas.mockResolvedValue(response([
      item(1, "2026-09-27T12:00:00Z", "На границе окна"),
      item(2, "2026-09-27T11:59:59Z", "За границей окна"),
      item(3, NOW.toISOString(), "Собрано сейчас"),
    ]));
    render(<AgendaHighlights />);
    expect(await screen.findByText("На границе окна")).toBeVisible();
    expect(screen.getByText("Собрано сейчас")).toBeVisible();
    expect(screen.queryByText("За границей окна")).not.toBeInTheDocument();
  });

  it("limits the visible section to six fresh agendas", async () => {
    mocks.agendas.mockResolvedValue(response(Array.from({ length: 8 }, (_, index) => item(index + 1, "2026-09-30T11:00:00Z"))));
    render(<AgendaHighlights />);
    await screen.findByText("Повестка 1");
    expect(screen.getAllByRole("article")).toHaveLength(6);
    expect(screen.queryByText("Повестка 7")).not.toBeInTheDocument();
  });

  it("shows an honest empty state instead of falling back to archived stories", async () => {
    mocks.agendas.mockResolvedValue(response([item(1, "2026-06-01T10:00:00Z", "Старый сюжет")]));
    render(<AgendaHighlights />);
    expect(await screen.findByText(/Среди загруженных повесток нет публикаций/)).toBeVisible();
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    expect(screen.getByText(/Это не означает отсутствия событий/)).toBeVisible();
  });

  it("offers a local retry on fetch failure and does not fetch an archive", async () => {
    mocks.agendas.mockRejectedValueOnce(new Error("offline"));
    render(<AgendaHighlights />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Не удалось загрузить свежие повестки");
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
    mocks.agendas.mockResolvedValueOnce(response([item(1, "2026-09-30T11:00:00Z", "Восстановленная повестка")]));
    fireEvent.click(within(alert).getByRole("button", { name: /Повторить/i }));
    expect(await screen.findByText("Восстановленная повестка")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(mocks.agendas).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["budget_exhausted", /Лимит бюджета достигнут/],
    ["error", /Последний проход завершился с ошибкой/],
    ["disabled", /Поиск новых повесток отключён/],
    ["running", /Идёт обновление повесток/],
    ["never_run", /Сбор повесток ещё не запускался/],
  ] as const)("keeps fresh evidence visible while coverage status is %s", async (status, message) => {
    mocks.agendas.mockResolvedValue(response([item(1, "2026-09-30T11:00:00Z")], status));
    render(<AgendaHighlights />);
    expect(await screen.findByText(message)).toBeVisible();
    expect(screen.getByRole("article")).toBeVisible();
  });

  it("expires cached cards on the polling clock even when refresh fails", async () => {
    vi.useRealTimers(); vi.useFakeTimers(); vi.setSystemTime(NOW);
    mocks.agendas.mockResolvedValueOnce(response([item(1, "2026-09-27T12:01:00Z", "Истекающая повестка")]))
      .mockRejectedValueOnce(new Error("refresh offline"));
    render(<AgendaHighlights />);
    await act(async () => {});
    expect(screen.getByText("Истекающая повестка")).toBeVisible();
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(screen.queryByText("Истекающая повестка")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Не удалось загрузить свежие повестки");
    expect(screen.getByText(/Среди загруженных повесток нет публикаций/)).toBeVisible();
  });

  it("does not overlap polling requests and aborts pending work on unmount", async () => {
    vi.useRealTimers(); vi.useFakeTimers(); vi.setSystemTime(NOW);
    let resolve!: (value: AgendasResponse) => void;
    mocks.agendas.mockReturnValueOnce(new Promise<AgendasResponse>((r) => { resolve = r; }));
    const { unmount } = render(<AgendaHighlights />);
    expect(screen.getByRole("status")).toHaveTextContent("Загружаем свежие повестки");
    await act(async () => { await vi.advanceTimersByTimeAsync(240_000); });
    expect(mocks.agendas).toHaveBeenCalledOnce();
    await act(async () => resolve(response([item(1, "2026-09-30T11:00:00Z")])));
    mocks.agendas.mockReturnValueOnce(new Promise(() => {}));
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(mocks.agendas).toHaveBeenCalledTimes(2);
    const signal = mocks.agendas.mock.calls[1][1] as AbortSignal;
    unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(240_000); });
    expect(mocks.agendas).toHaveBeenCalledTimes(2);
  });
});
