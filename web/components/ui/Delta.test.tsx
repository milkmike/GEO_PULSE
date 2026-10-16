import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Delta from "./Delta";

describe("Delta", () => {
  it.each([null, 0, .99, -.99, NaN, Infinity])("omits absent and insignificant changes: %s", (value) => {
    const { container } = render(<Delta value={value} />);
    expect(container).toBeEmptyDOMElement();
  });
  it("expresses direction, value and period without relying on color", () => {
    const { rerender } = render(<Delta value={1} />);
    expect(screen.getByTitle("за неделю: +1.0")).toHaveClass("text-partner");
    expect(screen.getByText("▲")).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText("за неделю")).toHaveClass("sr-only");
    rerender(<Delta value={-3.5} period="day" />);
    expect(screen.getByTitle("за сутки: -3.5")).toHaveClass("text-hostile");
    expect(screen.getByText("▼")).toBeVisible();
    expect(screen.getByText("за сутки")).toHaveClass("sr-only");
  });
});
