from __future__ import annotations

import math
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import methodology
from src.engine import index


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def test_engine_and_api_share_one_immutable_methodology_definition():
    definition = methodology.TEMPERATURE_METHODOLOGY

    assert index.TEMPERATURE_METHODOLOGY is definition
    assert index.EVENT_TYPE_WEIGHTS is definition.event_type_weights
    assert index.ACTION_MULTIPLIERS is definition.action_level_weights
    assert definition.window_days == 14
    assert definition.time_decay_tau_seconds == 14 * 86400
    with pytest.raises(TypeError):
        definition.event_type_weights["military"] = 99
    with pytest.raises(FrozenInstanceError):
        definition.window_days = 7


def test_methodology_payload_contains_every_current_coefficient_category():
    payload = methodology.temperature_methodology_payload()
    technical = payload["technical"]

    assert payload["methodology_version"] == "temperature-v1"
    assert payload["plain_language"]
    assert any(
        section["id"] == "relevance"
        for section in payload["plain_language"]
    )
    assert technical["window_days"] == 14
    assert technical["time_decay"]["kind"] == "exponential"
    assert technical["source_weights"] == {
        "field": "sources.weight",
        "default": 1.0,
        "cluster_order": "descending",
    }
    assert technical["event_type_weights"] == {
        "military": 1.5,
        "diplomatic": 1.3,
        "security": 1.2,
        "economic": 1.0,
        "cultural": 0.8,
        "unspecified": 1.0,
    }
    assert technical["action_level_weights"] == {
        "1": 1,
        "2": 3,
        "3": 5,
        "4": 8,
        "5": 12,
        "6": 15,
    }
    assert technical["cluster_diminishing"]["base"] == 0.2
    assert technical["normalization"]["factor"] == 100 / 3
    assert technical["anomaly"]["minimum_samples"] == 5
    assert technical["trend"]["history_points"] == 3
    assert technical["upstream_analysis"]["provenance_is_per_article"] is True
    assert payload["limitations"]


def test_worked_example_is_reproducible_from_returned_constants():
    payload = methodology.temperature_methodology_payload()
    technical = payload["technical"]
    example = payload["worked_example"]
    numerator = 0.0
    denominator = 0.0
    for item in example["articles"]:
        decay = math.exp(
            -item["age_seconds"] / technical["time_decay"]["tau_seconds"]
        )
        event_weight = technical["event_type_weights"][item["event_type"]]
        action_weight = technical["action_level_weights"][str(item["action_level"])]
        importance = 1 + math.log1p(item["reprint_count"])
        cluster_decay = technical["cluster_diminishing"]["base"] ** item["duplicate_index"]
        weight = (
            item["source_weight"]
            * event_weight
            * action_weight
            * decay
            * importance
            * cluster_decay
        )
        numerator += item["sentiment"] * weight
        denominator += abs(weight)
    temperature = round(
        numerator / denominator * technical["normalization"]["factor"],
        technical["normalization"]["temperature_round_digits"],
    )

    assert temperature == example["temperature"]


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class TemperatureSession:
    def __init__(self, article_rows):
        self.article_rows = article_rows
        self.added = []

    def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM analysis a" in sql:
            return FakeResult(self.article_rows)
        if "ORDER BY time DESC LIMIT 3" in sql:
            return FakeResult([
                SimpleNamespace(temperature=50),
                SimpleNamespace(temperature=55),
            ])
        if "ORDER BY time DESC LIMIT 30" in sql:
            return FakeResult([])
        raise AssertionError(sql)

    def add(self, item):
        self.added.append(item)


class SessionContext:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc, traceback):
        return False


class FixedDateTime:
    @classmethod
    def now(cls, tz=None):
        return NOW


class CanonicalPublisherTemperatureSession(TemperatureSession):
    def __init__(self):
        super().__init__([])
        self.selected_article_ids = {}
        self.selected_publisher_names = {}
        self.selected_source_ids = {}
        self.triplet = (
            SimpleNamespace(
                article_id=101,
                publisher_name="EL PAÍS",
                publisher_country_code="ES",
                publisher_source_id=11,
                publisher_weight=2.0,
                sentiment=3.0,
                event_type="diplomatic",
            ),
            SimpleNamespace(
                article_id=102,
                publisher_name="Reuters",
                publisher_country_code="GB",
                publisher_source_id=22,
                publisher_weight=1.5,
                sentiment=-3.0,
                event_type="economic",
            ),
            SimpleNamespace(
                article_id=103,
                publisher_name=None,
                publisher_country_code=None,
                publisher_source_id=None,
                publisher_weight=None,
                sentiment=1.0,
                event_type="cultural",
            ),
        )

    @staticmethod
    def _temperature_row(item, *, source_id, weight):
        return SimpleNamespace(
            article_id=item.article_id,
            sentiment=item.sentiment,
            event_type=item.event_type,
            sentiment_confidence=1.0,
            action_level=1,
            event_key=None,
            published_at=NOW,
            weight=weight,
            source_id=source_id,
            reprint_count=0,
        )

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "FROM analysis a" not in sql:
            return super().execute(statement, params)

        country_code = params["cc"]
        if "JOIN article_country_facts s ON s.article_id = ar.id" in sql:
            matched = [
                item
                for item in self.triplet
                if item.publisher_country_code == country_code
                and item.publisher_source_id is not None
            ]
            rows = [
                self._temperature_row(
                    item,
                    source_id=item.publisher_source_id,
                    weight=item.publisher_weight,
                )
                for item in matched
            ]
        else:
            matched = list(self.triplet) if country_code == "ES" else []
            rows = [
                self._temperature_row(item, source_id=900, weight=0.5)
                for item in matched
            ]

        self.selected_article_ids[country_code] = [row.article_id for row in rows]
        self.selected_publisher_names[country_code] = [
            item.publisher_name for item in matched
        ]
        self.selected_source_ids[country_code] = [row.source_id for row in rows]
        return FakeResult(rows)


def test_methodology_extraction_preserves_existing_temperature_output(monkeypatch):
    rows = [
        SimpleNamespace(
            sentiment=2.0,
            event_type="diplomatic",
            sentiment_confidence=1.0,
            action_level=2,
            event_key="summit",
            published_at=NOW,
            weight=2.0,
            source_id=1,
            reprint_count=0,
        ),
        SimpleNamespace(
            sentiment=-1.0,
            event_type="economic",
            sentiment_confidence=1.0,
            action_level=1,
            event_key="summit",
            published_at=NOW,
            weight=1.0,
            source_id=2,
            reprint_count=0,
        ),
        SimpleNamespace(
            sentiment=1.0,
            event_type="cultural",
            sentiment_confidence=1.0,
            action_level=1,
            event_key=None,
            published_at=NOW,
            weight=0.5,
            source_id=3,
            reprint_count=0,
        ),
    ]
    session = TemperatureSession(rows)
    monkeypatch.setattr(index, "get_session", lambda: SessionContext(session))
    monkeypatch.setattr(index, "datetime", FixedDateTime)

    result = index.calculate_temperature("KZ")

    assert result == {
        "time": NOW,
        "country_code": "KZ",
        "temperature": 62.7,
        "raw_sentiment": 1.88,
        "diplomatic": 2.0,
        "military": None,
        "economic": -1.0,
        "cultural": 1.0,
        "security": None,
        "article_count": 3,
        "source_count": 3,
        "trend": "rising",
        "anomaly_score": None,
    }


def test_temperature_uses_verified_publisher_country_weight_and_source_id(monkeypatch):
    session = CanonicalPublisherTemperatureSession()
    monkeypatch.setattr(index, "get_session", lambda: SessionContext(session))
    monkeypatch.setattr(index, "datetime", FixedDateTime)

    spain = index.calculate_temperature("ES")
    britain = index.calculate_temperature("GB")

    assert spain["temperature"] == 100.0
    assert spain["article_count"] == 1
    assert spain["source_count"] == 1
    assert britain["temperature"] == -100.0
    assert britain["article_count"] == 1
    assert britain["source_count"] == 1
    assert session.selected_article_ids == {"ES": [101], "GB": [102]}
    assert session.selected_publisher_names == {
        "ES": ["EL PAÍS"],
        "GB": ["Reuters"],
    }
    assert session.selected_source_ids == {"ES": [11], "GB": [22]}
    assert 103 not in session.selected_article_ids["ES"]
    assert 900 not in session.selected_source_ids["ES"]


@pytest.mark.parametrize(
    ("relative_path", "expected_joins"),
    (
        ("src/engine/index.py", 1),
        ("src/engine/ru_index.py", 1),
        ("src/engine/signals.py", 4),
        ("src/engine/explanations.py", 2),
        ("scripts/backfill_temperature.py", 1),
        ("scripts/retro_temperature.py", 1),
    ),
)
def test_article_derived_calculations_join_canonical_publisher_facts(
    relative_path,
    expected_joins,
):
    source = (ROOT / relative_path).read_text()

    assert source.count(
        "JOIN article_country_facts s ON s.article_id = ar.id"
    ) == expected_joins
    assert "JOIN sources s ON ar.source_id = s.id" not in source
    assert "JOIN sources s ON s.id = ar.source_id" not in source
