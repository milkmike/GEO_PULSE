from __future__ import annotations

from contextlib import contextmanager

from scripts import backfill_google_news_attribution as backfill


INVARIANTS = {
    "max_article_id": 0,
    "article_rows": 0,
    "protected_article_rows": 0,
    "analysis_rows": 0,
    "story_article_rows": 0,
    "provenance_sha256": "empty",
}


class EmptyResult:
    rowcount = 0

    def mappings(self):
        return self

    def all(self):
        return []


class RecordingSession:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        self.statements.append(" ".join(str(statement).split()))
        return EmptyResult()


def _install_empty_backend(monkeypatch):
    session = RecordingSession()

    @contextmanager
    def session_factory():
        yield session

    monkeypatch.setattr(backfill, "get_session", session_factory)
    monkeypatch.setattr(backfill, "_publisher_map", lambda _session: {})
    monkeypatch.setattr(
        backfill,
        "_snapshot_invariants",
        lambda _session, **_kwargs: dict(INVARIANTS),
    )
    return session


def test_unclassified_update_enables_transaction_local_lz4_before_writes():
    sql = " ".join(backfill.UNCLASSIFIED_UPDATE_SQL.split())

    assert sql.startswith("/* gnews-backfill:update-unclassified */ WITH")
    assert "set_config('wal_compression', 'lz4', true)" in sql
    assert "AS MATERIALIZED" in sql
    assert "FROM wal_settings" in sql
    assert sql.index("set_config") < sql.index("UPDATE articles")


def test_dry_run_does_not_change_wal_compression(monkeypatch):
    session = _install_empty_backend(monkeypatch)

    report = backfill.run_backfill(apply=False)

    assert report.done is True
    assert all("wal_compression" not in sql for sql in session.statements)
