import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ListRow from "./ListRow";

describe("ListRow", () => {
  it("makes the whole row an accessible link with supporting information", () => {
    render(<ListRow href="/country/RS" leading="🇷🇸" title="Сербия" meta="Балканы" trailing="+4" />);
    const link = screen.getByRole("link", { name: /Сербия/ });
    expect(link).toHaveAttribute("href", "/country/RS");
    expect(link).toHaveClass("min-h-9", "hover:bg-panel2", "focus-visible:outline-accent");
    expect(link).toHaveTextContent("Балканы");
    expect(link).toHaveTextContent("+4");
  });
});
