import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import EmptyLine from "./EmptyLine";

describe("EmptyLine", () => {
  it("shows a concise readable empty state", () => {
    render(<EmptyLine>Изменений нет</EmptyLine>);
    expect(screen.getByText("Изменений нет")).toHaveClass("text-sm", "text-dim");
  });
});
