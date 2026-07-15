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
};

describe("SignalFeed", () => {
  beforeEach(() => { motionState.reduced = false; });

  it("shows concrete list facts without a detail request and links only when enabled", () => {
    const { rerender } = render(<SignalFeed signals={[signal]} detailEnabled />);

    expect(screen.getByRole("link", { name: /Индекс Испании изменился на 8 пунктов/i })).toHaveAttribute("href", "/signals/17");
    expect(screen.getByText("Испания")).toBeVisible();
    expect(screen.getByText(/скачок индекса/i)).toBeVisible();
    expect(screen.getByText(/уверенность 82%/i)).toBeVisible();
    expect(screen.getByText(/сдвиг за 24 часа/i).closest("li")).toHaveTextContent("+8");
    expect(screen.getByText(/статей за 24 часа/i).closest("li")).toHaveTextContent("17");
    expect(screen.getByText(/база в день/i).closest("li")).toHaveTextContent("6");
    expect(screen.queryByText(/nested/i)).not.toBeInTheDocument();

    rerender(<SignalFeed signals={[signal]} detailEnabled={false} />);
    expect(screen.queryByRole("link", { name: /Индекс Испании изменился/i })).not.toBeInTheDocument();
    expect(screen.getByRole("article", { name: /Индекс Испании изменился/i })).toBeVisible();
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
});
