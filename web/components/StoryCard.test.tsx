import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { StoryListItem } from "@/lib/types";
import StoryCard from "./StoryCard";
import StoriesPanel from "./StoriesPanel";
import StoryTimeline from "./StoryTimeline";

const story = {
  id: 42,
  slug: "spain-port-talks",
  title_ru: "Испания и Россия обсуждают портовую логистику",
  title_en: null,
  summary: "Один сюжет объединяет сообщения испанских и российских источников.",
  lifecycle: "developing",
  first_seen: "2026-07-13T09:00:00+00:00",
  last_seen: "2026-07-15T12:00:00+00:00",
  article_count: 8,
  source_count: 5,
  country_count: 2,
  highest_action_level: 3,
  clustering_confidence: 0.84,
  generated_at: "2026-07-15T12:10:00+00:00",
  countries: ["ES", "RU"],
  primary_url: "https://elpais.com/mundo/port-talks",
  why_included: ["cross_country", "active_lifecycle"],
  relevance_score: 0.82,
  confidence: 0.84,
  evidence: { topics: ["diplomacy", "trade"] },
  linked_signal_count: 2,
  linked_signals: [
    {
      id: 9,
      type: "index_shift",
      severity: "warning",
      title: "Сдвиг индекса Испании",
      created_at: "2026-07-15T11:30:00+00:00",
      confidence: 0.8,
      completeness: "complete",
      relation: "explicit_story_evidence",
      evidence: { source: "signal_evidence.story_ids", story_id: 42 },
    },
  ],
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
} satisfies StoryListItem;

describe("StoryCard", () => {
  it("shows the evidence dossier without presenting temporal RRI context as a cause", () => {
    render(<StoryCard story={story} />);

    expect(screen.getByRole("link", { name: story.title_ru })).toHaveAttribute(
      "href",
      "/stories/42",
    );
    expect(screen.getByText("развивается")).toBeVisible();
    expect(screen.getByText(/Испания · Россия/)).toBeVisible();
    expect(screen.getByText(/8 публикаций/)).toBeVisible();
    expect(screen.getByText(/5 источников/)).toBeVisible();
    expect(screen.getByText(/2 сигнала/)).toBeVisible();
    expect(screen.getByText(/уровень события 3 · значимый/i)).toBeVisible();
    expect(screen.getByText(/Контекст RRI/)).toHaveTextContent("−8,0");
    expect(screen.getByText(/не доказывает причинность/i)).toBeVisible();

    const source = screen.getByRole("link", { name: /первоисточник/i });
    expect(source).toHaveAttribute("href", story.primary_url);
    expect(source).toHaveAttribute("rel", expect.stringContaining("noopener"));
  });

  it("keeps the same durable story id in global and country placements", () => {
    render(
      <>
        <StoryCard story={story} placement="global" />
        <StoryCard story={story} placement="country" countryCode="ES" />
      </>,
    );

    const links = screen.getAllByRole("link", { name: story.title_ru });
    expect(links).toHaveLength(2);
    expect(links.every((link) => link.getAttribute("href") === "/stories/42")).toBe(true);
  });

  it("shows the country-local article count and tone in a country placement", () => {
    render(
      <StoryCard
        story={{
          ...story,
          country_context: {
            country_code: "ES",
            country_name: "Испания",
            article_count: 3,
            source_count: 2,
            media_tone: -1.25,
          },
        }}
        placement="country"
        countryCode="ES"
      />,
    );

    expect(screen.getByText(/Испания · 3 публикации · 2 источника · тон −1,3/i)).toBeVisible();
  });

  it("does not expose an unsafe primary URL", () => {
    render(<StoryCard story={{ ...story, primary_url: "javascript:alert(1)" }} />);

    expect(screen.queryByRole("link", { name: /первоисточник/i })).not.toBeInTheDocument();
    expect(screen.getByText(/ссылка недоступна/i)).toBeVisible();
  });
});

describe("StoryTimeline", () => {
  it("renders a chronological, keyboard-readable evidence trail and safe source links", () => {
    render(
      <StoryTimeline
        events={[
          {
            entity_id: "event-2",
            event_key: "Совместное заявление",
            event_at: "2026-07-15T11:00:00+00:00",
            action_level: 3,
            confidence: 0.8,
            evidence: { article_ids: [2] },
          },
          {
            entity_id: "event-1",
            event_key: "Начало переговоров",
            event_at: "2026-07-13T09:00:00+00:00",
            action_level: 2,
            confidence: 0.9,
            evidence: { article_ids: [1] },
          },
        ]}
        articles={[
          {
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
          },
          {
            article_id: 2,
            title: "Небезопасная ссылка",
            url: "https://reader:secret@example.com/private",
            published_at: "2026-07-15T11:05:00+00:00",
            source: "Источник",
            country_code: "RU",
            membership_confidence: 0.8,
            evidence: {},
            is_primary: true,
            why_included: ["story_membership"],
            relevance_score: 0.8,
            confidence: 0.8,
          },
        ]}
      />,
    );

    const timeline = screen.getByRole("list", { name: /хронология сюжета/i });
    const items = within(timeline).getAllByRole("listitem");
    expect(items[0]).toHaveTextContent("Начало переговоров");
    expect(items.at(-1)).toHaveTextContent("Небезопасная ссылка");
    expect(screen.getByRole("link", { name: /El País: переговоры начались/i })).toHaveAttribute(
      "href",
      "https://elpais.com/mundo/talks",
    );
    expect(screen.getByText("Небезопасная ссылка").closest("a")).toBeNull();
  });
});

describe("StoriesPanel", () => {
  it("keeps an explicit empty state on country pages", () => {
    render(
      <StoriesPanel
        stories={[]}
        title="Сюжеты с участием Испании"
        countryCode="ES"
        state="ready"
      />,
    );

    expect(screen.getByRole("region", { name: /Сюжеты с участием Испании/i })).toBeVisible();
    expect(screen.getByText(/пока нет межстрановых сюжетов/i)).toBeVisible();
  });
});
