import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SignalDetail } from "@/lib/types";
import SignalEvidence from "./SignalEvidence";

vi.mock("@/components/Plot", () => ({ default: () => <div data-testid="signal-chart">chart</div> }));

const fmtContextTime = (value: string) => new Date(value).toLocaleString("ru-RU", {
  day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
});

const complete: SignalDetail = {
  id: 17,
  type: "index_shift",
  severity: "warning",
  summary: {
    headline: "Индекс Испании изменился на 8 пунктов",
    description: "Сохранённый сдвиг RRI.",
    what_changed: "RRI: −10 → −2 за 24 часа",
  },
  rule: {
    detector: "index_shift",
    version: "2.1",
    description: "Срабатывает при заметном изменении индекса.",
    threshold: { delta: 6, enabled: false },
    current_rule_reference: { delta: 7 },
  },
  values: {
    observed: { delta: 8, article_count: 0 },
    baseline: { delta: 1 },
    window: {
      start: "2026-07-14T20:00:00Z",
      end: "2026-07-15T20:00:00Z",
      basis: "persisted_trigger_window",
      status: "exact",
    },
  },
  chart_points: [
    { time: "2026-07-14T20:00:00Z", score: -10 },
    { time: "2026-07-15T20:00:00Z", score: -2 },
    { time: "bad", score: "no" },
  ],
  articles: [{
    id: 1, title: "El País: переговоры", url: "https://elpais.com/mundo/talks",
    published_at: "2026-07-15T19:00:00Z", source_name: "El País", country_code: "ES",
    sentiment: -0.5, action_level: 3, event_key: "talks",
  }],
  context_preview: null,
  articles_page: { total: 1, returned: 1, limit: 100, truncated: false, has_more: false },
  related_story: {
    id: 42, slug: "talks", title: "Переговоры в Испании", summary: "Межстрановой сюжет.",
    lifecycle: "developing", last_seen: "2026-07-15T20:00:00Z", confidence: 0.84,
  },
  countries: [{ code: "ES", name: "Испания", article_count: 3, media_tone: -1.2 }],
  state: { created_at: "2026-07-15T20:00:00Z", expires_at: "2026-07-20T20:00:00Z", active: true, status: "active" },
  confidence: 0.82,
  evidence_completeness: "complete",
  evidence_ids: ["signal:17", "article:1"],
  evidence: { article_ids: [1], story_ids: [42], rri_points: [] },
  evidence_truncation: {
    evidence_ids: { total: 2, returned: 2, truncated: false },
    article_ids: { total: 1, returned: 1, truncated: false },
    story_ids: { total: 1, returned: 1, truncated: false },
    rri_points: { total: 2, returned: 2, truncated: false },
    countries: { total: 1, returned: 1, truncated: false },
  },
  limitations: ["contextual_proximity_is_not_causation"],
};

describe("SignalEvidence", () => {
  it("renders concrete rule, values, exact window, chart and primary evidence", () => {
    render(<SignalEvidence detail={complete} storiesEnabled />);

    expect(screen.getByRole("heading", { name: complete.summary.headline })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Как сработал детектор" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Почему сработал сигнал" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Публикации-доказательства" })).toBeVisible();
    expect(screen.getByText(/RRI: −10 → −2/i)).toBeVisible();
    expect(screen.getByText(/уверенность 82%/i)).toBeVisible();
    expect(screen.getAllByText(/активен/i).length).toBeGreaterThanOrEqual(1);
    const state = screen.getByRole("region", { name: /состояние сигнала/i });
    expect(state.querySelector('time[datetime="2026-07-15T20:00:00Z"]')).not.toBeNull();
    expect(state.querySelector('time[datetime="2026-07-20T20:00:00Z"]')).not.toBeNull();
    expect(state).toHaveTextContent(/текущий статус.*активен/i);
    expect(screen.getByTestId("signal-chart")).toBeVisible();

    const observed = screen.getByRole("region", { name: /наблюдаемое значение/i });
    expect(within(observed).getByText("Изменение").closest("li")).toHaveTextContent("8");
    expect(within(observed).getByText("Число публикаций").closest("li")).toHaveTextContent("0");
    const threshold = screen.getByRole("region", { name: /порог срабатывания/i });
    expect(within(threshold).getByText("Включено").closest("li")).toHaveTextContent("нет");
    expect(screen.getByText(/текущее правило, не исторический порог/i)).toBeVisible();

    const article = screen.getByRole("link", { name: /El País: переговоры/i });
    expect(article).toHaveAttribute("href", "https://elpais.com/mundo/talks");
    expect(article).toHaveAttribute("rel", expect.stringContaining("noopener"));
    expect(screen.getByRole("link", { name: /Переговоры в Испании/i })).toHaveAttribute("href", "/stories/42");
    expect(screen.getByText(/не доказывает причинность/i)).toBeVisible();
  });

  it("renders a legacy GDELT observation window without tying it to signal creation", () => {
    const windowStart = "2026-07-13T00:00:00Z";
    const windowEnd = "2026-07-16T00:00:00Z";
    const contextual: SignalDetail = {
      ...complete,
      type: "tone_shift",
      state: {
        ...complete.state,
        created_at: "2026-07-15T12:00:00Z",
      },
      articles: [],
      articles_page: { total: 0, returned: 0, limit: 100, truncated: false, has_more: false },
      context_preview: {
        kind: "context",
        total: 8,
        window_hours: 72,
        window_start: windowStart,
        window_end: windowEnd,
        articles: [{
          id: 501,
          title: "Контекст сдвига",
          url: "https://example.es/context",
          published_at: "2026-07-15T18:00:00Z",
          source_name: "Ejemplo",
          country_code: "ES",
        }],
      },
    };

    render(<SignalEvidence detail={contextual} storiesEnabled />);

    expect(screen.getByRole("heading", { name: "Новостной контекст" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Контекст сдвига" })).toHaveAttribute(
      "href",
      "https://example.es/context",
    );
    expect(screen.getByText(
      "Публикации отобраны в 72-часовом окне наблюдаемого периода как возможный контекст. Они не доказывают причину сдвига.",
    )).toBeVisible();
    expect(screen.getByText(
      `Окно контекста: ${fmtContextTime(windowStart)} — ${fmtContextTime(windowEnd)}`,
    )).toBeVisible();
    expect(screen.queryByText(/до сигнала|до срабатывания/i)).not.toBeInTheDocument();
    expect(screen.getByText("Показано 1 из 8")).toBeVisible();
    expect(screen.getByText(
      "Отобраны по уровню события, выраженности тона, числу перепечаток и времени публикации.",
    )).toBeVisible();
  });

  it("distinguishes empty context from unavailable context", () => {
    const noMatches: SignalDetail = {
      ...complete,
      articles: [],
      articles_page: { total: 0, returned: 0, limit: 100, truncated: false, has_more: false },
      context_preview: {
        kind: "context",
        articles: [],
        total: 0,
        window_hours: 72,
        window_start: "2026-07-12T20:00:00Z",
        window_end: "2026-07-15T20:00:00Z",
      },
    };
    const { rerender } = render(<SignalEvidence detail={noMatches} storiesEnabled />);

    expect(screen.getByText("В сохранённом 72-часовом окне релевантные публикации не найдены.")).toBeVisible();

    rerender(<SignalEvidence detail={{
      ...noMatches,
      context_preview: {
        kind: "unavailable",
        articles: [],
        total: 0,
        window_hours: null,
        window_start: null,
        window_end: null,
      },
    }} storiesEnabled />);

    expect(screen.getByText("Новостной контекст для этого сигнала недоступен.")).toBeVisible();
    expect(screen.queryByText("В сохранённом 72-часовом окне релевантные публикации не найдены.")).not.toBeInTheDocument();
  });

  it("keeps partial missing inputs honest and never exposes unsafe links or fake controls", () => {
    const partial: SignalDetail = {
      ...complete,
      evidence_completeness: "partial",
      rule: { ...complete.rule, threshold: {}, current_rule_reference: null },
      values: {
        observed: { value: 0, enabled: false },
        baseline: {},
        window: { start: null, end: null, basis: "not_persisted", status: "unknown" },
      },
      chart_points: [{ time: "2026-07-15T20:00:00Z", score: -2 }],
      articles: [{ ...complete.articles[0], title: "Опасная ссылка", url: "https://reader:secret@example.com/private" }],
      articles_page: { total: 140, returned: 1, limit: 100, truncated: true, has_more: true },
    };
    render(<SignalEvidence detail={partial} storiesEnabled={false} />);

    expect(screen.getByText(/частичные доказательства/i)).toBeVisible();
    expect(screen.getAllByText(/не сохранено/i).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/график доказательства не сохранён/i)).toBeVisible();
    expect(screen.getByText("Опасная ссылка").closest("a")).toBeNull();
    expect(screen.getByText("Ссылка на первоисточник не сохранена.")).toBeVisible();
    expect(screen.getByText(/показана 1 из 140/i)).toBeVisible();
    expect(screen.queryByRole("button", { name: /загрузить ещё/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Переговоры в Испании/i })).not.toBeInTheDocument();
    expect(screen.getByText("Переговоры в Испании")).toBeVisible();
    expect(within(screen.getByRole("region", { name: /наблюдаемое значение/i })).getByText(/Параметр доказательства «value»/i).closest("li")).toHaveTextContent("0");
  });

  it("distinguishes a not-applicable baseline from missing evidence", () => {
    render(<SignalEvidence detail={{ ...complete, values: { ...complete.values, baseline: { type: "not_applicable" } } }} storiesEnabled />);
    expect(screen.getByRole("region", { name: /базовое значение/i })).toHaveTextContent("сравнение не требуется");
  });

  it("translates live evidence fields, value codes, window metadata and missing state timestamps", () => {
    const liveCodes: SignalDetail = {
      ...complete,
      rule: {
        ...complete.rule,
        threshold: {
          absolute_delta_min: 7,
          absolute_delta_sanity_max: 18,
          minimum_daily_volume: 10,
        },
      },
      values: {
        observed: {
          delta_24h: -8,
          preceding_media_signal_count: 0,
          media_lookback_hours: 72,
        },
        baseline: {
          type: "rri_point",
          status: "reconstructed_from_signal_payload",
          comparison_hours: 24,
          standard_deviation: 1.5,
        },
        window: { start: null, end: null, basis: "not_persisted", status: "unknown" },
      },
      state: { created_at: null, expires_at: null, active: false, status: "expired" },
      limitations: ["threshold_not_persisted"],
    };
    render(<SignalEvidence detail={liveCodes} storiesEnabled />);

    expect(screen.getByText("Минимальный абсолютный сдвиг")).toBeVisible();
    expect(screen.getByText("Максимальный допустимый сдвиг")).toBeVisible();
    expect(screen.getByText("Число предшествующих медиасигналов")).toBeVisible();
    expect(screen.getByText("Глубина поиска медиасигналов, часов")).toBeVisible();
    expect(screen.getByText("Сохранённая точка RRI")).toBeVisible();
    expect(screen.getByText("Восстановлено из payload сигнала")).toBeVisible();
    expect(screen.getByText(/окно детектора не сохранялось/i)).toBeVisible();
    expect(screen.getByText(/статус окна неизвестен/i)).toBeVisible();
    const state = screen.getByRole("region", { name: /состояние сигнала/i });
    expect(within(state).getAllByText("не сохранено")).toHaveLength(2);
    expect(state).toHaveTextContent(/текущий статус.*истёк/i);
    expect(screen.queryByText("absolute_delta_min")).not.toBeInTheDocument();
    expect(screen.queryByText("reconstructed_from_signal_payload")).not.toBeInTheDocument();
  });

  it.each([
    ["military", "военное"],
    ["diplomatic", "дипломатическое"],
    ["security", "безопасность"],
    ["economic", "экономическое"],
    ["cultural", "культурное"],
  ])("localizes the reconstructed event value %s", (code, label) => {
    render(<SignalEvidence detail={{
      ...complete,
      values: { ...complete.values, observed: { event_type: code } },
    }} storiesEnabled />);

    expect(screen.getByText(label)).toBeVisible();
    expect(screen.queryByText(code)).not.toBeInTheDocument();
  });

  it.each([
    ["official", "официальные источники"],
    ["mainstream", "крупные СМИ"],
    ["independent", "независимые СМИ"],
    ["social", "социальные сети"],
    ["domestic_opposition", "внутренняя оппозиция"],
    ["western_proxy", "западные прокси-источники"],
    ["analytics", "аналитика"],
  ])("localizes the reconstructed source tier %s", (code, label) => {
    render(<SignalEvidence detail={{
      ...complete,
      values: { ...complete.values, observed: { tier: code } },
    }} storiesEnabled />);

    expect(screen.getByText(label)).toBeVisible();
    expect(screen.queryByText(code)).not.toBeInTheDocument();
  });

  it("fully localizes reconstructed legacy evidence fields, events and source tiers", () => {
    const legacy: SignalDetail = {
      ...complete,
      values: {
        observed: {
          articles: 12,
          avg_sentiment: -1.2,
          max_action_level: 4,
          event_type: "diplomatic",
          tiers: ["official", "mainstream", "independent", "social"],
        },
        baseline: {
          baseline_daily: 3,
          mean_90d: -0.4,
          std: 0.8,
          baseline_share: 0.15,
          volume: 12,
          change_1d_pct: 2.5,
          official_or_mainstream_sources_available: true,
          tier: "analytics",
        },
        window: complete.values.window,
      },
    };
    render(<SignalEvidence detail={legacy} storiesEnabled />);

    const page = document.body;
    for (const label of [
      "Число публикаций",
      "Средняя тональность",
      "Максимальный уровень действия",
      "Среднее число публикаций в день",
      "Среднее за 90 дней",
      "Обычное отклонение",
      "Базовая доля повестки",
      "Публикаций в день",
      "Изменение курса за день, %",
      "Доступны официальные или крупные СМИ",
    ]) {
      expect(within(page).getByText(label)).toBeVisible();
    }
    expect(screen.getByText("дипломатическое")).toBeVisible();
    expect(screen.getByText(/официальные источники, крупные СМИ, независимые СМИ, социальные сети/i)).toBeVisible();
    expect(screen.getByText("аналитика")).toBeVisible();
    expect(screen.queryByText(/avg_sentiment|mean_90d|change_1d_pct|diplomatic|mainstream/i)).not.toBeInTheDocument();
  });
});
