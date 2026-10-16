import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import FixedPanel from "./FixedPanel";

describe("FixedPanel", () => {
  it("collapses empty asynchronous content and restores the cell when data arrives", async () => {
    const { container, rerender } = render(<FixedPanel id="brief" tip="Подсказка">{null}</FixedPanel>);
    await waitFor(() => expect(container.firstChild).not.toBeVisible());
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
    rerender(<FixedPanel id="brief" tip="Подсказка"><p>Сводка</p></FixedPanel>);
    await waitFor(() => expect(container.firstChild).toBeVisible());
    expect(screen.getByText("Сводка")).toBeVisible();
    expect(container.querySelector("[draggable]")).toBeNull();
    expect(screen.getByRole("tooltip")).toHaveTextContent("Подсказка");
  });
});
