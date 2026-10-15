import { describe, expect, it } from "vitest";
import { countryNameRu, levelRu, localizeLevelTokens, pluralRu, severityRu, storySummaryRu } from "./format";

describe("Russian labels for stored copy", () => {
  it("replaces raw RRI level codes in signal descriptions and AI briefs", () => {
    expect(localizeLevelTokens("сейчас +41.4 [partner].")).toBe("сейчас +41.4 (партнёр).");
    expect(localizeLevelTokens("индекс перешёл в зону «cooling», уровень «tension»"))
      .toBe("индекс перешёл в зону «охлаждение», уровень «напряжение»");
    expect(localizeLevelTokens("partner program [beta]")).toBe("partner program [beta]");
  });

  it("maps levels and severities, keeping unknown codes visible", () => {
    expect(levelRu("hostile")).toBe("враждебность");
    expect(levelRu("unknown")).toBe("unknown");
    expect(severityRu("info")).toBe("наблюдение");
    expect(severityRu("other")).toBe("other");
  });

  it("chooses Russian plural forms", () => {
    expect([1, 2, 4, 5, 11, 12, 21, 22, 25].map((n) => pluralRu(n, "публикация", "публикации", "публикаций")))
      .toEqual(["публикация", "публикации", "публикации", "публикаций", "публикаций", "публикаций", "публикация", "публикации", "публикаций"]);
  });

  it("names countries instead of ISO codes", () => {
    expect(countryNameRu("JP")).toBe("Япония");
    expect(countryNameRu("not-a-code")).toBe("not-a-code");
  });

  it("rewrites the legacy story summary template and leaves written summaries alone", () => {
    expect(storySummaryRu("Сюжет объединяет 4 публикаций из стран JP, US по событию «Russia Detains Japanese Journalist»."))
      .toBe("Сюжет объединяет 4 публикации о событии «Russia Detains Japanese Journalist». Страны: Япония, Соединенные Штаты.");
    const written = "СМИ Кыргызстана и Таджикистана сообщили о заявлении Пашиняна.";
    expect(storySummaryRu(written)).toBe(written);
  });
});
