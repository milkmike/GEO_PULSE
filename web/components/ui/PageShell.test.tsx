import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
vi.mock("@/components/SiteHeader", () => ({ default: ({ active }: { active?: string }) => <header data-active={active}>Шапка</header> }));
import PageShell from "./PageShell";

describe("PageShell", () => {
  it("shares the page width and navigation while leaving the footer to layout", () => {
    const { rerender } = render(<PageShell active="/countries">Страны</PageShell>);
    expect(screen.getByRole("banner")).toHaveAttribute("data-active", "/countries");
    expect(screen.getByRole("main")).toHaveClass("max-w-[var(--container-page)]", "px-4", "pb-16");
    expect(screen.queryByRole("contentinfo")).not.toBeInTheDocument();
    rerender(<PageShell width="read">Методология</PageShell>);
    expect(screen.getByRole("main")).toHaveClass("max-w-[var(--container-read)]");
    expect(screen.getByRole("main")).toHaveTextContent("Методология");
  });
});
