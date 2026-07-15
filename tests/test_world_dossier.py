from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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
