import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DecisionEvidence, DecisionWorkspaceResponse, NewsLead } from "@/lib/decisionTypes";
import DecisionWorkspace from "./DecisionWorkspace";

const mocks = vi.hoisted(() => ({ decisionWorkspace: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: mocks }));

const evidence: DecisionEvidence = {
  article_id: 42,
  title_ru: "Сербия обсуждает визовый порядок",
  title_original: "Serbia discusses visa rules",
  url: "https://example.org/article",
  publisher_name: "Газета",
  publisher_country_code: "RS",
  published_at: "2026-09-30T07:00:00Z",
  collected_at: "2026-09-30T09:00:00Z",
  russia_explanation_ru: "В сообщении названы граждане России.",
  russia_evidence_quote: "Russian citizens",
  country_evidence_quote: "Serbia",
  summary_ru: "По сообщению газеты, предложение находится на обсуждении.",
  kind: "decision",
};

const lead: NewsLead = {
  article_id: 77,
  title_ru: null,
  title_original: "Србија и Русија разговарају",
  url: "https://example.rs/news",
  publisher_name: "Dnevnik",
  publisher_country_code: "RS",
  published_at: "2026-09-30T08:00:00Z",
  collected_at: "2026-09-30T09:00:00Z",
  topic: "diplomacy",
  event_type: "analysis",
  actor_type: "government",
  russia_relation: "uncertain",
  status: "needs_review",
  countries: ["RS", "RU"],
};

function response(code = "RS", name = "Сербия"): DecisionWorkspaceResponse {
  return {
    as_of: "2026-09-30T10:00:00Z",
    country: { code, name, region: "Европа" },
    countries: [{ code: "RS", name: "Сербия", region: "Европа" }, { code: "AE", name: "ОАЭ", region: "Азия" }],
    attention: [{ code: "RS", name: "Сербия", count_24h: 2, count_7d: 4, reason: "Новые сообщения", latest_at: "2026-09-30T07:00:00Z" }],
    brief: { day: [evidence], week: [evidence] },
    positions: [{ id: "position-1", actor: "Ведомство", actor_type: "government", position_ru: "Сообщается, что ведомство изучает предложение.", evidence_quote: "proposal", evidence }],
    changes: [{ id: "change-1", category: "travel", change_ru: "Предлагается изменить визовые правила для граждан РФ.", evidence_quote: "Russian citizens", evidence }],
    topics: [{ id: "topic-1", title: "Визовый порядок", question: "Когда может быть принято решение?", evidence: [evidence] }],
    coverage: { collected_from_country_7d: 12, reviewed_from_country_7d: 4, relevant_to_country_7d: 2, publisher_families: 2, local_publisher_families: 1, last_collected_at: "2026-09-30T09:00:00Z", last_published_at: "2026-09-30T07:00:00Z", last_analyzed_at: "2026-09-30T09:30:00Z", independent_confirmation: "not_assessed", truncated: false, limitations: ["Охвачены не все издатели."] },
  };
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  mocks.decisionWorkspace.mockReset();
  mocks.decisionWorkspace.mockResolvedValue(response());
});
afterEach(() => vi.useRealTimers());

describe("DecisionWorkspace", () => {
  it("puts the map first and retains source dates, uncertainty and detail sections", async () => {
    render(<DecisionWorkspace />);
    expect(await screen.findByText("Сербия ↔ Россия")).toBeVisible();
    expect(screen.getByRole("region", { name: "Карта отношений России и мира" })).toBeVisible();
    for (const heading of ["Где требуется внимание", "Страна за 60 секунд", "Насколько полна картина"]) {
      expect(screen.getByText(heading)).toBeVisible();
    }
    for (const heading of ["Кто какую позицию занимает", "Что меняется для российских граждан и организаций", "Темы для разговора"]) {
      expect(screen.getByText(new RegExp(heading))).toBeVisible();
    }
    expect(screen.getByText(/Связь с Россией и значение события ещё требуют проверки/)).toBeVisible();
    expect(screen.getByText(/Независимость подтверждений не оценивалась/)).toBeVisible();
    expect(screen.getByText(/Охвачены не все издатели/)).toBeVisible();
    const citation = screen.getAllByText("Цитата и источник")[0].closest("details");
    expect(citation).not.toBeNull();
    expect(within(citation!).getByText(/Опубликовано:/)).toHaveTextContent("собрано:");
    expect(within(citation!).getByRole("link", { name: /Открыть публикацию/ })).toHaveAttribute("href", "https://example.org/article");
    expect(window.location.search).toBe("?country=RS");
  });

  it("selects and requests the country from a lowercase URL code", async () => {
    window.history.replaceState(null, "", "/?country=rs");
    render(<DecisionWorkspace />);
    expect(await screen.findByText("Сербия ↔ Россия")).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Выбранная страна" })).toHaveValue("RS");
    expect(mocks.decisionWorkspace).toHaveBeenCalledWith("RS", expect.any(AbortSignal));
  });

  it("uses the map selection in the same country request and URL", async () => {
    mocks.decisionWorkspace.mockImplementation((country: string | null) => Promise.resolve(
      country === "AE" ? response("AE", "ОАЭ") : response(),
    ));
    render(<DecisionWorkspace renderMap={({ selectedCountry, onSelectCountry }) =>
      <button type="button" onClick={() => onSelectCountry("AE")}>Карта: {selectedCountry ?? "нет выбора"}</button>
    } />);
    expect(await screen.findByText("Карта: RS")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Карта: RS" }));
    expect(await screen.findByText("ОАЭ ↔ Россия")).toBeVisible();
    expect(screen.getByText("Карта: AE")).toBeVisible();
    expect(window.location.search).toBe("?country=AE");
    expect(mocks.decisionWorkspace).toHaveBeenNthCalledWith(2, "AE", expect.any(AbortSignal));
  });

  it("keeps the country in the URL and ignores an older response after switching", async () => {
    let finishOld!: (value: DecisionWorkspaceResponse) => void;
    mocks.decisionWorkspace.mockImplementation((country: string | null) => {
      if (country === "AE") return new Promise<DecisionWorkspaceResponse>((resolve) => { finishOld = resolve; });
      if (country === "RS") return Promise.resolve(response());
      return Promise.resolve(response());
    });
    render(<DecisionWorkspace />);
    await screen.findByText("Сербия ↔ Россия");
    fireEvent.change(screen.getByRole("combobox", { name: "Выбранная страна" }), { target: { value: "AE" } });
    expect(screen.getByRole("status")).toHaveTextContent("Собираем срез");
    fireEvent.change(screen.getByRole("combobox", { name: "Выбранная страна" }), { target: { value: "RS" } });
    expect(await screen.findByText("Сербия ↔ Россия")).toBeVisible();
    await act(async () => finishOld(response("AE", "ОАЭ")));
    expect(screen.queryByText("ОАЭ ↔ Россия")).not.toBeInTheDocument();
    expect(window.location.search).toBe("?country=RS");
  });

  it("retries an error and retains country choices", async () => {
    mocks.decisionWorkspace.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(response());
    render(<DecisionWorkspace />);
    const alert = await screen.findByRole("alert");
    fireEvent.click(within(alert).getByRole("button", { name: "Повторить загрузку" }));
    expect(await screen.findByText("Сербия ↔ Россия")).toBeVisible();
    expect(mocks.decisionWorkspace).toHaveBeenCalledTimes(2);
  });

  it("does not create a link for unsafe evidence URLs and explains empty evidence", async () => {
    const value = response();
    value.brief = { day: [], week: [] };
    value.positions = [];
    value.changes = [];
    value.topics = [{ id: "topic-1", title: "Вопрос", question: "Что известно?", evidence: [{ ...evidence, url: "javascript:alert(1)" }] }];
    value.coverage.truncated = true;
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    expect(await screen.findByText("Сербия ↔ Россия")).toBeVisible();
    expect(screen.getByText(/За выбранный период среди обработанных источников нет публикаций/)).toBeVisible();
    fireEvent.click(screen.getByText(/Кто какую позицию занимает/));
    fireEvent.click(screen.getByText(/Темы для разговора/));
    expect(screen.getByText(/Модель не выделила явно атрибутированных позиций/)).toBeVisible();
    expect(screen.getByText(/Выборка ограничена/)).toBeVisible();
    expect(screen.queryByRole("link", { name: /Открыть публикацию/ })).not.toBeInTheDocument();
    expect(screen.getByText("Ссылка на оригинал недоступна.")).toBeInTheDocument();
  });

  it("refreshes the currently selected country manually", async () => {
    const newer = response();
    newer.as_of = "2026-09-30T11:00:00Z";
    mocks.decisionWorkspace.mockResolvedValueOnce(response()).mockResolvedValueOnce(newer);
    render(<DecisionWorkspace />);
    await screen.findByText("Сербия ↔ Россия");
    fireEvent.click(screen.getByRole("button", { name: "Обновить срез" }));
    expect(await screen.findByText(/срез .*11:00 UTC/)).toBeVisible();
    expect(mocks.decisionWorkspace).toHaveBeenNthCalledWith(2, "RS", expect.any(AbortSignal));
  });

  it("checks every two minutes and keeps the old snapshot visible if refresh fails", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    let rejectRefresh!: (error: Error) => void;
    mocks.decisionWorkspace.mockResolvedValueOnce(response()).mockReturnValueOnce(new Promise((_, reject) => { rejectRefresh = reject; }));
    render(<DecisionWorkspace />);
    await screen.findByText("Сербия ↔ Россия");
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(mocks.decisionWorkspace).toHaveBeenCalledTimes(2);
    expect(mocks.decisionWorkspace).toHaveBeenNthCalledWith(2, "RS", expect.any(AbortSignal));
    expect(screen.getByText("Сербия ↔ Россия")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("Обновляем срез");
    expect(screen.getByRole("button", { name: "Обновить срез" })).toBeDisabled();
    await act(async () => rejectRefresh(new Error("offline")));
    expect(screen.getByRole("alert")).toHaveTextContent("Показаны данные от");
    expect(screen.getByText("Сербия ↔ Россия")).toBeVisible();
  });

  it("keeps possible leads separate from checked conclusions and follows the selected period", async () => {
    const value = response();
    value.discovery = { day: [lead], week: [{ ...lead, article_id: 78, title_ru: "Переговоры Сербии и России" }] };
    value.coverage.classified_from_country_7d = 8;
    value.coverage.pending_from_country_7d = 4;
    value.coverage.discovered_to_country_7d = 15;
    value.coverage.triage_status = "partial";
    value.coverage.last_classified_at = "2026-09-30T09:15:00Z";
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    const discovery = await screen.findByRole("region", { name: /Возможные повестки/ });
    expect(within(discovery).getByText("Дипломатия: анализ")).toBeVisible();
    expect(within(discovery).getByText("Тема: Дипломатия")).toBeVisible();
    expect(within(discovery).getByText("Тип: анализ")).toBeVisible();
    expect(within(discovery).getByText("Связь с Россией: требует уточнения")).toBeVisible();
    expect(within(discovery).getByText("Перевод готовится")).toBeVisible();
    expect(within(discovery).getByText("Требует проверки")).toBeVisible();
    expect(within(discovery).getByText("Србија и Русија разговарају").closest("details")).not.toHaveAttribute("open");
    expect(within(discovery).getByRole("link", { name: /Открыть публикацию/ })).toHaveAttribute("href", "https://example.rs/news");
    expect(within(screen.getByRole("region", { name: /Страна за 60 секунд/ })).queryByText("Дипломатия: анализ")).not.toBeInTheDocument();
    expect(screen.getByText("Ожидает разметки").nextElementSibling).toHaveTextContent("4");
    expect(screen.getByText(/Кандидаты о стране из всех источников/)).toHaveTextContent("15");
    fireEvent.change(screen.getByRole("combobox", { name: "Период материалов" }), { target: { value: "week" } });
    expect(within(discovery).getByText("Переговоры Сербии и России")).toBeVisible();
    expect(within(discovery).queryByText("Перевод готовится")).not.toBeInTheDocument();
  });

  it("shows pending and unavailable discovery honestly and never links unsafe lead URLs", async () => {
    const value = response();
    value.discovery = { day: [{ ...lead, url: "javascript:alert(1)" }], week: [] };
    value.attention[0].status = "needs_review";
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    const discovery = await screen.findByRole("region", { name: /Возможные повестки/ });
    fireEvent.click(within(discovery).getByText("Оригинал и источник"));
    expect(within(discovery).getByText("Ссылка на оригинал недоступна.")).toBeVisible();
    expect(within(discovery).queryByRole("link", { name: /Открыть публикацию/ })).not.toBeInTheDocument();
    expect(screen.getAllByText("Требует проверки")).toHaveLength(2);
    fireEvent.change(screen.getByRole("combobox", { name: "Период материалов" }), { target: { value: "week" } });
    expect(within(discovery).getByText(/не означает отсутствия событий/)).toBeVisible();
  });

  it("does not show a previous country's leads while the next country loads", async () => {
    const serbia = response();
    serbia.discovery = { day: [lead], week: [lead] };
    mocks.decisionWorkspace.mockImplementation((country: string | null) => country === "AE"
      ? new Promise<DecisionWorkspaceResponse>(() => {}) : Promise.resolve(serbia));
    render(<DecisionWorkspace />);
    expect(await screen.findByText("Дипломатия: анализ")).toBeVisible();
    fireEvent.change(screen.getByRole("combobox", { name: "Выбранная страна" }), { target: { value: "AE" } });
    expect(screen.queryByText("Дипломатия: анализ")).not.toBeInTheDocument();
  });

  it("labels each backend topic, event, and relation even for translated titles", async () => {
    const value = response();
    const topics = ["sanctions", "travel", "business", "education", "culture", "security", "diplomacy", "other"];
    const events = ["statement", "proposal", "decision", "incident", "analysis", "other"];
    value.discovery = { day: topics.map((topic, index) => ({
      ...lead, article_id: index + 100, title_ru: `Перевод ${index}`, topic,
      event_type: events[index % events.length],
      russia_relation: (["direct", "indirect", "uncertain"] as const)[index % 3],
    })), week: [] };
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    const discovery = await screen.findByRole("region", { name: /Возможные повестки/ });
    for (const topic of ["Санкции", "Поездки", "Бизнес", "Образование", "Культура", "Безопасность", "Дипломатия", "Другие темы"]) {
      expect(within(discovery).getByText(`Тема: ${topic}`)).toBeVisible();
    }
    for (const event of ["заявление", "предложение", "решение", "происшествие", "анализ", "другое сообщение"]) {
      expect(within(discovery).getAllByText(`Тип: ${event}`)[0]).toBeVisible();
    }
    expect(within(discovery).getAllByText("Связь с Россией: прямая")).toHaveLength(3);
    expect(within(discovery).getAllByText("Связь с Россией: косвенная")).toHaveLength(3);
    expect(within(discovery).getAllByText("Связь с Россией: требует уточнения")).toHaveLength(2);
  });

  it("keeps unassigned NATO news visible in a general stream without attributing it to Serbia", async () => {
    const value = response();
    const natoLead: NewsLead = { ...lead, article_id: 201, title_ru: "НАТО обсуждает отношения с Россией", title_original: "NATO discusses Russia", countries: [] };
    value.discovery = {
      day: [], week: [],
      unassigned_day: [natoLead, ...[202, 203, 204, 205].map((article_id) => ({ ...natoLead, article_id, title_ru: `Общий материал ${article_id}` }))],
      unassigned_week: [{ ...natoLead, article_id: 206, title_ru: "Недельный общий материал" }],
    };
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    const discovery = await screen.findByRole("region", { name: /Возможные повестки/ });
    expect(within(discovery).getByText("По выбранной стране: Сербия")).toBeVisible();
    expect(within(discovery).getByText(/По выбранной стране за этот период кандидаты пока не найдены/)).toBeVisible();
    const general = within(discovery).getByRole("group", { name: /Международные темы без привязки к стране/ });
    expect(within(general).getByText("НАТО обсуждает отношения с Россией")).not.toBeVisible();
    fireEvent.click(within(general).getByText(/Международные темы без привязки к стране/));
    expect(within(general).getByText(/Общий поток, не только выбранная страна/)).toBeVisible();
    expect(within(general).getByText("НАТО обсуждает отношения с Россией")).toBeVisible();
    expect(within(general).getAllByText("Требует проверки")).toHaveLength(5);
    expect(within(general).getByText("Общий материал 205")).not.toBeVisible();
    fireEvent.click(within(general).getByText("Показать ещё 1"));
    expect(within(general).getByText("Общий материал 205")).toBeVisible();
    fireEvent.change(screen.getByRole("combobox", { name: "Период материалов" }), { target: { value: "week" } });
    expect(within(general).getByText("Недельный общий материал")).toBeVisible();
    expect(within(general).queryByText("НАТО обсуждает отношения с Россией")).not.toBeInTheDocument();
  });
});
