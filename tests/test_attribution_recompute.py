from __future__ import annotations

import importlib
import json
import math
from collections import deque as real_deque
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.engine import index


NOW = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
TEMPERATURE_FIELDS = (
    "temperature",
    "raw_sentiment",
    "diplomatic",
    "military",
    "economic",
    "cultural",
    "security",
    "article_count",
    "source_count",
    "trend",
    "anomaly_score",
)


class FakeResult:
    def __init__(self, *, rows=(), row=None):
        self._rows = list(rows)
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class SessionContext:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc, traceback):
        return False


class AsOfTemperatureSession:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        self.calls.append((sql, params))
        if "FROM analysis a" in sql:
            return FakeResult(rows=[SimpleNamespace(
                analysis_id=101,
                article_id=201,
                sentiment=2.0,
                event_type="diplomatic",
                sentiment_confidence=1.0,
                action_level=2,
                event_key="green corridor agreement",
                published_at=NOW - timedelta(days=1),
                weight=1.5,
                source_id=11,
                reprint_count=0,
            )])
        if "ORDER BY time DESC LIMIT 30" in sql:
            return FakeResult(rows=[
                SimpleNamespace(temperature=20.0),
                SimpleNamespace(temperature=25.0),
            ])
        if "ORDER BY time DESC LIMIT 3" in sql:
            return FakeResult(rows=[])
        raise AssertionError(sql)

    def add(self, item):
        raise AssertionError("as-of calculation must not emit persisted alerts")


class ParityTemperatureSession:
    def __init__(self, article_rows):
        self.article_rows = list(article_rows)
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if "FROM analysis a" in sql:
            return FakeResult(rows=self.article_rows)
        if "ORDER BY time DESC LIMIT 30" in sql:
            return FakeResult(rows=[
                SimpleNamespace(temperature=value)
                for value in (10.0, 15.0, 20.0, 25.0, 30.0)
            ])
        if "ORDER BY time DESC LIMIT 3" in sql:
            return FakeResult(rows=[
                SimpleNamespace(temperature=value) for value in (10.0, 15.0, 20.0)
            ])
        raise AssertionError(sql)

    def add(self, item):
        raise AssertionError("parity calculation must not emit persisted alerts")


class FixedDateTime:
    @classmethod
    def now(cls, tz=None):
        return NOW


def _load_task7_module(name: str):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        pytest.fail(f"Task 7 module {name} does not exist")


def _temperature_row(at: datetime, country: str, temperature: float):
    return {
        "time": at,
        "country_code": country,
        "temperature": temperature,
        "raw_sentiment": round(temperature / (100 / 3), 2),
        "diplomatic": 0.1,
        "military": None,
        "economic": -0.1,
        "cultural": None,
        "security": None,
        "article_count": 3,
        "source_count": 2,
        "trend": "stable",
        "anomaly_score": None,
        "pattern_type": "legacy-pattern",
    }


class RecomputeSession:
    def __init__(self):
        rows = (
            _temperature_row(NOW - timedelta(days=91), "ES", 1.0),
            _temperature_row(NOW - timedelta(days=90), "ES", 2.0),
            _temperature_row(NOW - timedelta(days=1), "GB", 3.0),
        )
        self.values = {
            (row["time"], row["country_code"]): dict(row)
            for row in rows
        }
        self.calls = []
        self.upserted_keys = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        if "attribution_recompute:existing_keys" in sql:
            rows = [
                SimpleNamespace(**row)
                for row in self.values.values()
                if params["window_start"] <= row["time"] <= params["window_end"]
            ]
            rows.sort(key=lambda row: (row.time, row.country_code))
            return FakeResult(rows=rows)
        if "attribution_recompute:history_seeds" in sql:
            rows = [
                SimpleNamespace(**row)
                for row in self.values.values()
                if row["country_code"] in params["country_codes"]
                and row["time"] < params["window_start"]
            ]
            rows.sort(key=lambda row: (row.time, row.country_code))
            return FakeResult(rows=rows)
        if "attribution_recompute:article_inputs" in sql:
            rows = []
            for (at, country_code), _ in self.values.items():
                if (
                    country_code == params["cc"]
                    and params["input_start"] < at <= params["window_end"]
                ):
                    rows.append(SimpleNamespace(
                        sentiment=0.3,
                        event_type="diplomatic",
                        sentiment_confidence=1.0,
                        action_level=2,
                        event_key=f"recent-event-{at.isoformat()}",
                        published_at=at,
                        weight=1.0,
                        source_id=11,
                        reprint_count=0,
                    ))
            rows.sort(key=lambda row: row.published_at)
            return FakeResult(rows=rows)
        if "INSERT INTO temperature" in sql:
            batch = params if isinstance(params, list) else [params]
            for item in batch:
                key = (item["time"], item["country_code"])
                if key not in self.values:
                    raise AssertionError("recompute must not create temperature keys")
                preserved_pattern = self.values[key]["pattern_type"]
                self.values[key].update(item)
                self.values[key]["pattern_type"] = preserved_pattern
                self.upserted_keys.append(key)
            return FakeResult()
        raise AssertionError(sql)

    def snapshot(self):
        return {
            key: dict(value)
            for key, value in self.values.items()
        }


def _calculator_for(session: RecomputeSession, calls: list[tuple[str, datetime]]):
    def calculate(country_code, as_of, rows, *, history=()):
        assert rows
        calls.append((country_code, as_of))
        old = session.values[(as_of, country_code)]
        result = {
            key: value
            for key, value in old.items()
            if key != "pattern_type"
        }
        result["temperature"] = old["temperature"] + 10.0
        result["raw_sentiment"] = round(result["temperature"] / (100 / 3), 2)
        result["trend"] = "rising"
        return result

    return calculate


def test_calculate_temperature_at_uses_exact_v1_with_bounded_as_of_sql(monkeypatch):
    calculator = getattr(index, "calculate_temperature_at", None)
    assert callable(calculator), "calculate_temperature_at is missing"
    session = AsOfTemperatureSession()
    monkeypatch.setattr(index, "get_session", lambda: SessionContext(session))

    result = calculator("ES", NOW)

    assert result == {
        "time": NOW,
        "country_code": "ES",
        "temperature": 66.7,
        "raw_sentiment": 2.0,
        "diplomatic": 2.0,
        "military": None,
        "economic": None,
        "cultural": None,
        "security": None,
        "article_count": 1,
        "source_count": 1,
        "trend": "rising",
        "anomaly_score": None,
    }
    article_sql, article_params = next(
        call for call in session.calls if "FROM analysis a" in call[0]
    )
    assert "ar.published_at > :window_start" in article_sql
    assert "ar.published_at <= :as_of" in article_sql
    assert "ar.is_backfill = false" in article_sql.lower()
    assert article_params == {
        "cc": "ES",
        "window_start": NOW - timedelta(days=14),
        "as_of": NOW,
    }
    history_calls = [call for call in session.calls if "FROM temperature" in call[0]]
    assert len(history_calls) == 1
    assert "ORDER BY time DESC LIMIT 30" in history_calls[0][0]
    assert all("time < :as_of" in sql for sql, _ in history_calls)
    assert all(params["as_of"] == NOW for _, params in history_calls)


def test_calculate_temperature_from_rows_has_exact_wrapper_parity(monkeypatch):
    calculator = getattr(index, "calculate_temperature_from_rows", None)
    assert callable(calculator), "calculate_temperature_from_rows is missing"
    article_rows = [
        SimpleNamespace(
            analysis_id=101,
            article_id=201,
            sentiment=2.0,
            event_type="diplomatic",
            sentiment_confidence=1.0,
            action_level=2,
            event_key="green corridor agreement",
            published_at=NOW - timedelta(days=1),
            weight=1.5,
            source_id=11,
            reprint_count=0,
        ),
        SimpleNamespace(
            analysis_id=102,
            article_id=202,
            sentiment=-1.0,
            event_type="diplomatic",
            sentiment_confidence=0.8,
            action_level=3,
            event_key="green corridor agreement",
            published_at=NOW - timedelta(days=2),
            weight=0.7,
            source_id=12,
            reprint_count=2,
        ),
        SimpleNamespace(
            analysis_id=103,
            article_id=203,
            sentiment=0.5,
            event_type="economic",
            sentiment_confidence=0.9,
            action_level=1,
            event_key=None,
            published_at=NOW - timedelta(hours=6),
            weight=1.1,
            source_id=13,
            reprint_count=0,
        ),
    ]
    wrapper_session = ParityTemperatureSession(article_rows)
    monkeypatch.setattr(index, "get_session", lambda: SessionContext(wrapper_session))

    with index.suppress_temperature_alerts():
        wrapped = index.calculate_temperature_at("ES", NOW)
    pure = calculator(
        "ES",
        NOW,
        article_rows,
        history=(10.0, 15.0, 20.0, 25.0, 30.0),
    )

    assert pure == wrapped


def test_extreme_anomaly_score_keeps_exact_pure_wrapper_parity(monkeypatch):
    article_rows = [SimpleNamespace(
        analysis_id=101,
        article_id=201,
        sentiment=3.0,
        event_type="diplomatic",
        sentiment_confidence=1.0,
        action_level=1,
        event_key=None,
        published_at=NOW - timedelta(hours=1),
        weight=1.0,
        source_id=11,
        reprint_count=0,
    )]
    history = [-100.0] * 29 + [-99.99]

    class ExtremeAnomalySession(ParityTemperatureSession):
        def execute(self, statement, params=None):
            sql = str(statement)
            self.calls.append((sql, params or {}))
            if "FROM analysis a" in sql:
                return FakeResult(rows=self.article_rows)
            if "ORDER BY time DESC LIMIT 30" in sql:
                return FakeResult(rows=[
                    SimpleNamespace(temperature=value) for value in history
                ])
            raise AssertionError(sql)

    session = ExtremeAnomalySession(article_rows)
    monkeypatch.setattr(index, "get_session", lambda: SessionContext(session))

    with index.suppress_temperature_alerts():
        wrapped = index.calculate_temperature_at("ES", NOW)
    pure = index.calculate_temperature_from_rows(
        "ES",
        NOW,
        article_rows,
        history=history,
    )

    assert pure == wrapped
    assert pure is not None
    assert pure["temperature"] == 100.0
    assert pure["anomaly_score"] == 109544.33


def test_calculate_temperature_from_rows_default_is_database_free():
    row = SimpleNamespace(
        analysis_id=101,
        article_id=201,
        sentiment=0.5,
        event_type="economic",
        sentiment_confidence=1.0,
        action_level=1,
        event_key=None,
        published_at=NOW - timedelta(hours=1),
        weight=1.0,
        source_id=11,
        reprint_count=0,
    )

    result = index.calculate_temperature_from_rows("ES", NOW, [row])

    assert result is not None
    assert result["trend"] == "stable"
    assert result["anomaly_score"] is None


def test_equal_weight_cluster_order_is_deterministic(monkeypatch):
    earlier = SimpleNamespace(
        analysis_id=101,
        article_id=201,
        sentiment=2.0,
        event_type="diplomatic",
        sentiment_confidence=1.0,
        action_level=1,
        event_key="equal weight event",
        published_at=NOW - timedelta(days=2),
        weight=1.0,
        source_id=11,
        reprint_count=0,
    )
    later = SimpleNamespace(
        analysis_id=102,
        article_id=202,
        sentiment=-2.0,
        event_type="diplomatic",
        sentiment_confidence=1.0,
        action_level=1,
        event_key="equal weight event",
        published_at=NOW - timedelta(days=1),
        weight=1.0,
        source_id=12,
        reprint_count=0,
    )

    forward = index.calculate_temperature_from_rows(
        "ES", NOW, [earlier, later], history=(),
    )
    reverse = index.calculate_temperature_from_rows(
        "ES", NOW, [later, earlier], history=(),
    )

    assert forward == reverse

    session = ParityTemperatureSession([earlier, later])
    monkeypatch.setattr(index, "get_session", lambda: SessionContext(session))
    with index.suppress_temperature_alerts():
        index.calculate_temperature_at("ES", NOW)
    article_sql = next(sql for sql, _ in session.calls if "FROM analysis a" in sql)
    assert "a.id AS analysis_id" in article_sql
    assert "ar.id AS article_id" in article_sql
    assert "ORDER BY ar.published_at, ar.id, a.id" in article_sql


def test_preloaded_article_window_is_lower_exclusive_and_upper_inclusive():
    recompute = _load_task7_module("scripts.recompute_attribution_window")
    selector = getattr(recompute, "_article_rows_for_as_of", None)
    assert callable(selector), "_article_rows_for_as_of is missing"
    lower = NOW - timedelta(days=14)
    rows = [
        SimpleNamespace(article_id=1, published_at=lower - timedelta(microseconds=1)),
        SimpleNamespace(article_id=2, published_at=lower),
        SimpleNamespace(article_id=3, published_at=lower + timedelta(microseconds=1)),
        SimpleNamespace(article_id=4, published_at=NOW),
        SimpleNamespace(article_id=5, published_at=NOW + timedelta(microseconds=1)),
    ]

    selected = selector(rows, NOW)

    assert [row.article_id for row in selected] == [3, 4]


def test_current_temperature_delegates_to_the_as_of_implementation(monkeypatch):
    observed = []
    sentinel = {"time": NOW, "country_code": "ES", "temperature": 1.0}

    def fake_calculate(country_code, as_of, *, exclude_backfill=True):
        observed.append((country_code, as_of, exclude_backfill))
        return sentinel

    monkeypatch.setattr(index, "calculate_temperature_at", fake_calculate, raising=False)
    monkeypatch.setattr(index, "datetime", FixedDateTime)
    monkeypatch.setattr(
        index,
        "get_session",
        lambda: pytest.fail("calculate_temperature must delegate"),
    )

    assert index.calculate_temperature("ES") is sentinel
    assert observed == [("ES", NOW, True)]


def test_recompute_window_dry_run_reads_104_days_and_writes_nothing(monkeypatch):
    recompute = _load_task7_module("scripts.recompute_attribution_window")
    session = RecomputeSession()
    before = session.snapshot()
    calculator_calls = []
    post_apply_calls = []
    monkeypatch.setattr(recompute, "get_session", lambda: SessionContext(session))
    monkeypatch.setattr(recompute, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        recompute,
        "calculate_temperature_from_rows",
        _calculator_for(session, calculator_calls),
        raising=False,
    )
    monkeypatch.setattr(
        recompute,
        "calculate_temperature_at",
        lambda *args, **kwargs: pytest.fail("recompute used per-key DB calculator"),
        raising=False,
    )
    monkeypatch.setattr(
        recompute,
        "_run_post_apply_jobs",
        lambda: post_apply_calls.append(True),
    )

    report = recompute.recompute_window(days=90, apply=False, batch_size=1)

    assert report.input_days == 104
    assert report.window_start == NOW - timedelta(days=90)
    assert report.input_start == NOW - timedelta(days=104)
    assert report.window_end == NOW
    assert report.keys_read == 2
    assert report.recalculated == 2
    assert report.changed == 2
    assert report.upserted == 0
    assert session.snapshot() == before
    assert set(session.values) == set(before)
    assert calculator_calls == [
        ("ES", NOW - timedelta(days=90)),
        ("GB", NOW - timedelta(days=1)),
    ]
    assert post_apply_calls == []
    assert not any("INSERT INTO" in sql or "DELETE FROM" in sql for sql, _ in session.calls)
    assert len(report.deltas) == 2
    assert report.delta_total == 2
    assert report.deltas_omitted == 0
    assert report.deltas[0].before["temperature"] == 2.0
    assert report.deltas[0].after["temperature"] == 12.0
    json.dumps(asdict(report), default=str)


def test_recompute_window_apply_upserts_only_existing_90_day_keys(monkeypatch):
    recompute = _load_task7_module("scripts.recompute_attribution_window")
    session = RecomputeSession()
    before = session.snapshot()
    calculator_calls = []
    post_apply_calls = []
    monkeypatch.setattr(recompute, "get_session", lambda: SessionContext(session))
    monkeypatch.setattr(recompute, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        recompute,
        "calculate_temperature_from_rows",
        _calculator_for(session, calculator_calls),
        raising=False,
    )
    monkeypatch.setattr(
        recompute,
        "calculate_temperature_at",
        lambda *args, **kwargs: pytest.fail("recompute used per-key DB calculator"),
        raising=False,
    )
    monkeypatch.setattr(
        recompute,
        "_run_post_apply_jobs",
        lambda: post_apply_calls.append(True),
    )

    report = recompute.recompute_window(days=90, apply=True, batch_size=1)

    old_key = (NOW - timedelta(days=91), "ES")
    in_window_keys = {
        (NOW - timedelta(days=90), "ES"),
        (NOW - timedelta(days=1), "GB"),
    }
    assert set(session.values) == set(before)
    assert session.values[old_key] == before[old_key]
    assert set(session.upserted_keys) == in_window_keys
    assert all(session.values[key]["temperature"] == before[key]["temperature"] + 10 for key in in_window_keys)
    assert all(session.values[key]["pattern_type"] == "legacy-pattern" for key in in_window_keys)
    assert report.upserted == 2
    assert post_apply_calls == [True]
    assert not any("DELETE FROM" in sql for sql, _ in session.calls)


class RollingRecomputeSession(RecomputeSession):
    def __init__(self):
        seed_times = [NOW - timedelta(days=95 - offset) for offset in range(5)]
        rows = [
            _temperature_row(at, "ES", 10.0)
            for at in seed_times
        ] + [
            _temperature_row(NOW - timedelta(days=2), "ES", 10.0),
            _temperature_row(NOW - timedelta(days=1), "ES", 10.0),
        ]
        self.values = {
            (row["time"], row["country_code"]): dict(row)
            for row in rows
        }
        self.calls = []
        self.upserted_keys = []


def _rolling_calculator(session: RollingRecomputeSession):
    def calculate(country_code, as_of, rows, *, history=()):
        assert rows
        current = 30.0 if as_of == NOW - timedelta(days=2) else 20.0
        old = session.values[(as_of, country_code)]
        result = {
            key: value
            for key, value in old.items()
            if key != "pattern_type"
        }
        result["temperature"] = current
        result["raw_sentiment"] = round(current / (100 / 3), 2)
        result["trend"] = index._trend_from_history(
            current,
            history[:index.TEMPERATURE_METHODOLOGY.trend_history_points],
        )
        anomaly_statistics = index._anomaly_statistics(
            current,
            history[:index.TEMPERATURE_METHODOLOGY.anomaly_history_points],
        )
        result["anomaly_score"] = (
            anomaly_statistics[0] if anomaly_statistics is not None else None
        )
        return result

    return calculate


def test_recompute_rolls_repaired_history_forward_and_dry_run_matches_apply(
    monkeypatch,
):
    recompute = _load_task7_module("scripts.recompute_attribution_window")

    def run(apply):
        session = RollingRecomputeSession()
        before = session.snapshot()
        monkeypatch.setattr(recompute, "get_session", lambda: SessionContext(session))
        monkeypatch.setattr(recompute, "_utc_now", lambda: NOW)
        monkeypatch.setattr(
            recompute,
            "calculate_temperature_from_rows",
            _rolling_calculator(session),
            raising=False,
        )
        monkeypatch.setattr(
            recompute,
            "calculate_temperature_at",
            lambda *args, **kwargs: pytest.fail("recompute used per-key DB calculator"),
            raising=False,
        )
        monkeypatch.setattr(recompute, "_run_post_apply_jobs", lambda: None)
        report = recompute.recompute_window(days=90, apply=apply, batch_size=1)
        return session, before, report

    dry_session, dry_before, dry_report = run(False)
    apply_session, _, apply_report = run(True)

    dry_series = [delta.after for delta in dry_report.deltas]
    apply_series = [delta.after for delta in apply_report.deltas]
    assert dry_series == apply_series
    assert [point["trend"] for point in dry_series] == ["rising", "stable"]
    assert [point["anomaly_score"] for point in dry_series] == [20.0, 0.82]
    assert dry_session.snapshot() == dry_before
    assert not any("INSERT INTO temperature" in sql for sql, _ in dry_session.calls)
    assert [
        apply_session.values[(NOW - timedelta(days=2), "ES")]["temperature"],
        apply_session.values[(NOW - timedelta(days=1), "ES")]["temperature"],
    ] == [30.0, 20.0]


class PerformanceRecomputeSession:
    def __init__(self, *, keys_per_country=501):
        self.country_codes = ("ES", "GB")
        self.values = {}
        first = NOW - timedelta(hours=keys_per_country - 1)
        for offset in range(keys_per_country):
            at = first + timedelta(hours=offset)
            for country_code in reversed(self.country_codes):
                row = _temperature_row(at, country_code, 10.0)
                self.values[(at, country_code)] = row
        self.calls = []
        self.upserted_keys = []

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        self.calls.append((sql, params))
        if "attribution_recompute:existing_keys" in sql:
            rows = [SimpleNamespace(**row) for row in self.values.values()]
            rows.sort(key=lambda row: (row.time, row.country_code))
            return FakeResult(rows=rows)
        if "attribution_recompute:history_seeds" in sql:
            rows = []
            for country_code in self.country_codes:
                for position in range(30, 0, -1):
                    rows.append(SimpleNamespace(
                        time=NOW - timedelta(days=90, hours=position),
                        country_code=country_code,
                        temperature=10.0,
                    ))
            rows.sort(key=lambda row: (row.time, row.country_code))
            return FakeResult(rows=rows)
        if "attribution_recompute:article_inputs" in sql:
            assert params["cc"] in self.country_codes
            assert params["input_start"] == NOW - timedelta(days=104)
            assert params["window_end"] == NOW
            return FakeResult(rows=[SimpleNamespace(
                article_id=11 if params["cc"] == "ES" else 12,
                sentiment=0.3,
                event_type="diplomatic",
                sentiment_confidence=1.0,
                action_level=2,
                event_key="recent-event",
                published_at=NOW - timedelta(days=1),
                weight=1.0,
                source_id=11,
                reprint_count=0,
            )])
        if "INSERT INTO temperature" in sql:
            batch = params if isinstance(params, list) else [params]
            for item in batch:
                key = (item["time"], item["country_code"])
                assert key in self.values
                self.upserted_keys.append(key)
            return FakeResult()
        raise AssertionError(sql)


class TrackingDeque(real_deque):
    instances = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_observed = len(self)
        self.__class__.instances.append(self)

    def append(self, value):
        super().append(value)
        self.max_observed = max(self.max_observed, len(self))


def _scaled_calculator(country_code, as_of, rows, *, history=()):
    assert len(rows) <= 1
    current = 20.0
    anomaly_statistics = index._anomaly_statistics(
        current,
        history[:index.TEMPERATURE_METHODOLOGY.anomaly_history_points],
    )
    return {
        "time": as_of,
        "country_code": country_code,
        "temperature": current,
        "raw_sentiment": 0.6,
        "diplomatic": 0.3,
        "military": None,
        "economic": None,
        "cultural": None,
        "security": None,
        "article_count": len(rows),
        "source_count": len(rows),
        "trend": index._trend_from_history(
            current,
            history[:index.TEMPERATURE_METHODOLOGY.trend_history_points],
        ),
        "anomaly_score": (
            anomaly_statistics[0] if anomaly_statistics is not None else None
        ),
    }


def _run_scaled_recompute(monkeypatch, *, apply, delta_limit=7, batch_size=128):
    recompute = _load_task7_module("scripts.recompute_attribution_window")
    session = PerformanceRecomputeSession()
    TrackingDeque.instances = []
    monkeypatch.setattr(recompute, "get_session", lambda: SessionContext(session))
    monkeypatch.setattr(recompute, "_utc_now", lambda: NOW)
    monkeypatch.setattr(
        recompute,
        "calculate_temperature_from_rows",
        _scaled_calculator,
        raising=False,
    )
    monkeypatch.setattr(
        recompute,
        "calculate_temperature_at",
        lambda *args, **kwargs: pytest.fail("recompute used per-key DB calculator"),
        raising=False,
    )
    real_delta = recompute.TemperatureDelta
    created_deltas = []

    def tracked_delta(**kwargs):
        delta = real_delta(**kwargs)
        created_deltas.append(delta)
        return delta

    monkeypatch.setattr(recompute, "TemperatureDelta", tracked_delta)
    monkeypatch.setattr(recompute, "deque", TrackingDeque, raising=False)
    monkeypatch.setattr(recompute, "_run_post_apply_jobs", lambda: None)
    report = recompute.recompute_window(
        days=90,
        apply=apply,
        batch_size=batch_size,
        delta_limit=delta_limit,
    )
    return session, report, created_deltas


def test_scaled_recompute_reads_once_per_country_bounds_history_and_report(monkeypatch):
    session, report, created_deltas = _run_scaled_recompute(
        monkeypatch,
        apply=False,
    )

    read_calls = [
        sql for sql, _ in session.calls
        if "INSERT INTO temperature" not in sql
    ]
    article_reads = [
        sql for sql in read_calls if "attribution_recompute:article_inputs" in sql
    ]
    assert report.keys_read == 1002
    assert len(read_calls) == len(session.country_codes) + 2
    assert len(article_reads) == len(session.country_codes)
    assert report.delta_total == 1002
    assert len(report.deltas) == 7
    assert report.deltas_omitted == 995
    assert len(created_deltas) <= len(session.country_codes) * 7
    assert [
        (delta.time, delta.country_code) for delta in report.deltas
    ] == sorted((delta.time, delta.country_code) for delta in report.deltas)
    assert TrackingDeque.instances
    assert all(
        history.maxlen == 30 and history.max_observed <= 30
        for history in TrackingDeque.instances
    )
    assert not session.upserted_keys


def test_scaled_apply_matches_dry_samples_and_batches_exactly(monkeypatch):
    _, dry_report, _ = _run_scaled_recompute(monkeypatch, apply=False)
    apply_session, apply_report, _ = _run_scaled_recompute(
        monkeypatch,
        apply=True,
    )

    assert apply_report.deltas == dry_report.deltas
    assert apply_report.changed == dry_report.changed
    assert apply_report.upserted == 1002
    writes = [
        sql for sql, _ in apply_session.calls if "INSERT INTO temperature" in sql
    ]
    reads = [
        sql for sql, _ in apply_session.calls if "INSERT INTO temperature" not in sql
    ]
    assert len(reads) == len(apply_session.country_codes) + 2
    assert len(writes) == math.ceil(1002 / 128)
    assert apply_session.upserted_keys == sorted(apply_session.upserted_keys)


def test_delta_limit_parser_and_validation():
    recompute = _load_task7_module("scripts.recompute_attribution_window")
    args = recompute.build_parser().parse_args(["--delta-limit", "17"])

    assert args.delta_limit == 17
    with pytest.raises(ValueError, match="delta_limit"):
        recompute.recompute_window(delta_limit=-1)


class AuditSession:
    def __init__(
        self,
        *,
        foreign_ru_domains=0,
        legacy_rows=0,
        article_count=10,
        temperature_count=5,
    ):
        self.foreign_ru_domains = foreign_ru_domains
        self.legacy_rows = legacy_rows
        self.article_count = article_count
        self.temperature_count = temperature_count
        self.statements = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
            raise AssertionError("attribution audit must be read-only")
        if "attribution_audit:metadata" in sql:
            return FakeResult(row=SimpleNamespace(
                total=4,
                metadata_complete=3,
                verified=2,
                reassigned=1,
                unknown=1,
            ))
        if "attribution_audit:matrix" in sql:
            return FakeResult(rows=[
                SimpleNamespace(discovery_country="ES", publisher_country="ES", count=2),
                SimpleNamespace(discovery_country="ES", publisher_country="GB", count=1),
            ])
        if "attribution_audit:foreign_ru_domains" in sql:
            return FakeResult(row=SimpleNamespace(count=self.foreign_ru_domains))
        if "attribution_audit:legacy_rows" in sql:
            return FakeResult(row=SimpleNamespace(count=self.legacy_rows))
        if "attribution_audit:analytics_counts" in sql:
            return FakeResult(row=SimpleNamespace(
                article_count=self.article_count,
                temperature_count=self.temperature_count,
            ))
        raise AssertionError(sql)


def test_attribution_audit_reports_coverage_matrix_and_baseline_deltas(monkeypatch):
    audit = _load_task7_module("scripts.audit_google_news_attribution")
    session = AuditSession()
    monkeypatch.setattr(audit, "get_session", lambda: SessionContext(session))

    report = audit.build_attribution_audit(
        article_baseline=9,
        temperature_baseline=5,
    )

    assert report == {
        "publisher_metadata_coverage": 0.75,
        "verified": 2,
        "reassigned": 1,
        "unknown": 1,
        "matrix": {"ES": {"ES": 2, "GB": 1}},
        "foreign_ru_domains_in_country_analytics": 0,
        "legacy_rows_in_country_analytics": 0,
        "article_count": 10,
        "temperature_count": 5,
        "analytics_deltas": {
            "article_count": {"before": 9, "after": 10, "delta": 1},
            "temperature_count": {"before": 5, "after": 5, "delta": 0},
        },
    }
    assert audit.evaluate_audit_gates(
        report,
        article_baseline=9,
        temperature_baseline=5,
    ) == ()
    assert json.loads(json.dumps(report)) == report
    assert len(session.statements) == 5


def test_attribution_audit_fails_closed_on_mismatch_legacy_or_count_loss(monkeypatch):
    audit = _load_task7_module("scripts.audit_google_news_attribution")
    session = AuditSession(
        foreign_ru_domains=2,
        legacy_rows=1,
        article_count=8,
        temperature_count=4,
    )
    monkeypatch.setattr(audit, "get_session", lambda: SessionContext(session))

    report = audit.build_attribution_audit(
        article_baseline=9,
        temperature_baseline=5,
    )
    failures = audit.evaluate_audit_gates(
        report,
        article_baseline=9,
        temperature_baseline=5,
    )

    assert set(failures) == {
        "foreign_russian_domains_present",
        "legacy_rows_present_in_country_analytics",
        "article_count_below_baseline",
        "temperature_count_below_baseline",
    }
    assert report["unknown"] == 1


def test_foreign_russian_domain_gate_covers_ru_tld_and_curated_registry(monkeypatch):
    audit = _load_task7_module("scripts.audit_google_news_attribution")
    session = AuditSession()
    monkeypatch.setattr(audit, "get_session", lambda: SessionContext(session))

    audit.build_attribution_audit(
        article_baseline=9,
        temperature_baseline=5,
    )

    sql = next(
        statement
        for statement in session.statements
        if "attribution_audit:foreign_ru_domains" in statement
    )
    assert "LIKE '%.ru'" in sql
    assert "registry.country_code" in sql
    assert "registry.status = 'verified'" in sql


def test_recompute_uses_only_current_scoped_downstream_jobs():
    recompute = _load_task7_module("scripts.recompute_attribution_window")
    text = Path(recompute.__file__).read_text(encoding="utf-8")

    assert "scripts.calc_ru_index" in text
    assert "src.engine.signals" in text
    assert "scripts.generate_briefs" in text
    assert "scripts.build_threads" in text
    assert "rebuild_recent_threads_and_stories" in text
    assert "build_threads()" not in text
    assert "backfill_temperature" not in text
    assert "backfill_investigation_data" not in text


def test_recent_thread_rebuild_never_runs_global_cleanup_or_membership_replacement(
    monkeypatch,
):
    build_threads = _load_task7_module("scripts.build_threads")
    article = {
        "article_id": 11,
        "event_key": "recent-event",
        "country_code": "ES",
        "has_embedding": False,
    }
    observed = {}

    monkeypatch.setattr(
        build_threads,
        "get_session",
        lambda: SessionContext(object()),
    )
    monkeypatch.setattr(
        build_threads,
        "fetch_articles",
        lambda session, days=30: observed.setdefault("days", days) and [article],
    )
    monkeypatch.setattr(
        build_threads,
        "cluster_pass1_trgm",
        lambda session, articles: {"ES:recent-event": articles},
    )
    monkeypatch.setattr(build_threads, "cluster_pass2_llm", lambda clusters: clusters)

    def fake_upsert(session, cc, key, articles, all_keys, **kwargs):
        observed["upsert"] = kwargs
        return 42

    monkeypatch.setattr(build_threads, "upsert_thread", fake_upsert)
    monkeypatch.setattr(
        build_threads,
        "run_scoped_story_builder",
        lambda thread_ids, *, scope_start: observed.update({
            "story_thread_ids": set(thread_ids),
            "story_scope_start": scope_start,
        }),
        raising=False,
    )
    for forbidden in (
        "link_related_threads",
        "cleanup_duplicate_threads",
        "cleanup_old_threads",
        "run_story_builder",
        "build_threads",
    ):
        monkeypatch.setattr(
            build_threads,
            forbidden,
            lambda *args, _name=forbidden, **kwargs: pytest.fail(
                f"scoped rebuild called global/destructive {_name}"
            ),
        )

    build_threads.rebuild_recent_threads_and_stories(days=30, now=NOW)

    assert observed["days"] == 30
    assert observed["upsert"]["replace_memberships"] is False
    assert observed["upsert"]["generate_narrative"] is False
    assert observed["upsert"]["preserve_existing_copy"] is True
    assert observed["upsert"]["minimum_existing_last_seen"] == NOW - timedelta(days=30)
    assert observed["story_thread_ids"] == {42}
    assert observed["story_scope_start"] == NOW - timedelta(days=30)


def test_scoped_thread_upsert_guards_old_conflicts_and_contains_no_delete(monkeypatch):
    build_threads = _load_task7_module("scripts.build_threads")
    calls = []

    class OldConflictSession:
        def execute(self, statement, params=None):
            sql = str(statement)
            calls.append((sql, params))
            if "INSERT INTO threads" in sql:
                return FakeResult(row=None)
            raise AssertionError(sql)

    articles = [
        {
            "article_id": article_id,
            "sentiment": 0.0,
            "action_level": 1,
            "published_at": NOW - timedelta(days=1),
            "title": f"Article {article_id}",
            "tier": "mainstream",
            "source_name": "Source",
            "event_type": "diplomatic",
        }
        for article_id in (11, 12)
    ]
    monkeypatch.setattr(
        build_threads,
        "calculate_importance_v2",
        lambda items: {
            "importance": 3.0,
            "velocity": 1.0,
            "sentiment_shift": 0.0,
        },
    )
    monkeypatch.setattr(
        build_threads,
        "determine_arc_phase",
        lambda items: ("emerging", "developing"),
    )

    thread_id = build_threads.upsert_thread(
        OldConflictSession(),
        "ES",
        "recent-event",
        articles,
        ["recent-event"],
        replace_memberships=False,
        minimum_existing_last_seen=NOW - timedelta(days=30),
    )

    assert thread_id is None
    assert len(calls) == 1
    sql, params = calls[0]
    assert "WHERE threads.last_seen >= :minimum_existing_last_seen" in sql
    assert params["minimum_existing_last_seen"] == NOW - timedelta(days=30)
    assert "DELETE FROM" not in sql


def test_scoped_thread_upsert_skips_llm_narrative(monkeypatch):
    build_threads = _load_task7_module("scripts.build_threads")
    calls = []

    class InsertSession:
        def execute(self, statement, params=None):
            sql = str(statement)
            calls.append((sql, params))
            if "INSERT INTO threads" in sql:
                return FakeResult(row=(42,))
            if "INSERT INTO thread_articles" in sql:
                return FakeResult()
            raise AssertionError(sql)

    articles = [
        {
            "article_id": article_id,
            "sentiment": 0.0,
            "action_level": action_level,
            "published_at": NOW - timedelta(hours=hours_ago),
            "title": title,
            "tier": "mainstream",
            "source_name": "Source",
            "event_type": "diplomatic",
        }
        for article_id, action_level, hours_ago, title in (
            (11, 2, 2, "Secondary article"),
            (12, 4, 1, "Deterministic best article"),
        )
    ]
    monkeypatch.setattr(
        build_threads,
        "calculate_importance_v2",
        lambda items: {
            "importance": 8.0,
            "velocity": 2.0,
            "sentiment_shift": 0.1,
        },
    )
    monkeypatch.setattr(
        build_threads,
        "determine_arc_phase",
        lambda items: ("emerging", "developing"),
    )
    monkeypatch.setattr(
        build_threads,
        "generate_structured_narrative",
        lambda *args, **kwargs: pytest.fail("scoped upsert must not call narrative LLM"),
    )

    thread_id = build_threads.upsert_thread(
        InsertSession(),
        "ES",
        "recent-event",
        articles,
        ["recent-event"],
        replace_memberships=False,
        generate_narrative=False,
        preserve_existing_copy=True,
        minimum_existing_last_seen=NOW - timedelta(days=30),
    )

    assert thread_id == 42
    insert_sql, insert_params = calls[0]
    assert "title = threads.title" in insert_sql
    assert "narrative = threads.narrative" in insert_sql
    assert "summary_json = threads.summary_json" in insert_sql
    assert insert_params["title"] == "Deterministic best article"
    assert insert_params["narrative"] is None
    assert insert_params["summary_json"] is None


def test_full_thread_upsert_still_generates_and_updates_narrative(monkeypatch):
    build_threads = _load_task7_module("scripts.build_threads")
    calls = []
    narrative_calls = []

    class InsertSession:
        def execute(self, statement, params=None):
            sql = str(statement)
            calls.append((sql, params))
            if "INSERT INTO threads" in sql:
                return FakeResult(row=(42,))
            if "DELETE FROM thread_articles" in sql:
                return FakeResult()
            if "INSERT INTO thread_articles" in sql:
                return FakeResult()
            raise AssertionError(sql)

    articles = [
        {
            "article_id": article_id,
            "sentiment": 0.0,
            "action_level": 3,
            "published_at": NOW - timedelta(hours=article_id),
            "title": f"Article {article_id}",
            "tier": "mainstream",
            "source_name": "Source",
            "event_type": "diplomatic",
        }
        for article_id in (11, 12)
    ]
    monkeypatch.setattr(
        build_threads,
        "calculate_importance_v2",
        lambda items: {
            "importance": 8.0,
            "velocity": 2.0,
            "sentiment_shift": 0.1,
        },
    )
    monkeypatch.setattr(
        build_threads,
        "determine_arc_phase",
        lambda items: ("emerging", "developing"),
    )

    def fake_generate(cc, canonical_key, items, metrics):
        narrative_calls.append((cc, canonical_key, items, metrics))
        return {
            "title": "Generated title",
            "summary": "Generated summary.",
            "dynamics": "Generated dynamics.",
        }

    monkeypatch.setattr(build_threads, "generate_structured_narrative", fake_generate)

    thread_id = build_threads.upsert_thread(
        InsertSession(),
        "ES",
        "recent-event",
        articles,
        ["recent-event"],
    )

    assert thread_id == 42
    assert len(narrative_calls) == 1
    insert_sql, insert_params = calls[0]
    assert "title = EXCLUDED.title" in insert_sql
    assert "narrative = COALESCE(EXCLUDED.narrative, threads.narrative)" in insert_sql
    assert "summary_json = COALESCE(EXCLUDED.summary_json, threads.summary_json)" in insert_sql
    assert insert_params["title"] == "Generated title"
    assert insert_params["narrative"] == "Generated summary. Generated dynamics."
    assert json.loads(insert_params["summary_json"])["title"] == "Generated title"


def test_scoped_story_runner_forwards_cutoff_and_non_destructive_mode(monkeypatch):
    build_threads = _load_task7_module("scripts.build_threads")
    observed = {}

    def fake_build(session, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(
            clusters=1,
            stories_upserted=1,
            article_memberships=2,
        )

    monkeypatch.setattr(
        build_threads,
        "get_session",
        lambda: SessionContext(object()),
    )
    monkeypatch.setattr(build_threads, "build_global_stories", fake_build)
    monkeypatch.setattr(build_threads, "track_api_call", lambda **kwargs: None)

    scope_start = NOW - timedelta(days=30)
    build_threads.run_scoped_story_builder({41, 42}, scope_start=scope_start)

    assert observed["candidate_thread_ids"] == frozenset({41, 42})
    assert observed["candidate_article_start"] == scope_start
    assert observed["refresh_lifecycles"] is False
    assert observed["minimum_existing_last_seen"] == scope_start
    assert observed["non_destructive"] is True


def test_scoped_story_build_filters_candidates_and_skips_global_lifecycle(monkeypatch):
    import src.stories as stories

    old = SimpleNamespace(thread_id=1)
    recent = SimpleNamespace(thread_id=2)
    observed = {}

    def fake_fetch(session, **kwargs):
        observed["fetch_options"] = kwargs
        return [old, recent]

    monkeypatch.setattr(stories, "fetch_story_candidates", fake_fetch)

    def fake_pairs(session, candidates):
        observed["pair_candidates"] = list(candidates)
        return frozenset()

    def fake_clusters(candidates, *, reactivation_pairs):
        observed["cluster_candidates"] = list(candidates)
        return []

    monkeypatch.setattr(stories, "derive_reactivation_pairs", fake_pairs)
    monkeypatch.setattr(stories, "cluster_story_candidates", fake_clusters)
    monkeypatch.setattr(
        stories,
        "refresh_story_lifecycles",
        lambda *args, **kwargs: pytest.fail("scoped build ran global lifecycle update"),
    )

    result = stories.build_stories(
        object(),
        now=NOW,
        candidate_thread_ids=frozenset({2}),
        refresh_lifecycles=False,
    )

    assert result.candidates == 1
    assert observed == {
        "fetch_options": {"thread_ids": frozenset({2})},
        "pair_candidates": [recent],
        "cluster_candidates": [recent],
    }
