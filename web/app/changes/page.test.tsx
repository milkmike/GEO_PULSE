import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FeatureFlagsProvider } from "@/components/FeatureFlagsProvider";
import { DEFAULT_FEATURE_FLAGS } from "@/lib/features";
import type { ChangeItem, ChangesResponse } from "@/lib/types";
import ChangesPage from "./page";
import { ChangeRow, dayLabel, groupChanges } from "@/components/ChangesFeed";

const mocks = vi.hoisted(() => ({ search: "", replace: vi.fn(), changes: vi.fn(), countries: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mocks.replace }), usePathname: () => "/changes",
  useSearchParams: () => new URLSearchParams(mocks.search),
}));
vi.mock("@/components/SiteHeader", () => ({ default: () => <header>Шапка</header> }));
vi.mock("@/lib/api", () => ({ api: { changes: mocks.changes, countries: mocks.countries } }));

const item: ChangeItem = {
  id: "signal:1", kind: "signal", type: "index_shift", country_codes: ["JP"], country_names: ["Япония"],
  title: "Индекс −9,6 за сутки", context: "теперь −54, напряжение", direction: "worse", severity: "warning",
  occurred_at: "2026-10-16T07:00:00Z", first_at: "2026-10-14T07:00:00Z", repeat_count: 3, days_running: 3,
  href: "/signals/1", evidence_total: 21,
  evidence_preview: [{ id: 1, title: "Переговоры с Россией", source_name: "Источник", published_at: "2026-10-16T06:00:00Z", url: "https://example.org/1" },
    { id: 2, title: "Обсуждение соглашения", source_name: "Газета", published_at: "2026-10-15T06:00:00Z", url: "https://example.org/2" }],
};
const response = (items: ChangeItem[] = [item], next_cursor: string | null = null): ChangesResponse => ({ items, total: 111, raw_total: 176, next_cursor });

beforeEach(() => {
  vi.clearAllMocks(); mocks.search = "";
  mocks.changes.mockResolvedValue(response());
  mocks.countries.mockResolvedValue({ countries: [{ code: "JP", name: "Япония" }, { code: "GB", name: "Великобритания" }] });
});

describe("changes feed", () => {
  it("groups UTC days and labels today, yesterday and older days across month boundaries", () => {
    const now = new Date("2026-11-01T01:00:00Z");
    expect(dayLabel("2026-11-01", now)).toBe("Сегодня, 1 ноября");
    expect(dayLabel("2026-10-31", now)).toBe("Вчера, 31 октября");
    expect(dayLabel("2026-10-30", now)).toBe("30 октября");
    expect(groupChanges([item, { ...item, id: "signal:2", occurred_at: "2026-10-16T01:00:00+03:00" }]).map(([day]) => day)).toEqual(["2026-10-16", "2026-10-15"]);
  });

  it("shows the compact row with direction, country, copy and UTC time; expands publications on click", () => {
    render(<ChangeRow item={item} detailEnabled={false} />);
    const row = screen.getByRole("button", { name: /Япония/ });
    expect(row).toHaveClass("min-h-11");
    expect(row).toHaveTextContent("Индекс −9,6 за сутки");
    expect(row).toHaveTextContent("теперь −54, напряжение");
    expect(row).toHaveTextContent("07:00");
    expect(screen.getByRole("img", { name: "Хуже" })).toHaveTextContent("▼");
    expect(screen.queryByRole("link", { name: "Разбор →" })).not.toBeInTheDocument();
    fireEvent.click(row);
    expect(row).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("link", { name: "Переговоры с Россией" })).toHaveAttribute("href", "https://example.org/1");
    expect(screen.getByText("Показаны 2 из 21 публикаций.")).toBeVisible();
    expect(screen.getByRole("link", { name: "Разбор →" })).toHaveAttribute("href", "/country/JP");
    fireEvent.click(row);
    expect(screen.queryByRole("link", { name: "Разбор →" })).not.toBeInTheDocument();
  });

  it.each([["better", "Лучше", "▲"], ["new", "Новый сюжет", "◆"], ["attention", "Внимание", "●"]] as const)("uses the %s icon", (direction, label, icon) => {
    render(<ChangeRow item={{ ...item, direction }} detailEnabled />);
    expect(screen.getByRole("img", { name: label })).toHaveTextContent(icon);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByRole("link", { name: "Разбор →" })).toHaveAttribute("href", "/signals/1");
  });

  it.each([false, true])("omits the publication count for empty evidence (detail enabled: %s)", (detailEnabled) => {
    render(<ChangeRow item={{ ...item, evidence_total: 0, evidence_preview: [] }} detailEnabled={detailEnabled} />);
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("Публикации пока недоступны.")).toBeVisible();
    expect(screen.queryByText(/Показаны/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Разбор →" })).toHaveAttribute("href", detailEnabled ? "/signals/1" : "/country/JP");
  });

  it.each([
    [["Казахстан", "Таджикистан"], "Казахстан, Таджикистан."],
    [["Казахстан", "Таджикистан", "Кыргызстан"], "Казахстан, Таджикистан, Кыргызстан."],
    [["Казахстан", "Таджикистан", "Кыргызстан", "Россия", "Узбекистан"], "Казахстан, Таджикистан, Кыргызстан и ещё 2."],
  ])("shows merged countries with a limit of three: %s", (country_names, label) => {
    render(<ChangeRow item={{ ...item, type: "notable_event", country_names }} detailEnabled />);
    expect(screen.getByText(label)).toBeVisible();
    expect(screen.queryByText(/Узбекистан/)).not.toBeInTheDocument();
  });

  it("wraps the title and context separately on phones and keeps desktop rows inline", () => {
    render(<ChangeRow item={item} detailEnabled />);
    const row = screen.getByRole("button");
    const title = screen.getByText("Япония.").parentElement!;
    const context = screen.getByText(item.context);
    expect(row).toHaveClass("items-start", "sm:items-center");
    for (const text of [title, context]) {
      expect(text).toHaveClass("line-clamp-2", "break-words", "sm:line-clamp-none", "sm:inline");
      expect(text).not.toHaveClass("truncate");
    }
    expect(title.parentElement).toBe(context.parentElement);
    expect(title.parentElement).toHaveClass("min-w-0", "sm:truncate");
    expect(title.parentElement).not.toHaveClass("truncate");
    expect(context.firstElementChild).toHaveClass("hidden", "sm:inline");
    const time = screen.getByText("07:00");
    expect(time.parentElement).toHaveClass("shrink-0");
    expect(time.parentElement?.parentElement).toBe(row);
    expect(row.lastElementChild).toBe(time.parentElement);
  });

  it("loads thirty events, shows grouping and totals, and appends the next page without repeats", async () => {
    mocks.changes.mockResolvedValueOnce(response([item], "cursor-1")).mockResolvedValueOnce(response([item, { ...item, id: "story:2", kind: "story", direction: "new", title: "Новый договор", occurred_at: "2026-10-15T10:00:00Z" }]));
    render(<ChangesPage />);
    expect(await screen.findByText("за 7 дней · 111 событий (65 повторов склеено)")).toBeVisible();
    expect(mocks.changes).toHaveBeenCalledWith({ type: "all", country: "", important: false }, null, expect.any(AbortSignal));
    expect(screen.getByRole("heading", { name: /16 октября/ })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Ещё" }));
    expect(await screen.findByText("Новый договор", { exact: false })).toBeVisible();
    expect(mocks.changes).toHaveBeenLastCalledWith({ type: "all", country: "", important: false }, "cursor-1", expect.any(AbortSignal));
    expect(screen.getAllByRole("button", { name: /Япония/ })).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Ещё" })).not.toBeInTheDocument();
    expect(screen.getByText("Совпадение по времени не означает причину.")).toBeVisible();
  });

  it("reads URL filters, writes changes into the URL and resets pagination", async () => {
    mocks.search = "type=tone&country=GB&important=1&cursor=old";
    render(<ChangesPage />);
    await screen.findByRole("option", { name: "Япония" });
    expect(mocks.changes).toHaveBeenCalledWith({ type: "tone", country: "GB", important: true }, null, expect.any(AbortSignal));
    expect(screen.getByRole("button", { name: "Тон СМИ" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Индекс" }));
    expect(mocks.replace).toHaveBeenLastCalledWith("/changes?type=index&country=GB&important=1", { scroll: false });
    fireEvent.change(screen.getByRole("combobox", { name: "Страна" }), { target: { value: "JP" } });
    expect(mocks.replace).toHaveBeenLastCalledWith("/changes?type=tone&country=JP&important=1", { scroll: false });
    fireEvent.click(screen.getByRole("switch", { name: "Только важные" }));
    expect(mocks.replace).toHaveBeenLastCalledWith("/changes?type=tone&country=GB", { scroll: false });
  });

  it("does not let an older filter response overwrite the new one", async () => {
    let resolveOld!: (value: ChangesResponse) => void;
    mocks.changes.mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
    const { rerender } = render(<ChangesPage />);
    mocks.search = "type=stories";
    mocks.changes.mockResolvedValueOnce(response([]));
    rerender(<ChangesPage />);
    expect(await screen.findByText("За 7 дней изменений по выбранным фильтрам нет.")).toBeVisible();
    resolveOld(response());
    await waitFor(() => expect(screen.queryByRole("button", { name: /Япония/ })).not.toBeInTheDocument());
  });

  it("shows an empty line and a recoverable error", async () => {
    mocks.changes.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(response([]));
    render(<ChangesPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить изменения.");
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByText("За 7 дней изменений по выбранным фильтрам нет.")).toBeVisible();
  });

  it("uses signal detail when enabled and keeps story links regardless of the flag", async () => {
    render(<FeatureFlagsProvider flags={{ ...DEFAULT_FEATURE_FLAGS, signalDetail: true }}><ChangesPage /></FeatureFlagsProvider>);
    fireEvent.click(await screen.findByRole("button", { name: /Япония/ }));
    expect(screen.getByRole("link", { name: "Разбор →" })).toHaveAttribute("href", "/signals/1");
  });
});
