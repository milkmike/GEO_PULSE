import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import Stat from "./Stat";

describe("Stat", () => {
  it("labels the number and supplies its change and context", () => {
    render(<Stat label="Индекс" value="42" delta={2} hint="За семь дней" />);
    expect(screen.getByText("Индекс")).toHaveClass("text-xs");
    expect(screen.getByText("42")).toHaveClass("tnum");
    expect(screen.getByTitle("за неделю: +2.0")).toBeVisible();
    expect(screen.getByText("За семь дней")).toBeVisible();
  });
});
