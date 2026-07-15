import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SignalListItem } from "@/lib/types";
import SignalFeed from "./SignalFeed";

const motionState = vi.hoisted(() => ({ reduced: false }));
vi.mock("motion/react", async () => {
  const React = await import("react");
  return {
    useReducedMotion: () => motionState.reduced,
    motion: {
      div: React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
        ({ children, ...props }, ref) => <div ref={ref} {...props}>{children}</div>,
      ),
    },
  };
});

const signal: SignalListItem = {
  id: 17,
  type: "index_shift",
  country_code: "ES",
  country_name: "Испания",
  severity: "warning",
  confidence: 0.82,
  title: "Индекс Испании изменился на 8 пунктов",
  description: "Сдвиг сохранённой дневной точки RRI за сутки.",
  payload: { delta_24h: 8, articles_24h: 17, baseline_daily: 6, nested: { ignored: true } },
  created_at: "2026-07-15T20:00:00Z",
  expires_at: "2026-07-20T20:00:00Z",
  active: true,
  evidence_preview: null,
};

const contextSignal: SignalListItem = {
  ...signal,
  evidence_preview: {
    kind: "context",
    total: 47,
    window_hours: 72,
    window_start: "2026-07-12T20:00:00Z",
    window_end: "2026-07-15T20:00:00Z",
    articles: [
      {
        id: 501,
        title: "Правительство прокомментировало отношения с Россией",
        url: "https://example.es/story",
        published_at: "2026-07-15T01:30:00Z",
        source_name: "Ejemplo",
        country_code: "ES",
      },
      {
        id: 502,
        title: "Парламент обсудил новый дипломатический курс",
        url: "https://example.es/second-story",
        published_at: "2026-07-14T18:00:00Z",
        source_name: "Diario",
        country_code: "ES",
      },
    ],
  },
};

describe("SignalFeed", () => {
  beforeEach(() => { motionState.reduced = false; });

  it("keeps the card semantic and exposes a separate detail action", () => {
    const { rerender } = render(<SignalFeed signals={[signal]} detailEnabled />);

    const card = screen.getByRole("article", { name: /Индекс Испании изменился на 8 пунктов/i });
    expect(card.closest("a")).toBeNull();
    expect(card.className).not.toContain("hover:bg-panel");
    expect(screen.getByRole("heading", { level: 3, name: signal.title })).toBeVisible();
    expect(screen.getByRole("link", { name: /Открыть разбор сигнала/i })).toHaveAttribute("href", "/signals/17");
    expect(screen.getByText("Испания")).toBeVisible();
    expect(screen.getByText(/скачок индекса/i)).toBeVisible();
    expect(screen.getByText(/уверенность 82%/i)).toBeVisible();
    expect(screen.getByText(/сдвиг за 24 часа/i).closest("li")).toHaveTextContent("+8");
    expect(screen.getByText(/статей за 24 часа/i).closest("li")).toHaveTextContent("17");
    expect(screen.getByText(/база в день/i).closest("li")).toHaveTextContent("6");
    expect(screen.queryByText(/nested/i)).not.toBeInTheDocument();

    rerender(<SignalFeed signals={[signal]} detailEnabled={false} />);
    expect(screen.queryByRole("link", { name: /Открыть разбор сигнала/i })).not.toBeInTheDocument();
    expect(screen.getByRole("article", { name: /Индекс Испании изменился/i })).toBeVisible();
  });

  it("shows compact contextual sources without nesting links", () => {
    render(<SignalFeed signals={[contextSignal]} detailEnabled />);

    const card = screen.getByRole("article", { name: /Индекс Испании изменился/i });
    expect(screen.getByText("Публикации в окне сигнала")).toBeVisible();
    expect(screen.getByText("Контекст для проверки; причинная связь не установлена.")).toBeVisible();
    expect(screen.getByText("2 из 47 релевантных публикаций")).toBeVisible();

    const source = screen.getByRole("link", {
      name: /Открыть первоисточник: Правительство.*Ejemplo.*откроется в новой вкладке/i,
    });
    expect(source).toHaveAttribute("href", "https://example.es/story");
    expect(source).toHaveAttribute("target", "_blank");
    expect(source).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(source).toHaveClass("min-h-11", "focus-visible:outline-2");
    expect(screen.getByText("Правительство прокомментировало отношения с Россией")).toHaveClass("line-clamp-2");

    const detail = screen.getByRole("link", { name: /Открыть разбор сигнала/i });
    expect(detail).toHaveAttribute("href", "/signals/17");
    expect(detail).toHaveClass("min-h-11", "w-full", "focus-visible:outline-2");
    expect(source.contains(detail)).toBe(false);
    expect(detail.contains(source)).toBe(false);
    expect(card.querySelectorAll("a")).toHaveLength(3);
  });

  it("keeps source links when details are disabled and labels exact evidence", () => {
    const exact: SignalListItem = {
      ...contextSignal,
      evidence_preview: { ...contextSignal.evidence_preview!, kind: "evidence", total: 2, window_hours: null },
    };
    render(<SignalFeed signals={[exact]} detailEnabled={false} />);

    expect(screen.getByText("На чём основан сигнал")).toBeVisible();
    expect(screen.getAllByRole("link")).toHaveLength(2);
    expect(screen.queryByRole("link", { name: /Открыть разбор сигнала/i })).not.toBeInTheDocument();
    expect(screen.queryByText(/причинная связь не установлена/i)).not.toBeInTheDocument();
  });

  it("distinguishes empty context, unavailable previews and URL-less publications", () => {
    const emptyContext: SignalListItem = {
      ...signal,
      id: 18,
      title: "Пустой контекст",
      evidence_preview: {
        kind: "context",
        articles: [],
        total: 0,
        window_hours: 72,
        window_start: null,
        window_end: null,
      },
    };
    const unavailable: SignalListItem = { ...signal, id: 19, title: "Нет превью", evidence_preview: null };
    const urlLess: SignalListItem = {
      ...signal,
      id: 20,
      title: "Источник без ссылки",
      evidence_preview: {
        kind: "evidence",
        articles: [{
          id: 503,
          title: "Сохранённый заголовок",
          url: null,
          published_at: null,
          source_name: "Архив",
          country_code: "ES",
        }],
        total: 1,
        window_hours: null,
        window_start: null,
        window_end: null,
      },
    };

    render(<SignalFeed signals={[emptyContext, unavailable, urlLess]} detailEnabled />);

    expect(screen.getByText("В сохранённом 72-часовом окне релевантные публикации не найдены.")).toBeVisible();
    expect(screen.getByText("Публикации для этого сигнала не найдены или не сохранились.")).toBeVisible();
    expect(screen.getByText("Ссылка на первоисточник не сохранена.")).toBeVisible();
    expect(screen.getByText("Сохранённый заголовок").closest("a")).toBeNull();
  });

  it("keeps the same evidence text with reduced motion and does not invent unknown state", () => {
    const reducedSignal = { ...signal, active: undefined, expires_at: undefined };
    const { container, rerender } = render(<SignalFeed signals={[reducedSignal]} detailEnabled />);
    const animatedText = container.textContent;

    motionState.reduced = true;
    rerender(<SignalFeed signals={[reducedSignal]} detailEnabled />);
    expect(container.textContent).toBe(animatedText);
    expect(screen.queryByText(/активен|истёк/i)).not.toBeInTheDocument();
  });

  it("keeps zero and false facts visible with readable live-payload labels", () => {
    render(<SignalFeed signals={[{
      ...signal,
      type: "official_silence",
      payload: { loud_articles: 0, media_preceded: false, share: 0 },
    }]} />);

    expect(screen.getByText(/публикаций вне официальных СМИ/i).closest("li")).toHaveTextContent("0");
    expect(screen.getByText(/медиа-сигнал был раньше/i).closest("li")).toHaveTextContent("нет");
    expect(screen.getByText("доля").closest("li")).toHaveTextContent("0%");
  });

  it("localizes sanctions signals plus event and source-tier values", () => {
    render(<SignalFeed signals={[{
      ...signal,
      type: "sanctions_escalation",
      payload: {
        event_type: "military",
        tiers: [
          "official", "mainstream", "independent", "social",
          "domestic_opposition", "western_proxy", "analytics",
        ],
        official_or_mainstream_sources_available: false,
      },
    }]} />);

    expect(screen.getByText(/санкционное ужесточение/i)).toBeVisible();
    expect(screen.getByText("тип события").closest("li")).toHaveTextContent("военное");
    expect(screen.getByText("тиры").closest("li")).toHaveTextContent(
      "официальные источники, крупные СМИ, независимые СМИ, социальные сети, внутренняя оппозиция, западные прокси-источники, аналитика",
    );
    expect(screen.getByText(/доступны официальные или крупные СМИ/i).closest("li")).toHaveTextContent("нет");
    expect(screen.queryByText(/military|official|analytics|sanctions_escalation/i)).not.toBeInTheDocument();
  });
});
