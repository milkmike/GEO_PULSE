from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import re
from types import SimpleNamespace

import pytest

from src.api.routes import world


class QueryResult:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class SequentialSession:
    def __init__(self, result_sets):
        self.result_sets = list(result_sets)
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        rows = self.result_sets.pop(0) if self.result_sets else ()
        return QueryResult(rows)


def test_country_dossier_history_uses_utc_daily_last_persisted_points(monkeypatch):
    selected_time = datetime(
        2026,
        7,
        16,
        1,
        45,
        tzinfo=timezone(timedelta(hours=3)),
    )
    latest = SimpleNamespace(
        score=12.5,
        level="neutral",
        structural=10,
        media=2,
        boost=0.5,
        delta_24h=7.25,
        delta_7d=8,
        details={},
        time=selected_time,
        version="v1",
    )
    daily_last = SimpleNamespace(
        time_bucket=datetime(2026, 7, 15),
        time=selected_time,
        score=12.5,
        structural=10,
        media=2,
        boost=0.5,
        version="v1",
        delta_24h=7.25,
    )
    session = SequentialSession([[latest], [daily_last], [], [], []])

    @contextmanager
    def session_factory():
        yield session

    monkeypatch.setattr(world, "get_session", session_factory)

    dossier = world.country_dossier("es", days=30)

    assert dossier["index_history"] == [
        {
            "day": "2026-07-15",
            "time": "2026-07-15T22:45:00+00:00",
            "score": 12.5,
            "structural": 10.0,
            "media": 2.0,
            "boost": 0.5,
            "version": "v1",
            "delta_24h": 7.25,
            "aggregation": "daily_last",
        }
    ]

    history_sql = " ".join(session.calls[1][0].split())
    assert "DISTINCT ON (date_trunc('day', time AT TIME ZONE 'UTC'))" in history_sql
    assert "ORDER BY date_trunc('day', time AT TIME ZONE 'UTC'), time DESC" in history_sql
    assert "AVG(" not in history_sql.upper()
    assert session.calls[1][1] == {"cc": "ES", "days": 30}
    assert all(sql.lstrip().upper().startswith("SELECT") for sql, _ in session.calls)


def test_signal_list_treats_null_expiry_as_inactive(monkeypatch):
    created_at = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    signal = SimpleNamespace(
        id=22,
        signal_type="tone_shift",
        country_code="ES",
        severity="warning",
        confidence=0.8,
        title="Исторический сигнал",
        description=None,
        payload={},
        created_at=created_at,
        expires_at=None,
    )
    session = SequentialSession([[signal]])

    @contextmanager
    def session_factory():
        yield session

    monkeypatch.setattr(world, "get_session", session_factory)

    result = world.list_signals(
        days=7,
        country=None,
        signal_type=None,
        active_only=False,
        limit=10,
    )

    assert result["signals"][0]["expires_at"] is None
    assert result["signals"][0]["active"] is False


def test_signal_list_includes_context_article_preview_in_one_batch(monkeypatch):
    created_at = datetime(2026, 7, 15, 3, 0, tzinfo=timezone.utc)
    signal = SimpleNamespace(
        id=22,
        signal_type="tone_shift",
        country_code="ES",
        severity="warning",
        confidence=0.8,
        title="Изменение риторики",
        description=None,
        payload={},
        created_at=created_at,
        expires_at=None,
    )
    preview = SimpleNamespace(
        signal_id=22,
        kind="context",
        total=47,
        window_hours=72,
        window_start=datetime(2026, 7, 12, 3, 0, tzinfo=timezone.utc),
        window_end=created_at,
        article_id=501,
        title="Правительство прокомментировало отношения с Россией",
        url="https://example.es/story",
        published_at=datetime(2026, 7, 15, 1, 30, tzinfo=timezone.utc),
        source_name="Ejemplo",
        country_code="ES",
    )
    session = SequentialSession([[signal], [preview]])

    @contextmanager
    def session_factory():
        yield session

    monkeypatch.setattr(world, "get_session", session_factory)

    result = world.list_signals(
        days=7,
        country=None,
        signal_type=None,
        active_only=False,
        limit=10,
    )

    assert result["signals"][0]["evidence_preview"] == {
        "kind": "context",
        "total": 47,
        "window_hours": 72,
        "window_start": "2026-07-12T03:00:00+00:00",
        "window_end": "2026-07-15T03:00:00+00:00",
        "articles": [{
            "id": 501,
            "title": "Правительство прокомментировало отношения с Россией",
            "url": "https://example.es/story",
            "published_at": "2026-07-15T01:30:00+00:00",
            "source_name": "Ejemplo",
            "country_code": "ES",
        }],
    }
    assert len(session.calls) == 2
    assert session.calls[1][1]["signal_ids"] == [22]
    assert session.calls[1][1]["lim"] == 2
    preview_sql = " ".join(session.calls[1][0].split())
    assert "requested AS" in preview_sql
    assert "LEFT JOIN signal_evidence" in preview_sql
    assert "WITH ORDINALITY" in preview_sql
    assert "cardinality(requested.article_ids) = 0" in preview_sql
    assert "context_windows AS MATERIALIZED" in preview_sql
    assert (
        "SELECT DISTINCT country_code, context_start, context_end FROM requested"
        in preview_sql
    )
    assert re.search(
        r"signal\.signal_type IN \(\s*'tone_shift', 'volume_surge', 'index_shift'\s*\)",
        preview_sql,
    )
    assert "context_article_pool AS MATERIALIZED" in preview_sql
    assert "MIN(context_start) AS global_start" in preview_sql
    assert "MAX(context_end) AS global_end" in preview_sql
    assert "ar.published_at >= context_pool_bounds.global_start" in preview_sql
    assert "ar.published_at < context_pool_bounds.global_end" in preview_sql
    assert "context_article_pool.published_at >= context_windows.context_start" in preview_sql
    assert "context_article_pool.published_at < context_windows.context_end" in preview_sql
    assert "context_article_pool.published_at > context_windows.context_start" not in preview_sql
    assert "context_article_pool.published_at <= context_windows.context_end" not in preview_sql
    assert "ar.is_duplicate = FALSE" in preview_sql
    assert "analysis.is_relevant = TRUE" in preview_sql
    assert "source.country_code = ANY(context_pool_bounds.country_codes)" in preview_sql
    assert "ROW_NUMBER() OVER" in preview_sql
    assert "context_ranked AS" in preview_sql
    assert (
        "PARTITION BY window_country_code, context_start, context_end"
        in preview_sql
    )
    assert "context_top AS MATERIALIZED" in preview_sql
    assert "context_previews AS" in preview_sql
    assert "JOIN context_top" in preview_sql
    assert "FROM requested LEFT JOIN LATERAL ( WITH exact_candidates AS" not in preview_sql
    assert "JOIN articles ar ON ar.source_id = source.id" in preview_sql
    assert preview_sql.count("JOIN articles ar ON ar.source_id = source.id") == 1
    assert "FROM context_windows JOIN articles" not in preview_sql
    assert "FROM context_windows JOIN sources" not in preview_sql
    assert "FROM context_windows JOIN context_article_pool" in preview_sql
    assert "LEFT JOIN candidates" not in preview_sql
    assert "evidence_ordinality ASC NULLS LAST" in preview_sql
    assert "analysis_action_level DESC NULLS LAST" in preview_sql
    assert "absolute_sentiment DESC NULLS LAST" in preview_sql
    assert "reprint_count DESC NULLS LAST" in preview_sql
    assert "published_at DESC NULLS LAST" in preview_sql
    assert "candidate_rank <= :lim" in preview_sql


def test_signal_list_batches_exact_previews_and_sanitizes_urls(monkeypatch):
    created_at = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    signals = [
        SimpleNamespace(
            id=31,
            signal_type="notable_event",
            country_code="ES",
            severity="critical",
            confidence=0.9,
            title="Подтверждённое событие",
            description="Есть сохранённая доказательная статья.",
            payload={},
            created_at=created_at,
            expires_at=created_at + timedelta(hours=24),
        ),
        SimpleNamespace(
            id=32,
            signal_type="index_shift",
            country_code="FR",
            severity="warning",
            confidence=0.7,
            title="Сигнал без статей",
            description=None,
            payload={},
            created_at=created_at,
            expires_at=None,
        ),
        SimpleNamespace(
            id=33,
            signal_type="official_silence",
            country_code="DE",
            severity="info",
            confidence=0.5,
            title="Сигнал без допустимого контекстного fallback",
            description=None,
            payload={},
            created_at=created_at,
            expires_at=None,
        ),
    ]
    exact_preview = SimpleNamespace(
        signal_id=31,
        kind="evidence",
        total=1,
        window_hours=None,
        window_start=created_at - timedelta(hours=24),
        window_end=created_at,
        article_id=601,
        title="Подтверждение",
        url="javascript:alert(1)",
        published_at=created_at - timedelta(minutes=30),
        source_name="Unsafe",
        country_code="ES",
    )
    empty_context = SimpleNamespace(
        signal_id=32,
        kind="context",
        total=0,
        window_hours=72,
        window_start=created_at - timedelta(hours=72),
        window_end=created_at,
        article_id=None,
        title=None,
        url=None,
        published_at=None,
        source_name=None,
        country_code=None,
    )
    unavailable = SimpleNamespace(
        signal_id=33,
        kind="unavailable",
        total=0,
        window_hours=None,
        window_start=None,
        window_end=None,
        article_id=None,
        title=None,
        url=None,
        published_at=None,
        source_name=None,
        country_code=None,
    )
    session = SequentialSession(
        [signals, [exact_preview, empty_context, unavailable]]
    )

    @contextmanager
    def session_factory():
        yield session

    monkeypatch.setattr(world, "get_session", session_factory)

    result = world.list_signals(
        days=7,
        country=None,
        signal_type=None,
        active_only=False,
        limit=10,
    )

    assert result["signals"][0]["evidence_preview"] == {
        "kind": "evidence",
        "total": 1,
        "window_hours": None,
        "window_start": "2026-07-14T12:00:00+00:00",
        "window_end": "2026-07-15T12:00:00+00:00",
        "articles": [{
            "id": 601,
            "title": "Подтверждение",
            "url": None,
            "published_at": "2026-07-15T11:30:00+00:00",
            "source_name": "Unsafe",
            "country_code": "ES",
        }],
    }
    assert result["signals"][1]["evidence_preview"] == {
        "kind": "context",
        "total": 0,
        "window_hours": 72,
        "window_start": "2026-07-12T12:00:00+00:00",
        "window_end": "2026-07-15T12:00:00+00:00",
        "articles": [],
    }
    assert result["signals"][2]["evidence_preview"] == {
        "kind": "unavailable",
        "total": 0,
        "window_hours": None,
        "window_start": None,
        "window_end": None,
        "articles": [],
    }
    assert len(session.calls) == 2
    assert session.calls[1][1]["signal_ids"] == [31, 32, 33]
    assert session.calls[1][1]["lim"] == 2


def test_legacy_gdelt_signal_context_prefers_payload_matching_aggregate_day(monkeypatch):
    created_at = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    signal = SimpleNamespace(
        id=3675,
        signal_type="tone_shift",
        country_code="ES",
        severity="warning",
        confidence=0.8,
        title="Сдвиг тона о России",
        description="Сигнал построен на агрегате GDELT за 10 июля.",
        payload={"tone": -6.22},
        created_at=created_at,
        expires_at=None,
    )
    summary = SimpleNamespace(
        signal_id=3675,
        kind="context",
        total=0,
        window_hours=72,
        window_start=datetime(2026, 7, 8, tzinfo=timezone.utc),
        window_end=datetime(2026, 7, 11, tzinfo=timezone.utc),
        article_id=None,
        title=None,
        url=None,
        published_at=None,
        source_name=None,
        country_code=None,
    )
    session = SequentialSession([[signal], [summary]])

    @contextmanager
    def session_factory():
        yield session

    monkeypatch.setattr(world, "get_session", session_factory)

    result = world.list_signals(
        days=7,
        country=None,
        signal_type=None,
        active_only=False,
        limit=10,
    )

    assert result["signals"][0]["evidence_preview"] == {
        "kind": "context",
        "articles": [],
        "total": 0,
        "window_hours": 72,
        "window_start": "2026-07-08T00:00:00+00:00",
        "window_end": "2026-07-11T00:00:00+00:00",
    }
    assert len(session.calls) == 2
    preview_sql = " ".join(session.calls[1][0].split())
    assert "gdelt_daily" in preview_sql
    assert "signal.signal_type IN ('tone_shift', 'volume_surge')" in preview_sql
    assert "gdelt.day <= (signal.created_at AT TIME ZONE 'UTC')::date" in preview_sql
    assert "gdelt.day <= signal.created_at::date" not in preview_sql
    assert "jsonb_typeof(signal.payload -> 'tone') = 'number'" in preview_sql
    compact_preview_sql = re.sub(r"\s+", "", preview_sql)
    assert (
        "gdelt.tone_avg-CASEWHENjsonb_typeof(signal.payload->'tone')='number'"
        "THEN(signal.payload->>'tone')::numericEND)<=0.01"
        in compact_preview_sql
    )
    assert "jsonb_typeof(signal.payload -> 'share') = 'number'" in preview_sql
    assert "jsonb_typeof(signal.payload -> 'volume') = 'number'" in preview_sql
    assert (
        "gdelt.volume_share-CASEWHENjsonb_typeof(signal.payload->'share')='number'"
        "THEN(signal.payload->>'share')::numericEND)<=0.00001"
        in compact_preview_sql
    )
    assert (
        "gdelt.volume-CASEWHENjsonb_typeof(signal.payload->'volume')='number'"
        "THEN(signal.payload->>'volume')::numericEND)<=0.01"
        in compact_preview_sql
    )
    assert preview_sql.index("gdelt.tone_avg") < preview_sql.index("gdelt.day DESC")
    assert "gdelt.day DESC" in preview_sql
    assert "gdelt_anchor.day::timestamp AT TIME ZONE 'UTC'" in preview_sql
    assert "+ INTERVAL '1 day'" in preview_sql
    assert re.search(
        r"cardinality\(\s*COALESCE\(evidence\.article_ids, '\{\}'::integer\[\]\)\s*\) = 0",
        preview_sql,
    )
    assert session.calls[1][1] == {"signal_ids": [3675], "lim": 2}


@pytest.mark.parametrize("limit", [0, 101])
def test_signal_article_preview_limit_must_be_between_one_and_one_hundred(limit):
    from src.api.signal_article_context import load_signal_article_previews

    session = SequentialSession([])

    with pytest.raises(ValueError):
        load_signal_article_previews(session, [], limit=limit)

    assert session.calls == []
