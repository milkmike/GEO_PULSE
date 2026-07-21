import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("dynamic content visibility", () => {
  it("never makes reveal content depend on opacity animation", () => {
    const css = readFileSync(resolve(process.cwd(), "app/globals.css"), "utf8");
    const fadeUp = css.match(/@keyframes fadeUp\s*\{[\s\S]*?\n\}/)?.[0] ?? "";

    expect(fadeUp).not.toContain("opacity:");
  });
});
