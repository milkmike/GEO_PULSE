import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { StoryDetailResponse } from "@/lib/types";
import StoryDetailPage from "./page";
import { FeatureFlagsProvider } from "@/components/FeatureFlagsProvider";

const apiMocks = vi.hoisted(() => ({ story: vi.fn() }));

vi.mock("@/lib/api", () => ({ api: apiMocks }));

const detail: StoryDetailResponse = {
  id: 42,
  slug: "spain-port-talks",
  title_ru: "Испания и Россия обсуждают портовую логистику",
  title_en: null,
  summary: "Публикации двух стран описывают один раунд переговоров.",
  lifecycle: "developing",
  first_seen: "2026-07-13T09:00:00+00:00",
  last_seen: "2026-07-15T12:00:00+00:00",
  article_count: 3,
  source_count: 3,
  country_count: 2,
  highest_action_level: 3,
  clustering_confidence: 0.84,
  generated_at: "2026-07-15T12:10:00+00:00",
  primary_url: "https://elpais.com/mundo/port-talks",
  why_included: ["cross_country", "active_lifecycle"],
  relevance_score: 0.82,
  confidence: 0.84,
  evidence: { merge_audit: { entity_overlap: 0.76 }, topics: ["diplomacy"] },
  linked_signal_count: 1,
  linked_signals: [{
    id: 9,
    type: "index_shift",
    severity: "warning",
    title: "Сдвиг индекса Испании",
    created_at: "2026-07-15T11:30:00+00:00",
    confidence: 0.8,
    completeness: "complete",
    relation: "explicit_story_evidence",
    evidence: { source: "signal_evidence.story_ids", story_id: 42 },
  }],
  latest_rri_shift: {
    country_code: "ES",
    country_name: "Испания",
    at: "2026-07-15T10:00:00+00:00",
    score: -12,
    delta_24h: -8,
    version: "v1",
    relation: "temporal_context",
    why_included: "rri_point_within_story_window",
    limitation: "Временное совпадение с сюжетом не доказывает причинность.",
  },
  rri_shifts: [
    {
      country_code: "ES",
      country_name: "Испания",
      at: "2026-07-15T10:00:00+00:00",
      score: -12,
      delta_24h: -8,
      version: "v1",
      relation: "temporal_context",
      why_included: "rri_point_within_story_window",
      limitation: "Временное совпадение с сюжетом не доказывает причинность.",
    },
    {
      country_code: "RU",
      country_name: "Россия",
      at: "2026-07-14T10:00:00+00:00",
      score: 7,
      delta_24h: 4,
      version: "v1",
      relation: "temporal_context",
      why_included: "rri_point_within_story_window",
      limitation: "Временное совпадение с сюжетом не доказывает причинность.",
    },
  ],
  countries: [
    {
      country_code: "ES",
      country_name: "Испания",
      article_count: 2,
      source_count: 2,
      media_tone: -1.4,
      first_seen: "2026-07-13T09:00:00+00:00",
      last_seen: "2026-07-15T12:00:00+00:00",
      primary_url: "https://elpais.com/mundo/port-talks",
    },
    {
      country_code: "RU",
      country_name: "Россия",
      article_count: 1,
      source_count: 1,
      media_tone: 0.4,
      first_seen: "2026-07-13T10:00:00+00:00",
      last_seen: "2026-07-14T12:00:00+00:00",
      primary_url: "javascript:alert(1)",
    },
  ],
  entities: [{
    entity_id: "person:putin",
    canonical_name: "Владимир Путин",
    kind: "person",
    mentions: 3,
    confidence: 0.91,
    evidence: { article_ids: [1, 2] },
  }],
  events: [{
    entity_id: "event:talks",
    event_key: "Начало переговоров",
    event_at: "2026-07-13T09:00:00+00:00",
    action_level: 2,
    confidence: 0.9,
    evidence: { article_ids: [1] },
  }],
  articles: [{
    article_id: 1,
    title: "El País: переговоры начались",
    url: "https://elpais.com/mundo/talks",
    published_at: "2026-07-13T09:05:00+00:00",
    source: "El País",
    country_code: "ES",
    membership_confidence: 0.91,
    evidence: { matched_features: ["event_key", "entities"] },
    is_primary: true,
    why_included: ["matched_event_key", "matched_entities"],
    relevance_score: 0.9,
    confidence: 0.91,
  }],
  articles_next_cursor: "next-page",
  redirected_from_story_id: null,
};

describe("StoryDetailPage", () => {
  beforeEach(() => apiMocks.story.mockReset());

  it("explains the story evidence and labels RRI as non-causal context", async () => {
    apiMocks.story.mockResolvedValue(detail);
    await act(async () => {
      render(<StoryDetailPage params={Promise.resolve({ id: "42" })} />);
    });

    expect(await screen.findByRole("heading", { level: 1, name: detail.title_ru })).toBeVisible();
    expect(screen.getByText(detail.summary!)).toBeVisible();
    expect(screen.getByRole("link", { name: "Испания" })).toBeVisible();
    expect(screen.getByText("тон −1,4")).toBeVisible();
    expect(screen.getByText(/Владимир Путин/i)).toBeVisible();
    expect(screen.queryByRole("link", { name: /Владимир Путин/i })).not.toBeInTheDocument();
    expect(screen.getByText("Сдвиг индекса Испании")).toBeVisible();
    expect(screen.queryByRole("link", { name: /Сдвиг индекса Испании/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Временной контекст RRI/i)).toBeVisible();
    expect(screen.getByText((_, element) => (
      element?.tagName === "P" && element.textContent?.startsWith("Россия · +4") === true
    ))).toBeVisible();
    expect(screen.getAllByText(/не доказывает причинность/i).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/достоверность группировки/i)).toHaveTextContent("84%");
    expect(screen.getByText(/алгоритмическая группировка/i)).toBeVisible();
    expect(screen.getByText(/межстрановое покрытие/i)).toBeVisible();
    expect(screen.getByText(/сюжет продолжает развиваться/i)).toBeVisible();
    expect(screen.queryByText(/cross country|active lifecycle/i)).not.toBeInTheDocument();

    const timeline = screen.getByRole("list", { name: /хронология сюжета/i });
    expect(within(timeline).getByText("Начало переговоров")).toBeVisible();
    expect(within(timeline).getByText(/уровень события 2/i)).toBeVisible();
    expect(within(timeline).getByRole("link", { name: /El País: переговоры начались/i })).toHaveAttribute(
      "href",
      "https://elpais.com/mundo/talks",
    );
    expect(screen.queryByRole("link", { name: /Россия · первоисточник/i })).not.toBeInTheDocument();
  });

  it("links gated destinations only when the server snapshot enables their entry points", async () => {
    apiMocks.story.mockResolvedValue(detail);
    await act(async () => {
      render(
        <FeatureFlagsProvider flags={{ searchNavigation: true, storiesNavigation: true, investigation: false, signalDetail: true }}>
          <StoryDetailPage params={Promise.resolve({ id: "42" })} />
        </FeatureFlagsProvider>,
      );
    });

    expect(await screen.findByRole("link", { name: /Сдвиг индекса Испании/i })).toHaveAttribute("href", "/signals/9");
    expect(screen.getByRole("link", { name: /Владимир Путин/i })).toHaveAttribute(
      "href",
      "/search?entity_id=person%3Aputin&entity_label=%D0%92%D0%BB%D0%B0%D0%B4%D0%B8%D0%BC%D0%B8%D1%80+%D0%9F%D1%83%D1%82%D0%B8%D0%BD",
    );
  });

  it("appends a cursor page without replacing the evidence dossier", async () => {
    const user = userEvent.setup();
    apiMocks.story
      .mockResolvedValueOnce(detail)
      .mockResolvedValueOnce({
        ...detail,
        articles: [{ ...detail.articles[0], article_id: 2, title: "Второй источник" }],
        articles_next_cursor: null,
      });
    await act(async () => {
      render(<StoryDetailPage params={Promise.resolve({ id: "42" })} />);
    });

    await user.click(await screen.findByRole("button", { name: /ещё источники/i }));

    expect((await screen.findAllByText("Второй источник")).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText(detail.summary!)).toBeVisible();
    expect(apiMocks.story).toHaveBeenNthCalledWith(2, 42, "next-page", 25, expect.any(AbortSignal));
  });

  it("rejects a malformed durable id without requesting the API", async () => {
    await act(async () => {
      render(<StoryDetailPage params={Promise.resolve({ id: "not-a-number" })} />);
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(/некорректный идентификатор/i);
    expect(apiMocks.story).not.toHaveBeenCalled();
  });
});
