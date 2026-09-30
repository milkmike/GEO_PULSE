import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AgendaPanel from "./AgendaPanel";
import type { AgendasResponse } from "@/lib/types";

const mocks = vi.hoisted(() => ({ agendas: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: mocks }));

function payload(): AgendasResponse {
  return {
    items: [{ id: 1, title: "Открыт новый авиарейс", article_count: 3, source_count: 2,
      countries: ["RU", "AE"], first_seen: "2026-09-30T08:00:00Z", last_seen: "2026-09-30T09:00:00Z",
      updated_at: "2026-09-30T09:10:00Z", same_event_count: 1, development_count: 1, model: "typesafe/jev-1.13",
      articles: [
        { id: 1, title: "Первое сообщение", url: "https://example.com/a", source_name: "Газета", country_code: "RU", published_at: "2026-09-30T08:00:00Z", collected_at: "2026-09-30T08:30:00Z", relation: "seed", confidence: null },
        { id: 2, title: "Второе сообщение", url: "javascript:alert(1)", source_name: "Агентство", country_code: "AE", published_at: "2099-01-01T00:00:00Z", collected_at: "2026-09-30T09:00:00Z", date_warning: true, relation: "same_event", confidence: 0.9 },
        { id: 3, title: "Реакция перевозчика", url: null, source_name: "Газета", country_code: "RU", published_at: null, collected_at: "2026-09-30T09:10:00Z", relation: "development", confidence: 0.85 },
      ] }],
    coverage: { status: "ok", last_run_at: "2026-09-30T09:10:00Z", articles_scanned: 240, candidate_groups: 10, decisions: 20, accepted: 9, remaining_budget_usd: 1.25 }, has_more: false,
  };
}

beforeEach(() => { mocks.agendas.mockReset().mockResolvedValue(payload()); });

describe("AgendaPanel", () => {
  it("expands evidence with safe source links and collection dates for suspicious publication dates", async () => {
    const user = userEvent.setup();
    render(<AgendaPanel />);
    const expand = await screen.findByRole("button", { name: /показать публикации/i });
    expect(screen.queryByText("Первое сообщение")).not.toBeInTheDocument();
    await user.click(expand);
    expect(expand).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: "Первое сообщение" })).toHaveAttribute("href", "https://example.com/a");
    expect(screen.queryByRole("link", { name: "Второе сообщение" })).not.toBeInTheDocument();
    const suspicious = screen.getByText("Второе сообщение").closest("li")!;
    expect(within(suspicious).getByText(/Собрано/)).toHaveTextContent("30 сент.");
    expect(suspicious).not.toHaveTextContent("2099");
    expect(screen.getByText("Исходная публикация")).toBeVisible();
    expect(screen.getByText("То же событие")).toBeVisible();
    expect(screen.getByText("Развитие и реакция")).toBeVisible();
    expect(screen.getByText(/Уверенность в связи: 90%/)).toBeVisible();
    await user.click(expand);
    expect(screen.queryByText("Первое сообщение")).not.toBeInTheDocument();
  });

  it("recovers a failed request through retry without leaving the panel", async () => {
    mocks.agendas.mockRejectedValueOnce(new Error("offline"));
    const user = userEvent.setup();
    render(<AgendaPanel />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить повестки");
    await user.click(screen.getByRole("button", { name: /Повторить загрузку повесток/i }));
    expect(await screen.findByText("Открыт новый авиарейс")).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("separates loading, not-yet-run coverage, and searched empty results", async () => {
    let resolve!: (value: AgendasResponse) => void;
    mocks.agendas.mockReturnValueOnce(new Promise<AgendasResponse>((r) => { resolve = r; }));
    const user = userEvent.setup();
    render(<AgendaPanel />);
    expect(screen.getByRole("status")).toHaveTextContent("Загружаем повестки");
    const empty = payload(); empty.items = []; empty.coverage.status = "never_run"; empty.coverage.last_run_at = null;
    await act(async () => resolve(empty));
    expect(screen.getByText(/Сбор повесток ещё не запускался/)).toBeVisible();
    mocks.agendas.mockResolvedValue({ ...empty, coverage: { ...empty.coverage, status: "ok" } });
    await user.type(screen.getByRole("searchbox", { name: "Поиск по повесткам" }), "  рейс  ");
    await user.click(screen.getByRole("button", { name: "Найти повестки" }));
    expect(await screen.findByText(/По запросу «рейс» повесток не найдено/)).toBeVisible();
    expect(mocks.agendas).toHaveBeenLastCalledWith({ limit: 20, q: "рейс" }, expect.any(AbortSignal));
  });

  it("keeps evidence accessible when the discovery budget is exhausted", async () => {
    const paused = payload(); paused.coverage.status = "budget_exhausted"; paused.coverage.remaining_budget_usd = 0;
    mocks.agendas.mockResolvedValue(paused);
    const user = userEvent.setup();
    render(<AgendaPanel />);
    expect(await screen.findByText(/Лимит бюджета достигнут/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: /показать публикации/i }));
    expect(screen.getByRole("link", { name: "Первое сообщение" })).toBeVisible();
  });

  it("does not attribute fallback grouping or an anchor alone to Jev verification", async () => {
    const fallback = payload(); fallback.items[0].model = "lexical-fallback";
    mocks.agendas.mockResolvedValue(fallback);
    const { unmount } = render(<AgendaPanel />);
    await screen.findByText("Открыт новый авиарейс");
    expect(screen.queryByText("Связи проверены Jev")).not.toBeInTheDocument();
    unmount();
    fallback.items[0].model = "typesafe/jev-1.13";
    fallback.items[0].same_event_count = 0; fallback.items[0].development_count = 0;
    fallback.items[0].articles = [fallback.items[0].articles[0]];
    mocks.agendas.mockResolvedValue(fallback);
    render(<AgendaPanel />);
    await screen.findByText("Открыт новый авиарейс");
    expect(screen.queryByText("Связи проверены Jev")).not.toBeInTheDocument();
  });

  it("ignores a stale search response after a new request", async () => {
    let resolveOld!: (value: AgendasResponse) => void;
    mocks.agendas.mockReturnValueOnce(new Promise<AgendasResponse>((r) => { resolveOld = r; }));
    const user = userEvent.setup();
    render(<AgendaPanel />);
    const newer = payload(); newer.items[0].title = "Свежая повестка";
    mocks.agendas.mockResolvedValueOnce(newer);
    await user.type(screen.getByRole("searchbox"), "Свежая");
    await user.click(screen.getByRole("button", { name: "Найти повестки" }));
    await screen.findByText("Свежая повестка");
    await act(async () => resolveOld(payload()));
    await waitFor(() => expect(screen.queryByText("Открыт новый авиарейс")).not.toBeInTheDocument());
  });
});
