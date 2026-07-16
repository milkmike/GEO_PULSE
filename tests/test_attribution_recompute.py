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
        if "attribution_recompute:history_seeds" in sql:
            rows = [
                SimpleNamespace(**row)
                for row in self.values.values()
                if row["country_code"] in params["country_codes"]
                and row["time"] < params["window_start"]
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


class HistoryQueryForbiddenSession:
    def execute(self, statement, params=None):
        raise AssertionError(
            "rolling recompute must provide repaired in-memory temperature history"
        )

    def add(self, item):
        raise AssertionError("historical recompute must not emit alerts")


def _rolling_calculator(session: RollingRecomputeSession):
    def calculate(country_code, as_of, *, exclude_backfill=True):
        assert exclude_backfill is True
        current = 30.0 if as_of == NOW - timedelta(days=2) else 20.0
        old = session.values[(as_of, country_code)]
        result = {
            key: value
            for key, value in old.items()
            if key != "pattern_type"
        }
        history_session = HistoryQueryForbiddenSession()
        result["temperature"] = current
        result["raw_sentiment"] = round(current / (100 / 3), 2)
        result["trend"] = index.detect_trend(
            history_session,
            country_code,
            current,
            as_of=as_of,
        )
        result["anomaly_score"] = index.detect_anomaly(
            history_session,
            country_code,
            current,
            as_of=as_of,
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
            "calculate_temperature_at",
            _rolling_calculator(session),
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
    monkeypatch.setattr(
        stories,
        "fetch_story_candidates",
        lambda session: [old, recent],
    )

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
        "pair_candidates": [recent],
        "cluster_candidates": [recent],
    }
