import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { NewsLead } from "@/lib/decisionTypes";
import NewsReadingList from "./NewsReadingList";

const lead: NewsLead = {
  article_id: 1,
  title_ru: "Переговоры Сербии и России",
  title_original: "Serbia and Russia hold talks",
  url: "https://example.org/news",
  publisher_name: "Газета",
  publisher_country_code: "RS",
  published_at: "2026-09-30T08:00:00Z",
  collected_at: "2026-09-30T09:00:00Z",
  topic: "diplomacy",
  event_type: "analysis",
  actor_type: "government",
  russia_relation: "uncertain",
  status: "needs_review",
  countries: ["RS"],
};

describe("NewsReadingList", () => {
  it("starts with Russian titles, switches language, and reveals remaining articles", () => {
    const leads: NewsLead[] = [1, 2, 3, 4, 5].map((article_id) => ({
      ...lead, article_id, title_ru: `Перевод ${article_id}`,
    }));
    leads.push({ ...lead, article_id: 6, title_ru: null, title_original: "Original only" });
    render(<NewsReadingList leads={leads} initialCount={3} />);
    expect(screen.getByRole("button", { name: "На русском (5)" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "На языке источника (1)" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Перевод 1")).toBeVisible();
    expect(screen.queryByText("Перевод 4")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Показать ещё 2" }));
    expect(screen.getByText("Перевод 5")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Перевод 4" })).toHaveFocus();
    expect(screen.queryByRole("button", { name: /Показать ещё/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "На языке источника (1)" }));
    expect(screen.getByText("Original only")).toBeVisible();
    expect(screen.queryByText("Перевод 1")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "На языке источника (1)" })).toHaveAttribute("aria-pressed", "true");
  });

  it("explains missing translation and retains article details without unsafe links", () => {
    render(<NewsReadingList leads={[{ ...lead, title_ru: null, url: "javascript:alert(1)" }]} />);
    expect(screen.getByText("Русского перевода пока нет. Можно прочитать публикации на языке источника.")).toBeVisible();
    expect(screen.queryByRole("button", { name: /На русском/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "На языке источника (1)" }));
    expect(screen.getByText("Serbia and Russia hold talks")).toBeVisible();
    const details = screen.getByText("О публикации").closest("details");
    expect(details).not.toHaveAttribute("open");
    fireEvent.click(within(details!).getByText("О публикации"));
    expect(within(details!).getByText("Тема: Дипломатия")).toBeVisible();
    expect(within(details!).getByText("Тип: анализ")).toBeVisible();
    expect(within(details!).getByText("Связь с Россией: требует уточнения")).toBeVisible();
    expect(screen.queryByRole("link", { name: "Открыть статью" })).not.toBeInTheDocument();
    expect(within(details!).getByText("Ссылка на оригинал недоступна.")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "На языке источника (1)" }));
    expect(screen.getByRole("button", { name: "На языке источника (1)" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByText("Serbia and Russia hold talks")).not.toBeInTheDocument();
  });

  it("opens a safe original article and shows its Moscow publication time", () => {
    render(<NewsReadingList leads={[lead]} />);
    expect(screen.getByRole("link", { name: "Открыть статью" })).toHaveAttribute("href", "https://example.org/news");
    expect(screen.getByText("Газета")).toBeVisible();
    expect(screen.getByText(/30 сент.*11:00 мск/)).toBeVisible();
  });

  it("keeps the Russian selection when untranslated articles disappear and return on refresh", () => {
    const translated = { ...lead, article_id: 2 };
    const original = { ...lead, article_id: 3, title_ru: null, title_original: "Original only" };
    const view = render(<NewsReadingList leads={[translated, original]} />);
    fireEvent.click(screen.getByRole("button", { name: "На языке источника (1)" }));
    expect(screen.getByText("Original only")).toBeVisible();
    view.rerender(<NewsReadingList leads={[translated]} />);
    expect(screen.getByText("Переговоры Сербии и России")).toBeVisible();
    view.rerender(<NewsReadingList leads={[translated, original]} />);
    expect(screen.getByRole("button", { name: "На русском (1)" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Переговоры Сербии и России")).toBeVisible();
  });

  it("does not steal keyboard focus when an expanded list receives fresh articles", () => {
    const first = { ...lead, article_id: 1, title_ru: "Перевод 1" };
    const leads = [1, 2, 3, 4].map((article_id) => ({
      ...first, article_id, title_ru: `Перевод ${article_id}`,
    }));
    const view = render(<NewsReadingList leads={leads} initialCount={3} />);
    fireEvent.click(screen.getByRole("button", { name: "Показать ещё 1" }));
    const source = within(screen.getByText("Перевод 1").closest("article")!).getByRole("link", { name: "Открыть статью" });
    source.focus();
    expect(source).toHaveFocus();
    view.rerender(<NewsReadingList leads={[...leads.slice(0, 3), { ...first, article_id: 5, title_ru: "Новая статья" }]} initialCount={3} />);
    expect(screen.getByText("Новая статья")).toBeVisible();
    expect(source).toHaveFocus();
  });
});
