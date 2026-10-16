import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { fmtDate } from "@/lib/format";
import AiNote, { citationSourceCount } from "./AiNote";

describe("AiNote", () => {
  it.each([[1, "источник"], [4, "источника"], [15, "источников"]])("dates and attributes a brief with %s sources", (count, word) => {
    const { container } = render(<AiNote createdAt="2026-10-15T14:40:00Z" sources={Number(count)} model="Модель" />);
    expect(screen.getByTitle("Модель")).toHaveTextContent(`ИИ · ${fmtDate("2026-10-15T14:40:00Z")} · ${count} ${word}`);
    expect(container.querySelector("time")).toHaveAttribute("dateTime", "2026-10-15T14:40:00Z");
  });
  it("does not invent a source count when it is absent", () => {
    render(<AiNote createdAt="2026-10-15T14:40:00Z" />);
    expect(screen.queryByText(/источник/)).not.toBeInTheDocument();
    expect(citationSourceCount(undefined)).toBeUndefined();
    const citation = { n: 1, title: "Публикация", url: "https://example.org/1", source: "Издание", country: "RS" };
    expect(citationSourceCount([citation, { ...citation, n: 2, url: "https://example.org/2" }])).toBe(1);
  });
});
