import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { TopicBriefResponse } from "@/lib/types";
import HomePage from "./page";

const apiMocks = vi.hoisted(() => ({
  countries: vi.fn(),
  signals: vi.fn(),
  worldBrief: vi.fn(),
  meta: vi.fn(),
  worldHeadlines: vi.fn(),
  topicCountries: vi.fn(),
  topicBrief: vi.fn(),
  stories: vi.fn(),
}));

vi.mock("@/lib/api", () => ({ api: apiMocks }));
vi.mock("@/components/SiteHeader", () => ({ default: () => <nav>header</nav> }));
vi.mock("@/components/SortableGrid", () => ({
  default: ({ items }: { items: { id: string; node: ReactNode }[] }) => (
    <>{items.map((item) => <div key={item.id}>{item.node}</div>)}</>
  ),
}));
vi.mock("@/components/Filters", () => ({
  default: ({ value, onChange }: {
    value: { region: string | null; level: string | null; topic: string | null };
    onChange: (next: { region: string | null; level: string | null; topic: string | null }) => void;
  }) => (
    <button type="button" onClick={() => onChange({ ...value, topic: "culture_sport" })}>
      Выбрать культуру и спорт
    </button>
  ),
}));
vi.mock("@/components/Markdown", () => ({
  default: ({ text }: { text: string }) => <div>{text}</div>,
}));
vi.mock("@/components/CountryRanking", () => ({ default: () => null }));
vi.mock("@/components/HealthBadge", () => ({ default: () => null }));
vi.mock("@/components/HeadlinesFeed", () => ({ default: () => null }));
vi.mock("@/components/RadarPanel", () => ({ default: () => null }));
vi.mock("@/components/SignalFeed", () => ({ default: () => null }));
vi.mock("@/components/StoriesPanel", () => ({ default: () => null }));
vi.mock("@/components/WorldMap", () => ({ default: () => null }));

async function selectTopic() {
  fireEvent.click(await screen.findByRole("button", { name: "Выбрать культуру и спорт" }));
}

async function renderWithTopicResponse(response: TopicBriefResponse) {
  apiMocks.topicBrief.mockResolvedValue(response);
  render(<HomePage />);
  await selectTopic();
}

describe("thematic brief states", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    apiMocks.countries.mockResolvedValue({ countries: [], total: 0 });
    apiMocks.signals.mockResolvedValue({ signals: [] });
    apiMocks.worldBrief.mockRejectedValue(new Error("empty"));
    apiMocks.meta.mockResolvedValue({
      countries: [],
      topics: { culture_sport: "Культура и спорт" },
      regions: {},
      levels: [],
    });
    apiMocks.worldHeadlines.mockResolvedValue({ headlines: [], total: 0 });
    apiMocks.topicCountries.mockResolvedValue({ label: "Культура и спорт", countries: [] });
  });

  it("shows that a pending thematic brief is updating", async () => {
    await renderWithTopicResponse({
      status: "pending",
      topic: "culture_sport",
      label: "Культура и спорт",
    });

    expect(await screen.findByText("Тематический брифинг обновляется")).toBeVisible();
  });

  it("shows an explicit insufficient-data state", async () => {
    await renderWithTopicResponse({
      status: "insufficient",
      topic: "culture_sport",
      label: "Культура и спорт",
    });

    expect(await screen.findByText("Недостаточно данных по теме")).toBeVisible();
  });

  it("renders a ready brief and its metadata", async () => {
    await renderWithTopicResponse({
      status: "ready",
      topic: "culture_sport",
      label: "Культура и спорт",
      content: "Свежий брифинг",
      model: "qwen",
      created_at: "2026-07-16T13:00:00Z",
      cached: true,
      citations: [],
    });

    expect(await screen.findByText("Свежий брифинг")).toBeVisible();
    expect(screen.getByText(/qwen ·/)).toBeVisible();
  });

  it("distinguishes a network failure from insufficient topic data", async () => {
    apiMocks.topicBrief.mockRejectedValue(new Error("network down"));
    render(<HomePage />);
    await selectTopic();

    expect(await screen.findByText("Не удалось загрузить тематический брифинг")).toBeVisible();
    await waitFor(() => {
      expect(screen.queryByText("Недостаточно данных по теме")).not.toBeInTheDocument();
    });
  });
});
