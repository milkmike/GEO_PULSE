from __future__ import annotations

import importlib
import json
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
        if "ORDER BY time DESC LIMIT 3" in sql:
            return FakeResult(rows=[
                SimpleNamespace(temperature=20.0),
                SimpleNamespace(temperature=25.0),
            ])
        if "ORDER BY time DESC LIMIT 30" in sql:
            return FakeResult(rows=[])
        raise AssertionError(sql)

    def add(self, item):
        raise AssertionError("as-of calculation must not emit persisted alerts")


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
    def calculate(country_code, as_of, *, exclude_backfill=True):
        assert exclude_backfill is True
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
    assert len(history_calls) == 2
    assert all("time < :as_of" in sql for sql, _ in history_calls)
    assert all(params["as_of"] == NOW for _, params in history_calls)


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
        "calculate_temperature_at",
        _calculator_for(session, calculator_calls),
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
        "calculate_temperature_at",
        _calculator_for(session, calculator_calls),
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
    assert "backfill_temperature" not in text
    assert "backfill_investigation_data" not in text
