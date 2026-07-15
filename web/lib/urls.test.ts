import { describe, expect, it } from "vitest";
import { safeHttpUrl } from "./urls";

describe("safeHttpUrl", () => {
  it("accepts ordinary public HTTP URLs and a backend-compatible trailing dot", () => {
    expect(safeHttpUrl("https://example.com/news?id=7")).toBe("https://example.com/news?id=7");
    expect(safeHttpUrl("http://news.example.com./story")).toBe("http://news.example.com./story");
  });

  it.each([
    "javascript:alert(1)",
    "https://reader:secret@example.com/private",
    "https://example.com/line\nbreak",
    "https://example.com/%0Ahidden",
    "https://example.com/%1fhidden",
    "https://example.com/%7Fhidden",
    "https://example..com/story",
    "https://-example.com/story",
    "https://example-.com/story",
    "https://example.com\\@attacker.test/story",
  ])("rejects unsafe URL %s", (value) => {
    expect(safeHttpUrl(value)).toBeNull();
  });
});
