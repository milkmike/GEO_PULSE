import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { TemperatureMethodology } from "@/lib/types";
import TemperatureMethodologyBlock from "./TemperatureMethodologyBlock";

const methodology: TemperatureMethodology = {
  methodology_version: "temperature-2026.07-test",
  name: "Температура медиатона",
  plain_language: [
    { id: "meaning", title: "Что показывает", body: "Температура суммирует тон подходящих публикаций." },
    { id: "weighting", title: "Что влияет", body: "Свежие и первичные сообщения получают иной вес." },
  ],
  technical: {
    input_eligibility: { has_sentiment: true, ignored_reposts: false },
    window_days: 19,
    time_decay: { kind: "exponential", tau_seconds: 9876, formula: "exp(-age/tau)" },
    source_weights: { field: "source_tier", default: 0.375, cluster_order: "after_source" },
    event_type_weights: { diplomacy: 1.75, conflict: 2.25 },
    action_level_weights: { "0": 0.125, "4": 3.5 },
    reprint_importance: { base: 7.25, formula: "base / sqrt(reprints + 1)" },
    event_clustering: { key: "event_key", minimum_raw_key_length: 13, normalization_after_gate: "trim_lower" },
    cluster_diminishing: { base: 0.625, formula: "base ^ duplicate_index", unclustered_weight: 0.875 },
    aggregation: { numerator: "sum(sentiment * weight)", denominator: "sum(weight)", raw_sentiment: "numerator / denominator" },
    normalization: { factor: 8.125, formula: "raw * factor", temperature_round_digits: 5, raw_sentiment_round_digits: 6, component_round_digits: 7 },
    trend: { window_days: 23, minimum_points: 9, formula: "latest - earliest" },
    anomaly: { z_threshold: 4.75, minimum_points: 12, formula: "abs(z) >= threshold" },
    upstream_analysis: { provider: "analysis_service", requires_translation: true },
  },
  worked_example: {
    articles: [{
      sentiment: -1.125,
      source_weight: 0.375,
      event_type: "diplomacy",
      action_level: 4,
      age_seconds: 4567,
      reprint_count: 3,
      duplicate_index: 2,
    }],
    weighted_numerator: -6.54321,
    weighted_denominator: 7.65432,
    temperature: -8.76543,
  },
  limitations: ["Покрытие источников неравномерно.", "Тон публикаций не равен мнению населения."],
};

describe("TemperatureMethodologyBlock", () => {
  it("leads with every backend plain-language paragraph and version", () => {
    render(<TemperatureMethodologyBlock methodology={methodology} />);

    expect(screen.getByRole("heading", { name: methodology.name })).toBeVisible();
    expect(screen.getByText(/temperature-2026.07-test/i)).toBeVisible();
    for (const item of methodology.plain_language) {
      expect(screen.getByRole("heading", { name: item.title })).toBeVisible();
      expect(screen.getByText(item.body)).toBeVisible();
    }
  });

  it("reveals backend-driven technical values and the complete worked example", async () => {
    const user = userEvent.setup();
    render(<TemperatureMethodologyBlock methodology={methodology} />);

    await user.click(screen.getByText(/технические критерии/i));
    const technical = screen.getByRole("region", { name: /технические критерии/i });
    for (const value of ["19", "9876", "0,375", "1,75", "2,25", "13", "8,125", "4,75", "12"]) {
      expect(within(technical).getAllByText(value).length).toBeGreaterThan(0);
    }
    expect(technical).toHaveTextContent("exp(-age/tau)");
    expect(technical).toHaveTextContent("analysis_service");

    await user.click(screen.getByText(/пример расчёта/i));
    const example = screen.getByRole("region", { name: /пример расчёта/i });
    expect(example).toHaveTextContent("−1,125");
    expect(example).toHaveTextContent("4567");
    expect(example).toHaveTextContent("−6,54321");
    expect(example).toHaveTextContent("−8,76543");
  });

  it("renders every backend limitation without inventing replacements", () => {
    render(<TemperatureMethodologyBlock methodology={methodology} />);
    const limitations = screen.getByRole("region", { name: /ограничения температуры/i });
    expect(within(limitations).getAllByRole("listitem")).toHaveLength(methodology.limitations.length);
    for (const limitation of methodology.limitations) {
      expect(within(limitations).getByText(limitation)).toBeVisible();
    }
  });

  it("translates live contract keys and known limitation codes while preserving unknown codes", async () => {
    const user = userEvent.setup();
    const liveCodes = {
      ...methodology,
      technical: {
        ...methodology.technical,
        input_eligibility: {
          source_country_match: true,
          analysis_is_relevant: true,
          sentiment_required: true,
          backfill_excluded: true,
          published_within_window: true,
        },
        trend: {
          history_points: 3, minimum_samples: 2, threshold: 5,
          rising_operator: ">", falling_operator: "<", method: "current minus mean",
        },
        anomaly: {
          history_points: 30, minimum_samples: 5, warning_threshold: 2,
          critical_threshold: 3, threshold_operator: ">", round_digits: 2,
          zero_std_fallback: 0, method: "sample z-score",
        },
        upstream_analysis: {
          model_field: "analysis.model_used",
          prompt_version_field: "analysis.prompt_version",
          provenance_is_per_article: true,
          runtime_defined: true,
        },
      },
      limitations: [
        "coverage_is_limited_to_collected_and_successfully_analyzed_articles",
        "source_weight_and_source_availability_can_bias_the_result",
        "sentiment_and_event_classification_can_be_incorrect",
        "repeated_event_detection_depends_on_event_key_quality",
        "temperature_is_not_the_broader_rri_and_does_not_measure_causation",
        "unknown_future_limitation",
      ],
    } satisfies TemperatureMethodology;
    render(<TemperatureMethodologyBlock methodology={liveCodes} />);

    await user.click(screen.getByText(/технические критерии/i));
    const technical = screen.getByRole("region", { name: /технические критерии/i });
    for (const label of [
      "Число недавних измерений", "Минимум измерений", "Порог предупреждения",
      "Критический порог", "Поле модели", "Поле версии промпта",
      "Значение определяется при выполнении", "Анализ подтвердил релевантность России",
    ]) expect(within(technical).getAllByText(label).length).toBeGreaterThan(0);

    const limitations = screen.getByRole("region", { name: /ограничения температуры/i });
    expect(limitations).toHaveTextContent("Учитываются только собранные и успешно проанализированные публикации.");
    expect(limitations).toHaveTextContent("Доступность источников и их веса могут смещать результат.");
    expect(limitations).toHaveTextContent("Классификация тональности и типа события может ошибаться.");
    expect(limitations).toHaveTextContent("Распознавание повторов зависит от качества ключа события.");
    expect(limitations).toHaveTextContent("Температура — не общий индекс RRI и не доказательство причинности.");
    expect(limitations).toHaveTextContent("unknown_future_limitation");
  });
});
