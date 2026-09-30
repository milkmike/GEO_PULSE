import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DecisionEvidence, DecisionWorkspaceResponse } from "@/lib/decisionTypes";
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

describe("DecisionWorkspace", () => {
  it("renders all six analyst sections with source dates, uncertainty and citation", async () => {
    render(<DecisionWorkspace />);
    expect(await screen.findByText("Сербия ↔ Россия")).toBeVisible();
    for (const heading of ["Где требуется внимание сегодня", "Страна за 60 секунд", "Кто какую позицию занимает", "Что меняется для российских граждан и организаций", "Темы для разговора", "Насколько полна картина"]) {
      expect(screen.getByText(heading)).toBeVisible();
    }
    expect(screen.getByText(/машинным анализом/)).toBeVisible();
    expect(screen.getByText(/Независимость подтверждений не оценивалась/)).toBeVisible();
    expect(screen.getByText(/Охвачены не все издатели/)).toBeVisible();
    const citation = screen.getAllByText("Цитата и источник")[0].closest("details");
    expect(citation).not.toBeNull();
    expect(within(citation!).getByText(/Опубликовано:/)).toHaveTextContent("собрано:");
    expect(within(citation!).getByRole("link", { name: /Открыть публикацию/ })).toHaveAttribute("href", "https://example.org/article");
    expect(window.location.search).toBe("?country=RS");
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
    expect(screen.getByText(/Модель не выделила явно атрибутированных позиций/)).toBeVisible();
    expect(screen.getByText(/Выборка ограничена/)).toBeVisible();
    expect(screen.queryByRole("link", { name: /Открыть публикацию/ })).not.toBeInTheDocument();
    expect(screen.getByText("Ссылка на оригинал недоступна.")).toBeInTheDocument();
  });
});
