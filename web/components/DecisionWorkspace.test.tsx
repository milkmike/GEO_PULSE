import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { DecisionEvidence, DecisionWorkspaceResponse, NewsLead } from "@/lib/decisionTypes";
import type { GlobalCoverageResponse } from "@/lib/globalMonitoringTypes";
import DecisionWorkspace from "./DecisionWorkspace";

const mocks = vi.hoisted(() => ({ decisionWorkspace: vi.fn(), earlySignals: vi.fn(), globalCoverage: vi.fn() }));
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
  mocks.earlySignals.mockReset();
  mocks.earlySignals.mockResolvedValue({ as_of: "2026-09-30T10:00:00Z", items: [], notice: null });
  mocks.globalCoverage.mockReset();
  mocks.globalCoverage.mockResolvedValue({ status: "not_started", as_of: null, countries: [], scope_count: 0 });
  mocks.decisionWorkspace.mockResolvedValue(response());
});
afterEach(() => vi.useRealTimers());

const monitoring: GlobalCoverageResponse = {
  as_of: "2026-10-06T10:00:00Z", status: "ok", scope_count: 2,
  countries: [
    { code: "AD", name_ru: "Андорра", configured_sources: 1, working_direct_publishers: 1, sampled_articles_7d: 1, latest_local_published_at: null, latest_local_collected_at: null, coverage_state: "sampled", work: {} },
    { code: "RS", name_ru: "Сербия", configured_sources: 1, working_direct_publishers: 1, sampled_articles_7d: 1, latest_local_published_at: null, latest_local_collected_at: null, coverage_state: "sampled", work: {} },
  ],
  limits: { per_country: 40, window_days: 7, counts_are_bounded: true },
  screening: { status: "ok" }, writer: { status: "disabled" }, notice: "Ограниченная выборка.",
};

describe("DecisionWorkspace", () => {
  it("opens monitoring-only country signals from the URL without changing the legacy country overview", async () => {
    window.history.replaceState(null, "", "/?country=RS&signal_country=AD");
    mocks.globalCoverage.mockResolvedValue(monitoring);
    render(<DecisionWorkspace />);
    expect(await screen.findByText("Сербия ↔ Россия")).toBeVisible();
    expect(mocks.decisionWorkspace).toHaveBeenCalledWith("RS", expect.any(AbortSignal));
    expect(mocks.decisionWorkspace).not.toHaveBeenCalledWith("AD", expect.anything());
    expect(screen.getByRole("combobox", { name: "Выбранная страна" })).toHaveValue("RS");
    expect(await screen.findByText(/опубликованных гипотез пока нет/)).toHaveTextContent("Андорра");
    expect(mocks.earlySignals).toHaveBeenCalledWith("AD", expect.any(AbortSignal));
    fireEvent.click(screen.getByRole("button", { name: "Посмотреть ранние сигналы в мире" }));
    expect(window.location.search).toContain("signal_country=world");
    expect(mocks.earlySignals).toHaveBeenCalledWith(null, expect.any(AbortSignal));
    expect(screen.getByText("Сербия ↔ Россия")).toBeVisible();
  });

  it("ignores an unknown signal-country URL code before querying early signals", async () => {
    window.history.replaceState(null, "", "/?country=RS&signal_country=ZZ");
    mocks.globalCoverage.mockResolvedValue(monitoring);
    render(<DecisionWorkspace />);
    await screen.findByText("Сербия ↔ Россия");
    await waitFor(() => expect(window.location.search).toBe("?country=RS"));
    expect(mocks.earlySignals).not.toHaveBeenCalledWith("ZZ", expect.anything());
  });

  it("uses a coverage-row action for signal-only selection", async () => {
    mocks.globalCoverage.mockResolvedValue(monitoring);
    render(<DecisionWorkspace />);
    await screen.findByText("Сербия ↔ Россия");
    fireEvent.click(await screen.findByText("Какой мир мы видим"));
    fireEvent.click(screen.getByRole("button", { name: "Ранние сигналы: Андорра" }));
    expect(window.location.search).toContain("signal_country=AD");
    expect(mocks.earlySignals).toHaveBeenCalledWith("AD", expect.any(AbortSignal));
    const horizon = document.getElementById("early-signals");
    expect(horizon).toHaveAttribute("tabindex", "-1");
    await act(async () => { await new Promise<void>((resolve) => window.requestAnimationFrame(() => resolve())); });
    expect(horizon).toHaveFocus();
    expect(screen.getByRole("combobox", { name: "Выбранная страна" })).toHaveValue("RS");
    expect(mocks.decisionWorkspace).not.toHaveBeenCalledWith("AD", expect.anything());
  });
  it("starts with world signals and filters them after an explicit country choice", async () => {
    render(<DecisionWorkspace />);
    await screen.findByText("Сербия ↔ Россия");
    expect(mocks.earlySignals).toHaveBeenCalledWith(null, expect.any(AbortSignal));
    fireEvent.change(screen.getByRole("combobox", { name: "Выбранная страна" }), { target: { value: "AE" } });
    expect(mocks.earlySignals).toHaveBeenCalledWith("AE", expect.any(AbortSignal));
  });
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

  it("shows checked source excerpts with claim labels once and hides legacy quotes when the new array exists", async () => {
    const value = response();
    value.brief.day = [{ ...evidence, supporting_quotes: [
      { claim: "headline", quote: "visa rules", source_part: "title" },
      { claim: "summary", quote: "proposal", source_part: "excerpt" },
      { claim: "russia", quote: "Russian citizens", source_part: "excerpt" },
      { claim: "country", quote: "Serbia", source_part: "title" },
      { claim: "country", quote: "Russian citizens", source_part: "excerpt" },
      { claim: "summary", quote: "  ", source_part: "excerpt" },
    ] }];
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    await screen.findByText("Сербия ↔ Россия");
    const brief = screen.getByText("Страна за 60 секунд").closest("section")!;
    const citation = within(brief).getByText("Цитата и источник").closest("details")!;
    expect(citation).not.toHaveAttribute("open");
    fireEvent.click(within(citation).getByText("Цитата и источник"));
    const excerpts = [...citation.querySelectorAll("blockquote")];
    expect(excerpts.map((node) => node.textContent)).toEqual([
      "«visa rules»", "«proposal»", "«Russian citizens»", "«Serbia»",
    ]);
    for (const label of ["Суть сообщения", "Подробности", "Связь с Россией", "Участие страны"]) {
      expect(within(citation).getAllByText(label)[0]).toBeVisible();
    }
    expect(within(citation).queryByText(/Связь со страной:/)).not.toBeInTheDocument();
  });

  it("keeps every claim label when one exact source fragment supports three claims", async () => {
    const value = response();
    value.brief.day = [{ ...evidence, supporting_quotes: [
      { claim: "headline", quote: "Serbia", source_part: "title" },
      { claim: "russia", quote: "Serbia", source_part: "title" },
      { claim: "country", quote: "Serbia", source_part: "title" },
    ] }];
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    await screen.findByText("Сербия ↔ Россия");
    const brief = screen.getByText("Страна за 60 секунд").closest("section")!;
    const citation = within(brief).getByText("Цитата и источник").closest("details")!;
    fireEvent.click(within(citation).getByText("Цитата и источник"));
    expect([...citation.querySelectorAll("blockquote")].map((node) => node.textContent)).toEqual(["«Serbia»"]);
    for (const label of ["Суть сообщения", "Связь с Россией", "Участие страны"]) {
      expect(within(citation).getByText(label)).toBeVisible();
    }
  });

  it("puts a position's own quote first and keeps legacy source quotes when supporting excerpts are absent", async () => {
    render(<DecisionWorkspace />);
    await screen.findByText("Сербия ↔ Россия");
    fireEvent.click(screen.getByText(/Кто какую позицию занимает/));
    const position = screen.getByText("Сообщается, что ведомство изучает предложение.").closest("article")!;
    const citation = within(position).getByText("Цитата и источник").closest("details")!;
    fireEvent.click(within(citation).getByText("Цитата и источник"));
    expect(citation.querySelector("blockquote")).toHaveTextContent("«proposal»");
    expect(within(citation).getByText("Связь с Россией: «Russian citizens»")).toBeVisible();
    expect(within(citation).getByText("Связь со страной: «Serbia»")).toBeVisible();
  });

  it("keeps position and change quotes first when they repeat a supporting excerpt", async () => {
    const value = response();
    const withQuotes: DecisionEvidence = { ...evidence, supporting_quotes: [
      { claim: "summary", quote: "proposal", source_part: "excerpt" },
      { claim: "russia", quote: "Russian citizens", source_part: "excerpt" },
    ] };
    value.positions[0].evidence = withQuotes;
    value.changes[0].evidence = withQuotes;
    mocks.decisionWorkspace.mockResolvedValue(value);
    render(<DecisionWorkspace />);
    await screen.findByText("Сербия ↔ Россия");
    for (const heading of [/Кто какую позицию занимает/, /Что меняется для российских граждан/]) {
      fireEvent.click(screen.getByText(heading));
    }
    for (const [body, expected] of [
      ["Сообщается, что ведомство изучает предложение.", "«proposal»"],
      ["Предлагается изменить визовые правила для граждан РФ.", "«Russian citizens»"],
    ]) {
      const article = screen.getByText(body).closest("article")!;
      const citation = within(article).getByText("Цитата и источник").closest("details")!;
      fireEvent.click(within(citation).getByText("Цитата и источник"));
      const excerpts = [...citation.querySelectorAll("blockquote")];
      expect(excerpts[0]).toHaveTextContent(expected);
      expect(excerpts.filter((node) => node.textContent === expected)).toHaveLength(1);
      expect(within(citation).getByText(body.startsWith("Сообщается") ? "Подробности" : "Связь с Россией")).toBeVisible();
    }
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
    expect(within(document.getElementById("country-overview")!).getByRole("status")).toHaveTextContent("Загружаем обзор");
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
