import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Card from "./Card";

describe("Card", () => {
  it("groups sections within one border and preserves accessibility attributes", () => {
    render(<Card role="region" aria-label="Сводка" className="p-4"><p>Главное</p><p>Источники</p></Card>);
    const card = screen.getByRole("region", { name: "Сводка" });
    expect(card).toHaveClass("card", "divide-y", "divide-line", "p-4");
    expect(card.querySelectorAll(".card")).toHaveLength(0);
    expect(card.children).toHaveLength(2);
  });
});
