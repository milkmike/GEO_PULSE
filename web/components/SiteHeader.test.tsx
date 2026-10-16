import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { FeatureFlagsProvider } from "./FeatureFlagsProvider";
import { DEFAULT_FEATURE_FLAGS } from "@/lib/features";
import SiteHeader from "./SiteHeader";

const mocks = vi.hoisted(() => ({ push: vi.fn(), countries: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock("@/lib/api", () => ({ api: { countries: mocks.countries } }));
vi.mock("./HealthBadge", () => ({ default: () => <span data-testid="freshness">Данные обновляются</span> }));

function header(search = true, stories = true) {
  return <FeatureFlagsProvider flags={{ ...DEFAULT_FEATURE_FLAGS, searchNavigation: search, storiesNavigation: stories }}><SiteHeader active="/countries" /></FeatureFlagsProvider>;
}

beforeEach(() => {
  mocks.push.mockClear();
  mocks.countries.mockResolvedValue({ countries: [{ code: "RS", name: "Сербия", flag: "🇷🇸" }, { code: "CN", name: "Китай", flag: "🇨🇳" }] });
});
afterEach(() => vi.restoreAllMocks());

describe("SiteHeader", () => {
  it("shows the five sections, active accent and freshness; gates search and stories", () => {
    const { rerender } = render(header());
    expect(within(screen.getByRole("navigation")).getAllByRole("link").map((link) => link.textContent)).toEqual(["Обзор", "Страны", "Сюжеты", "Изменения", "О данных"]);
    expect(screen.getByRole("link", { name: "Изменения" })).toHaveAttribute("href", "/changes");
    expect(screen.getByRole("link", { name: "Страны" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "Страны" })).toHaveClass("text-accent");
    expect(screen.getByTestId("freshness")).toBeVisible();
    rerender(header(false, false));
    expect(screen.queryByRole("link", { name: "Сюжеты" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Поиск/ })).not.toBeInTheDocument();
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mocks.countries).not.toHaveBeenCalled();
  });

  it("loads countries once, focuses the input, traps focus, closes with Esc and restores focus", async () => {
    const user = userEvent.setup();
    const { unmount } = render(header());
    const button = screen.getByRole("button", { name: /Поиск/ });
    await user.click(button);
    const input = screen.getByRole("combobox");
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
    expect(input).toHaveFocus();
    await waitFor(() => expect(mocks.countries).toHaveBeenCalledTimes(1));
    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Найти" })).toHaveFocus();
    await user.tab();
    expect(input).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(button).toHaveFocus();
    unmount();
    render(header());
    await user.click(screen.getByRole("button", { name: /Поиск/ }));
    await user.type(screen.getByRole("combobox"), "Серб");
    expect(await screen.findByRole("option", { name: /Сербия/ })).toBeVisible();
    expect(mocks.countries).toHaveBeenCalledTimes(1);
  });

  it("supports Ctrl K, selects the first matching country by default and opens it on Enter", async () => {
    const user = userEvent.setup();
    render(header());
    fireEvent.keyDown(document, { key: "k", ctrlKey: true });
    await user.type(screen.getByRole("combobox"), "серб");
    const country = await screen.findByRole("option", { name: /Сербия/ });
    expect(country).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("combobox")).toHaveAttribute("aria-activedescendant", country.id);
    expect(screen.getAllByRole("option").at(-1)).toHaveTextContent("Искать «серб» в публикациях");
    await user.keyboard("{Enter}");
    expect(mocks.push).toHaveBeenLastCalledWith("/country/RS");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Поиск/ }));
    await user.type(screen.getByRole("combobox"), "Китай");
    await user.click(await screen.findByRole("option", { name: /Китай$/ }));
    expect(mocks.push).toHaveBeenLastCalledWith("/country/CN");
  });

  it("selects publication search after the country with ArrowDown and opens it on Enter", async () => {
    const user = userEvent.setup();
    render(header());
    await user.click(screen.getByRole("button", { name: /Поиск/ }));
    await user.type(screen.getByRole("combobox"), "серб");
    await screen.findByRole("option", { name: /Сербия/ });
    await user.keyboard("{ArrowDown}");
    const search = screen.getByRole("option", { name: "Искать «серб» в публикациях" });
    expect(search).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("combobox")).toHaveAttribute("aria-activedescendant", search.id);
    await user.keyboard("{Enter}");
    expect(mocks.push).toHaveBeenLastCalledWith(`/search?q=${encodeURIComponent("серб")}`);
  });

  it("wraps arrow selection through all options and resets it when the query changes", async () => {
    const user = userEvent.setup();
    render(header());
    await user.click(screen.getByRole("button", { name: /Поиск/ }));
    const input = screen.getByRole("combobox");
    await user.type(input, "и");
    const serbia = await screen.findByRole("option", { name: /Сербия/ });
    const china = screen.getByRole("option", { name: /Китай/ });
    const search = screen.getByRole("option", { name: "Искать «и» в публикациях" });
    await user.keyboard("{ArrowDown}");
    expect(china).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowDown}");
    expect(search).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowDown}");
    expect(serbia).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{ArrowUp}");
    expect(search).toHaveAttribute("aria-selected", "true");
    await user.clear(input);
    await user.type(input, "серб");
    expect(screen.getByRole("option", { name: /Сербия/ })).toHaveAttribute("aria-selected", "true");
  });

  it("selects publication search when no countries match and ignores blank input", async () => {
    const user = userEvent.setup();
    render(header());
    await user.click(screen.getByRole("button", { name: /Поиск/ }));
    await user.keyboard("{Enter}");
    expect(mocks.push).not.toHaveBeenCalled();
    await user.type(screen.getByRole("combobox"), "  газ  ");
    const search = screen.getByRole("option", { name: "Искать «газ» в публикациях" });
    expect(screen.getAllByRole("option")).toHaveLength(1);
    expect(search).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("combobox")).toHaveAttribute("aria-expanded", "true");
    await user.keyboard("{Enter}");
    expect(mocks.push).toHaveBeenCalledWith(`/search?q=${encodeURIComponent("газ")}`);
  });

  it("shows the platform shortcut and supports Command K", async () => {
    vi.spyOn(navigator, "platform", "get").mockReturnValue("MacIntel");
    render(header());
    expect(screen.getByRole("button", { name: "Поиск ⌘K" })).toBeVisible();
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    expect(screen.getByRole("dialog")).toBeVisible();
    fireEvent.keyDown(document, { key: "k", metaKey: true });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
