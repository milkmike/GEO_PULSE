import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import SectionHeader from "./SectionHeader";

describe("SectionHeader", () => {
  it("gives a section a heading, description and optional destination", () => {
    const { rerender } = render(<SectionHeader title="Изменения" description="За сутки" action={{ href: "/signals", label: "все" }} />);
    expect(screen.getByRole("heading", { level: 2, name: "Изменения" })).toHaveClass("text-xl");
    expect(screen.getByText("За сутки")).toHaveClass("text-sm", "text-dim");
    expect(screen.getByRole("link", { name: "все" })).toHaveAttribute("href", "/signals");
    rerender(<SectionHeader title="Страны" />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });
});
