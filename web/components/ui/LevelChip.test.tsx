import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { LEVEL_COLOR, LEVEL_RU } from "@/lib/format";
import type { Level } from "@/lib/types";
import LevelChip from "./LevelChip";

describe("LevelChip", () => {
  it.each(Object.keys(LEVEL_RU) as Level[])("localizes and colors %s", (level) => {
    render(<LevelChip level={level} />);
    expect(screen.getByText(LEVEL_RU[level])).toHaveStyle({ color: LEVEL_COLOR[level] });
  });
});
