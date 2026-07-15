import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import RriShiftList from "./RriShiftList";

const markers = [{
  fromDay: "2026-07-14",
  day: "2026-07-15",
  fromTime: "2026-07-14T18:00:00Z",
  time: "2026-07-15T19:45:11.123Z",
  fromScore: -10,
  score: -2,
  dailyDelta: 8,
  at: "2026-07-15T19:45:11.123Z",
}];

describe("RriShiftList", () => {
  it("offers each marker as a real keyboard button with exact timestamp", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    render(<RriShiftList markers={markers} onSelect={onSelect} />);

    const list = screen.getByRole("list", { name: /заметные сдвиги RRI/i });
    const button = screen.getByRole("button", { name: /15 июл.*−10,0.*−2,0.*дневными точками RRI.*\+8,0/i });
    expect(list).toContainElement(button);
    await user.tab();
    expect(button).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(onSelect).toHaveBeenCalledWith(markers[0], button);
  });

  it("states explicitly when the period has no visible shift", () => {
    render(<RriShiftList markers={[]} onSelect={() => {}} />);
    expect(screen.getByText(/заметных сдвигов в выбранном периоде нет/i)).toBeVisible();
  });
});
