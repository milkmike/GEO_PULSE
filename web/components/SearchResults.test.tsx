import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { SearchArticle } from "@/lib/types";
import SearchResults from "./SearchResults";

const result = {
  article_id: 123,
  title: "Путин обсудил отношения с Испанией",
  summary: "Переговоры затронули двусторонние отношения.",
  url: "https://elpais.com/mundo/putin-espana",
  published_at: "2026-07-14T12:30:00+00:00",
  language: "es",
  source: { name: "El País", country: "ES", tier: "mainstream" },
  topics: ["diplomacy"],
  matched_entities: [
    {
      id: "93dbeaec-c20b-44ad-aaed-46b18ea86a47",
      name: "Владимир Путин",
      kind: "person",
      mention_text: "Путин",
      confidence: 0.96,
    },
  ],
  sentiment: 1.5,
  action_level: 3,
  story: {
    id: 7,
    slug: "russia-spain-talks",
    title: "Переговоры России и Испании",
  },
  why_included: "Точное упоминание сущности «Владимир Путин»",
  relevance_score: 0.712345,
  confidence: 0.91,
  evidence: [
    {
      type: "text_span",
      article_id: 123,
      text: "В Мадриде «Путин» обсуждал отношения с Испанией",
    },
  ],
  scores: {
    lexical: 0.75,
    entity: 1,
    topic: 0,
    freshness: 0.98,
    trust: 0.9,
    story: 0.8,
    vector: null,
  },
} satisfies SearchArticle;

describe("SearchResults", () => {
  it("shows primary evidence and explains why the Spain/Putin result matched", async () => {
    const user = userEvent.setup();
    render(<SearchResults items={[result]} />);

    expect(
      screen.getByRole("heading", { name: result.title }),
    ).toBeInTheDocument();
    expect(screen.getByText(/El País/)).toHaveTextContent("Испания");
    expect(screen.getByText(/El País/)).toHaveTextContent("14 июл");
    expect(screen.getByText("Путин", { selector: "mark" })).toBeInTheDocument();

    const sourceLink = screen.getByRole("link", { name: /открыть источник/i });
    expect(sourceLink).toHaveAttribute("href", result.url);
    expect(sourceLink).toHaveAttribute("rel", expect.stringContaining("noopener"));

    expect(
      screen.getByRole("link", { name: result.story.title }),
    ).toHaveAttribute("href", "/stories/7");

    await user.click(screen.getByRole("button", { name: /почему найдено/i }));
    expect(screen.getByText(result.why_included)).toBeVisible();
    expect(screen.getByText(/достоверность 91%/i)).toBeVisible();
  });

  it("does not turn an unsafe source URL into a link", () => {
    render(
      <SearchResults
        items={[
          {
            ...result,
            article_id: 124,
            title: "Непроверенная ссылка",
            url: "javascript:alert(1)",
            story: null,
          },
        ]}
      />,
    );

    expect(
      screen.queryByRole("link", { name: /открыть источник/i }),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/ссылка на источник недоступна/i)).toBeVisible();
  });
});
