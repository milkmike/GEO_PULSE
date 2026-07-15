"""Immutable, machine-readable definition of the production Thermometer v1."""

from __future__ import annotations

import math
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class TemperatureMethodology:
    version: str
    window_days: int
    time_decay_kind: str
    time_decay_tau_seconds: int
    source_weight_field: str
    source_weight_default: float
    source_cluster_order: str
    event_type_weights: Mapping[str | None, float]
    action_level_weights: Mapping[int, int]
    event_key_min_length: int
    cluster_diminishing_base: float
    unclustered_weight: float
    reprint_importance_base: float
    normalization_factor: float
    temperature_round_digits: int
    raw_sentiment_round_digits: int
    component_round_digits: int
    component_event_types: tuple[str, ...]
    trend_history_points: int
    trend_minimum_samples: int
    trend_threshold: float
    anomaly_history_points: int
    anomaly_minimum_samples: int
    anomaly_warning_threshold: float
    anomaly_critical_threshold: float
    anomaly_round_digits: int
    anomaly_zero_std_fallback: float


TEMPERATURE_METHODOLOGY = TemperatureMethodology(
    version="temperature-v1",
    window_days=14,
    time_decay_kind="exponential",
    time_decay_tau_seconds=14 * 86400,
    source_weight_field="sources.weight",
    source_weight_default=1.0,
    source_cluster_order="descending",
    event_type_weights=MappingProxyType({
        "military": 1.5,
        "diplomatic": 1.3,
        "security": 1.2,
        "economic": 1.0,
        "cultural": 0.8,
        None: 1.0,
    }),
    action_level_weights=MappingProxyType({
        1: 1,
        2: 3,
        3: 5,
        4: 8,
        5: 12,
        6: 15,
    }),
    event_key_min_length=4,
    cluster_diminishing_base=0.2,
    unclustered_weight=1.0,
    reprint_importance_base=1.0,
    normalization_factor=100 / 3,
    temperature_round_digits=1,
    raw_sentiment_round_digits=2,
    component_round_digits=2,
    component_event_types=(
        "diplomatic",
        "military",
        "economic",
        "cultural",
        "security",
    ),
    trend_history_points=3,
    trend_minimum_samples=2,
    trend_threshold=5.0,
    anomaly_history_points=30,
    anomaly_minimum_samples=5,
    anomaly_warning_threshold=2.0,
    anomaly_critical_threshold=3.0,
    anomaly_round_digits=2,
    anomaly_zero_std_fallback=1.0,
)


def _worked_example() -> dict:
    definition = TEMPERATURE_METHODOLOGY
    articles = [
        {
            "sentiment": 2.0,
            "source_weight": 1.0,
            "event_type": "diplomatic",
            "action_level": 2,
            "age_seconds": 0,
            "reprint_count": 0,
            "duplicate_index": 0,
        },
        {
            "sentiment": -1.0,
            "source_weight": 0.5,
            "event_type": "economic",
            "action_level": 1,
            "age_seconds": 86400,
            "reprint_count": 0,
            "duplicate_index": 0,
        },
    ]
    numerator = 0.0
    denominator = 0.0
    for article in articles:
        weight = (
            article["source_weight"]
            * definition.event_type_weights[article["event_type"]]
            * definition.action_level_weights[article["action_level"]]
            * math.exp(-article["age_seconds"] / definition.time_decay_tau_seconds)
            * (
                definition.reprint_importance_base
                + math.log1p(article["reprint_count"])
            )
            * definition.cluster_diminishing_base ** article["duplicate_index"]
        )
        numerator += article["sentiment"] * weight
        denominator += abs(weight)
    return {
        "articles": articles,
        "weighted_numerator": round(numerator, 6),
        "weighted_denominator": round(denominator, 6),
        "temperature": round(
            numerator / denominator * definition.normalization_factor,
            definition.temperature_round_digits,
        ),
    }


def temperature_methodology_payload() -> dict:
    """Return plain and technical layers derived from the live engine definition."""

    definition = TEMPERATURE_METHODOLOGY
    event_weights = {
        ("unspecified" if event_type is None else event_type): weight
        for event_type, weight in definition.event_type_weights.items()
    }
    return {
        "methodology_version": definition.version,
        "name": "Термометр отношений",
        "plain_language": [
            {
                "id": "inputs",
                "title": "Какие материалы учитываются",
                "body": (
                    "Учитываются релевантные России, не архивные статьи источников "
                    "выбранной страны за последние 14 дней с определённой тональностью."
                ),
            },
            {
                "id": "relevance",
                "title": "Как определяется релевантность",
                "body": (
                    "Предварительный анализ отмечает, относится ли статья к России, "
                    "и сохраняет для каждой статьи использованные модель и версию "
                    "промпта. Термометр принимает только отмеченные материалы."
                ),
            },
            {
                "id": "weights",
                "title": "Почему вклад новостей различается",
                "body": (
                    "Свежесть, вес источника, тип события, уровень действия и число "
                    "перепечаток меняют вес материала; повторы одного события затухают."
                ),
            },
            {
                "id": "interpretation",
                "title": "Как читать результат",
                "body": (
                    "Положительные значения отражают более позитивный медиатон, "
                    "отрицательные — более негативный. Это аналитическая оценка, а не факт."
                ),
            },
            {
                "id": "rri_difference",
                "title": "Чем Термометр отличается от RRI",
                "body": (
                    "Термометр измеряет медиатон собранных статей; RRI отдельно объединяет "
                    "структурные отношения, медиаслой и усиление недавними событиями."
                ),
            },
        ],
        "technical": {
            "input_eligibility": {
                "source_country_match": True,
                "analysis_is_relevant": True,
                "sentiment_required": True,
                "backfill_excluded": True,
                "published_within_window": True,
            },
            "window_days": definition.window_days,
            "time_decay": {
                "kind": definition.time_decay_kind,
                "tau_seconds": definition.time_decay_tau_seconds,
                "formula": "exp(-age_seconds / tau_seconds)",
            },
            "source_weights": {
                "field": definition.source_weight_field,
                "default": definition.source_weight_default,
                "cluster_order": definition.source_cluster_order,
            },
            "event_type_weights": event_weights,
            "action_level_weights": {
                str(level): weight
                for level, weight in definition.action_level_weights.items()
            },
            "reprint_importance": {
                "base": definition.reprint_importance_base,
                "formula": "1 + ln(1 + reprint_count)",
            },
            "event_clustering": {
                "key": "analysis.event_key",
                "minimum_raw_key_length": definition.event_key_min_length,
                "normalization_after_gate": "lowercase and trim",
            },
            "cluster_diminishing": {
                "base": definition.cluster_diminishing_base,
                "formula": "base ** duplicate_index",
                "unclustered_weight": definition.unclustered_weight,
            },
            "aggregation": {
                "numerator": "sum(sentiment * article_weight)",
                "denominator": "sum(abs(article_weight))",
                "raw_sentiment": "numerator / denominator",
            },
            "normalization": {
                "factor": definition.normalization_factor,
                "formula": "raw_sentiment * factor",
                "temperature_round_digits": definition.temperature_round_digits,
                "raw_sentiment_round_digits": definition.raw_sentiment_round_digits,
                "component_round_digits": definition.component_round_digits,
            },
            "trend": {
                "history_points": definition.trend_history_points,
                "minimum_samples": definition.trend_minimum_samples,
                "threshold": definition.trend_threshold,
                "rising_operator": ">",
                "falling_operator": "<",
                "method": "current minus mean of recent stored readings",
            },
            "anomaly": {
                "history_points": definition.anomaly_history_points,
                "minimum_samples": definition.anomaly_minimum_samples,
                "warning_threshold": definition.anomaly_warning_threshold,
                "critical_threshold": definition.anomaly_critical_threshold,
                "threshold_operator": ">",
                "round_digits": definition.anomaly_round_digits,
                "zero_std_fallback": definition.anomaly_zero_std_fallback,
                "method": "sample z-score against recent stored readings",
            },
            "upstream_analysis": {
                "model_field": "analysis.model_used",
                "prompt_version_field": "analysis.prompt_version",
                "provenance_is_per_article": True,
                "runtime_defined": True,
            },
        },
        "worked_example": _worked_example(),
        "limitations": [
            "coverage_is_limited_to_collected_and_successfully_analyzed_articles",
            "source_weight_and_source_availability_can_bias_the_result",
            "sentiment_and_event_classification_can_be_incorrect",
            "repeated_event_detection_depends_on_event_key_quality",
            "temperature_is_not_the_broader_rri_and_does_not_measure_causation",
        ],
    }
