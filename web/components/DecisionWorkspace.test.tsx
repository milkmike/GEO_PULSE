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
    const value = response();
    value.discovery = { day: [lead], week: [] };
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    expect(await screen.findByText("Сербия ↔ Россия")).toBeVisible();
    expect(screen.getByRole("region", { name: "Карта отношений России и мира" })).toBeVisible();
    for (const heading of ["Где требуется внимание", "Страна за 60 секунд", "Что вошло в обзор"]) {
      expect(screen.getByText(heading)).toBeVisible();
    }
    for (const heading of ["Кто какую позицию занимает", "Что меняется для российских граждан и организаций", "Темы для разговора"]) {
      expect(screen.getByText(new RegExp(heading))).toBeVisible();
    }
    expect(screen.getByText("Что пишут СМИ о стране и России. Сообщения ещё не проверены.")).toBeVisible();
    const coverageDetails = screen.getByText("Как собраны данные").closest("details");
    expect(coverageDetails).not.toHaveAttribute("open");
    fireEvent.click(within(coverageDetails!).getByText("Как собраны данные"));
    expect(within(coverageDetails!).getByText(/Независимость подтверждений не оценивалась/)).toBeVisible();
    expect(within(coverageDetails!).getByText(/Охвачены не все издатели/)).toBeVisible();
    const citation = screen.getAllByText("Цитата и источник")[0].closest("details");
    expect(citation).not.toBeNull();
    fireEvent.click(within(citation!).getByText("Цитата и источник"));
    expect(within(citation!).getByText(/Опубликовано:/)).toHaveTextContent("собрано:");
    expect(within(citation!).getByText(/Опубликовано:/)).toHaveTextContent("мск");
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
    expect(screen.getByRole("status")).toHaveTextContent("Загружаем обзор");
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

  it("hides empty sections and does not create a link for unsafe evidence URLs", async () => {
    const value = response();
    value.brief = { day: [], week: [] };
    value.positions = [];
    value.changes = [];
    value.topics = [{ id: "topic-1", title: "Вопрос", question: "Что известно?", evidence: [{ ...evidence, url: "javascript:alert(1)" }] }];
    value.coverage.truncated = true;
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    expect(await screen.findByText("Сербия ↔ Россия")).toBeVisible();
    expect(screen.queryByText("Страна за 60 секунд")).not.toBeInTheDocument();
    expect(screen.queryByText(/Кто какую позицию занимает/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Что меняется для российских граждан/)).not.toBeInTheDocument();
    expect(screen.queryByText("В новостях")).not.toBeInTheDocument();
    fireEvent.click(screen.getByText(/Темы для разговора/));
    expect(screen.queryByText(/По выбранной стране за этот период материалов пока нет/)).not.toBeInTheDocument();
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
    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
    expect(await screen.findByText(/Обновлено .*14:00 мск/)).toBeVisible();
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
    expect(screen.getByRole("status")).toHaveTextContent("Обновляем обзор");
    expect(screen.getByRole("button", { name: "Обновить" })).toBeDisabled();
    await act(async () => rejectRefresh(new Error("offline")));
    expect(screen.getByRole("alert")).toHaveTextContent("Показаны данные от");
    expect(screen.getByText("Сербия ↔ Россия")).toBeVisible();
  });

  it("keeps translated headlines readable and moves machine labels into details", async () => {
    const value = response();
    value.discovery = { day: [{ ...lead, title_ru: "Переговоры Сербии и России" }], week: [] };
    value.coverage.classified_from_country_7d = 8;
    value.coverage.pending_from_country_7d = 4;
    value.coverage.discovered_to_country_7d = 15;
    value.coverage.triage_status = "partial";
    value.coverage.last_classified_at = "2026-09-30T09:15:00Z";
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    const discovery = await screen.findByRole("region", { name: /В новостях/ });
    expect(within(discovery).getByText("Переговоры Сербии и России")).toBeVisible();
    expect(within(discovery).getByText("Что пишут СМИ о стране и России. Сообщения ещё не проверены.")).toBeVisible();
    expect(within(discovery).queryByText("Требует проверки")).not.toBeInTheDocument();
    expect(within(discovery).queryByText("Перевод готовится")).not.toBeInTheDocument();
    expect(within(discovery).queryByText("Дипломатия: анализ")).not.toBeInTheDocument();
    expect(within(discovery).getByRole("link", { name: "Открыть статью" })).toHaveAttribute("href", "https://example.rs/news");
    expect(within(discovery).getByText(/11:00 мск/)).toBeVisible();
    const details = within(discovery).getByText("О публикации").closest("details");
    expect(details).not.toHaveAttribute("open");
    expect(within(details!).getByText("Тема: Дипломатия")).not.toBeVisible();
    fireEvent.click(within(details!).getByText("О публикации"));
    expect(within(details!).getByText("Тема: Дипломатия")).toBeVisible();
    expect(within(details!).getByText("Тип: анализ")).toBeVisible();
    expect(within(details!).getByText("Связь с Россией: требует уточнения")).toBeVisible();
    expect(within(details!).getByText("Србија и Русија разговарају")).toBeVisible();
    expect(screen.getByText("Как собраны данные").closest("details")).not.toHaveAttribute("open");
    expect(screen.getByText("Публикаций за неделю").nextElementSibling).toHaveTextContent("12");
    expect(screen.getByText("Групп источников").nextElementSibling).toHaveTextContent("1");
    fireEvent.change(screen.getByRole("combobox", { name: "Период материалов" }), { target: { value: "week" } });
    expect(screen.queryByRole("region", { name: /В новостях/ })).not.toBeInTheDocument();
  });

  it("shows pending and unavailable discovery honestly and never links unsafe lead URLs", async () => {
    const value = response();
    value.discovery = { day: [{ ...lead, url: "javascript:alert(1)" }], week: [] };
    value.attention[0].status = "needs_review";
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    const discovery = await screen.findByRole("region", { name: /В новостях/ });
    expect(within(discovery).getByText("Русского перевода пока нет. Можно прочитать публикации на языке источника.")).toBeVisible();
    fireEvent.click(within(discovery).getByRole("button", { name: "На языке источника (1)" }));
    expect(within(discovery).getByText("Србија и Русија разговарају")).toBeVisible();
    fireEvent.click(within(discovery).getByText("О публикации"));
    expect(within(discovery).getByText("Ссылка на оригинал недоступна.")).toBeVisible();
    expect(within(discovery).queryByRole("link", { name: "Открыть статью" })).not.toBeInTheDocument();
    expect(within(discovery).queryByText("Перевод готовится")).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: "Период материалов" }), { target: { value: "week" } });
    expect(screen.queryByRole("region", { name: /В новостях/ })).not.toBeInTheDocument();
  });

  it("does not show a previous country's leads while the next country loads", async () => {
    const serbia = response();
    serbia.discovery = { day: [lead], week: [lead] };
    mocks.decisionWorkspace.mockImplementation((country: string | null) => country === "AE"
      ? new Promise<DecisionWorkspaceResponse>(() => {}) : Promise.resolve(serbia));
    render(<DecisionWorkspace />);
    expect(await screen.findByRole("button", { name: "На языке источника (1)" })).toBeVisible();
    fireEvent.change(screen.getByRole("combobox", { name: "Выбранная страна" }), { target: { value: "AE" } });
    expect(screen.queryByRole("button", { name: "На языке источника (1)" })).not.toBeInTheDocument();
  });

  it("preserves topic, event, and relation labels for translated titles inside details", async () => {
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
    const discovery = await screen.findByRole("region", { name: /В новостях/ });
    expect(within(discovery).getByText("Перевод 0")).toBeVisible();
    expect(within(discovery).getByText("Что пишут СМИ о стране и России. Сообщения ещё не проверены.")).toBeVisible();
    expect(within(discovery).queryByText("Требует проверки")).not.toBeInTheDocument();
    expect(within(discovery).queryByText("Перевод 4")).not.toBeInTheDocument();
    fireEvent.click(within(discovery).getByRole("button", { name: "Показать ещё 4" }));
    const detailSummaries = within(discovery).getAllByText("О публикации");
    expect(detailSummaries).toHaveLength(topics.length);
    for (const summary of detailSummaries) fireEvent.click(summary);
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
    await screen.findByText("Сербия ↔ Россия");
    expect(screen.queryByRole("region", { name: /В новостях/ })).not.toBeInTheDocument();
    const general = screen.getByRole("region", { name: "В мире" });
    expect(document.getElementById("country-overview")).not.toContainElement(general);
    expect(within(general).getByText("Новости о России из разных стран. Связь этих публикаций с выбранной страной не установлена.")).toBeVisible();
    expect(within(general).getByText("НАТО обсуждает отношения с Россией")).toBeVisible();
    expect(within(general).queryByText("Требует проверки")).not.toBeInTheDocument();
    expect(within(general).queryByText("Общий материал 205")).not.toBeInTheDocument();
    fireEvent.click(within(general).getByRole("button", { name: "Показать ещё 2" }));
    expect(within(general).getByText("Общий материал 205")).toBeVisible();
    expect(within(general).queryByRole("button", { name: /Показать ещё/ })).not.toBeInTheDocument();
  });

  it("shows one country empty message while retaining coverage warnings and a separate global stream", async () => {
    const value = response();
    value.brief = { day: [], week: [] };
    value.positions = [];
    value.changes = [];
    value.topics = [];
    value.discovery = { day: [], week: [], unassigned_day: [{ ...lead, countries: [], title_ru: "Общий материал" }] };
    value.coverage.triage_status = "budget_exhausted";
    value.coverage.truncated = true;
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    expect(await screen.findByText("Сербия ↔ Россия")).toBeVisible();
    expect(screen.getAllByText(/По выбранной стране за этот период материалов пока нет/)).toHaveLength(1);
    for (const heading of ["В новостях", "Страна за 60 секунд", "Кто какую позицию занимает", "Что меняется для российских граждан и организаций", "Темы для разговора"]) {
      expect(screen.queryByText(new RegExp(heading))).not.toBeInTheDocument();
    }
    expect(screen.getByText("Обработка приостановлена: достигнут лимит.")).toBeVisible();
    expect(screen.getByText(/Выборка ограничена/)).toBeVisible();
    const general = screen.getByRole("region", { name: "В мире" });
    expect(within(general).getByText("Общий материал")).toBeVisible();
  });

  it("reports unavailable discovery separately from empty country materials", async () => {
    const value = response();
    value.discovery = undefined;
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    expect(await screen.findByText("Сербия ↔ Россия")).toBeVisible();
    expect(screen.getByText("Не удалось загрузить сообщения по стране.")).toBeVisible();
    expect(screen.getByText("Страна за 60 секунд")).toBeVisible();
    expect(screen.queryByText(/По выбранной стране за этот период материалов пока нет/)).not.toBeInTheDocument();
    expect(screen.queryByText("В новостях")).not.toBeInTheDocument();
  });

  it("resets the source-language choice after changing country", async () => {
    const serbia = response();
    serbia.discovery = { day: [lead], week: [] };
    const emirates = response("AE", "ОАЭ");
    emirates.discovery = { day: [{ ...lead, article_id: 88, title_original: "Новость ОАЭ", publisher_country_code: "AE", countries: ["AE"] }], week: [] };
    mocks.decisionWorkspace.mockImplementation((country: string | null) => Promise.resolve(country === "AE" ? emirates : serbia));
    render(<DecisionWorkspace />);
    const firstGroup = await screen.findByRole("button", { name: "На языке источника (1)" });
    fireEvent.click(firstGroup);
    expect(screen.getByText("Србија и Русија разговарају")).toBeVisible();
    fireEvent.change(screen.getByRole("combobox", { name: "Выбранная страна" }), { target: { value: "AE" } });
    expect(await screen.findByText("ОАЭ ↔ Россия")).toBeVisible();
    const secondGroup = screen.getByRole("button", { name: "На языке источника (1)" });
    expect(secondGroup).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByText("Србија и Русија разговарају")).not.toBeInTheDocument();
    fireEvent.click(secondGroup);
    expect(screen.getByText("Новость ОАЭ")).toBeVisible();
  });

  it("keeps country and world language choices independent and resets them for a new period", async () => {
    const value = response();
    const translated = { ...lead, article_id: 90, title_ru: "Русский заголовок" };
    const original = { ...lead, article_id: 91, title_original: "Country original" };
    const worldOriginal = { ...lead, article_id: 92, title_original: "World original", countries: [] };
    value.discovery = {
      day: [translated, original], week: [translated, original],
      unassigned_day: [worldOriginal], unassigned_week: [worldOriginal],
    };
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    const country = await screen.findByRole("region", { name: /В новостях/ });
    const world = screen.getByRole("region", { name: "В мире" });
    fireEvent.click(within(country).getByRole("button", { name: "На языке источника (1)" }));
    expect(within(country).getByText("Country original")).toBeVisible();
    expect(within(world).getByRole("button", { name: "На языке источника (1)" })).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(within(world).getByRole("button", { name: "На языке источника (1)" }));
    expect(within(world).getByText("World original")).toBeVisible();
    fireEvent.change(screen.getByRole("combobox", { name: "Период материалов" }), { target: { value: "week" } });
    expect(within(country).getByRole("button", { name: "На русском (1)" })).toHaveAttribute("aria-pressed", "true");
    expect(within(country).getByText("Русский заголовок")).toBeVisible();
    expect(within(world).getByRole("button", { name: "На языке источника (1)" })).toHaveAttribute("aria-pressed", "false");
    expect(within(world).queryByText("World original")).not.toBeInTheDocument();
  });

  it("keeps the processing limit warning visible while detailed coverage is collapsed", async () => {
    const value = response();
    value.coverage.triage_status = "budget_exhausted";
    value.coverage.pending_from_country_7d = 4;
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    await screen.findByText("Сербия ↔ Россия");
    expect(screen.getByText("Обработка приостановлена: достигнут лимит.")).toBeVisible();
    expect(screen.getByText("Как собраны данные").closest("details")).not.toHaveAttribute("open");
  });
});
