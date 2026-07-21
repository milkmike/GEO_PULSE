from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


NOW = datetime(2026, 7, 16, 15, 30, tzinfo=timezone.utc)


@contextmanager
def _session_context(session):
    yield session


class _ThreadIdResult:
    def fetchall(self):
        return [SimpleNamespace(thread_id=42), SimpleNamespace(thread_id=41)]


class _RecentThreadSession:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params))
        return _ThreadIdResult()


def test_hourly_story_builder_is_bounded_to_recent_non_destructive_scope(
    monkeypatch,
):
    import scripts.build_threads as build_threads

    observed = {}
    monkeypatch.setattr(
        build_threads,
        "get_session",
        lambda: _session_context(object()),
    )
    monkeypatch.setattr(
        build_threads,
        "build_global_stories",
        lambda _session, **kwargs: observed.update(kwargs)
        or SimpleNamespace(
            clusters=0,
            stories_upserted=0,
            article_memberships=0,
        ),
    )
    monkeypatch.setattr(build_threads, "track_api_call", lambda **_kwargs: None)

    build_threads.run_story_builder(recent_days=30, now=NOW)

    assert observed["candidate_article_start"] == NOW - timedelta(days=30)
    assert observed["minimum_existing_last_seen"] == NOW - timedelta(days=30)
    assert observed["non_destructive"] is True
    assert observed["refresh_lifecycles"] is True


def test_stories_only_recent_rebuild_queries_canonical_bounded_threads(monkeypatch):
    import scripts.build_threads as build_threads

    session = _RecentThreadSession()
    observed = {}
    scope_start = NOW - timedelta(days=7)

    monkeypatch.setattr(
        build_threads,
        "get_session",
        lambda: _session_context(session),
    )
    monkeypatch.setattr(
        build_threads,
        "run_scoped_story_builder",
        lambda thread_ids, *, scope_start, raise_on_error=False: observed.update(
            thread_ids=set(thread_ids),
            scope_start=scope_start,
            raise_on_error=raise_on_error,
        ),
    )
    for forbidden in (
        "fetch_articles",
        "cluster_pass1_embeddings",
        "cluster_pass1_trgm",
        "cluster_pass2_llm",
        "upsert_thread",
        "generate_structured_narrative",
        "cleanup_duplicate_threads",
        "cleanup_old_threads",
        "run_story_builder",
        "build_threads",
    ):
        monkeypatch.setattr(
            build_threads,
            forbidden,
            lambda *args, _name=forbidden, **kwargs: pytest.fail(
                f"stories-only rebuild called {_name}"
            ),
        )

    build_threads.rebuild_recent_stories(days=7, now=NOW)

    assert len(session.calls) == 1
    sql, params = session.calls[0]
    assert params == {"scope_start": scope_start}
    assert "SELECT DISTINCT t.id AS thread_id" in sql
    assert "JOIN thread_articles ta ON ta.thread_id = t.id" in sql
    assert "JOIN articles ar ON ar.id = ta.article_id" in sql
    assert "JOIN article_country_facts s" in sql
    assert "s.article_id = ar.id" in sql
    assert "TRIM(s.country_code) = TRIM(t.country_code)" in sql
    assert "t.article_count > 0" in sql
    assert "ar.published_at >= :scope_start" in sql
    assert "ORDER BY t.id" in sql
    assert "DELETE" not in sql.upper()
    assert observed == {
        "thread_ids": {41, 42},
        "scope_start": scope_start,
        "raise_on_error": True,
    }


def test_scoped_story_runner_swallows_failures_by_default(monkeypatch):
    import scripts.build_threads as build_threads

    failure = RuntimeError("story persistence failed")
    tracked = []
    monkeypatch.setattr(
        build_threads,
        "get_session",
        lambda: _session_context(object()),
    )
    monkeypatch.setattr(
        build_threads,
        "build_global_stories",
        lambda *args, **kwargs: (_ for _ in ()).throw(failure),
    )
    monkeypatch.setattr(
        build_threads,
        "track_api_call",
        lambda **kwargs: tracked.append(kwargs),
    )

    build_threads.run_scoped_story_builder(
        {41, 42},
        scope_start=NOW - timedelta(days=7),
    )

    assert tracked[-1]["status"] == "error"


def test_scoped_story_runner_propagates_failures_when_requested(monkeypatch):
    import scripts.build_threads as build_threads

    failure = RuntimeError("story persistence failed")
    tracked = []
    monkeypatch.setattr(
        build_threads,
        "get_session",
        lambda: _session_context(object()),
    )
    monkeypatch.setattr(
        build_threads,
        "build_global_stories",
        lambda *args, **kwargs: (_ for _ in ()).throw(failure),
    )
    monkeypatch.setattr(
        build_threads,
        "track_api_call",
        lambda **kwargs: tracked.append(kwargs),
    )

    with pytest.raises(RuntimeError, match="story persistence failed"):
        build_threads.run_scoped_story_builder(
            {41, 42},
            scope_start=NOW - timedelta(days=7),
            raise_on_error=True,
        )

    assert tracked[-1]["status"] == "error"


def test_stories_only_recent_cli_routes_only_to_story_refresh(monkeypatch):
    import scripts.build_threads as build_threads

    calls = []
    monkeypatch.setattr(build_threads, "wait_for_db", lambda: calls.append("wait"))
    monkeypatch.setattr(
        build_threads,
        "rebuild_recent_stories",
        lambda days: calls.append(("stories-only", days)),
        raising=False,
    )
    monkeypatch.setattr(
        build_threads,
        "rebuild_recent_threads_and_stories",
        lambda days: pytest.fail("stories-only CLI ran thread rebuild"),
    )
    monkeypatch.setattr(
        build_threads,
        "build_threads",
        lambda: pytest.fail("stories-only CLI ran global rebuild"),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["build_threads.py", "--stories-only-recent-days", "7"],
    )

    build_threads.main()

    assert calls == ["wait", ("stories-only", 7)]


def test_stories_only_recent_cli_does_not_hide_rebuild_failure(monkeypatch):
    import scripts.build_threads as build_threads

    monkeypatch.setattr(build_threads, "wait_for_db", lambda: None)
    monkeypatch.setattr(
        build_threads,
        "rebuild_recent_stories",
        lambda days: (_ for _ in ()).throw(RuntimeError("story rebuild failed")),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["build_threads.py", "--stories-only-recent-days", "7"],
    )

    with pytest.raises(RuntimeError, match="story rebuild failed"):
        build_threads.main()


@pytest.mark.parametrize(
    "args",
    [
        ["--loop", "--stories-only-recent-days", "7"],
        ["--recent-days", "30", "--stories-only-recent-days", "7"],
        ["--loop", "--recent-days", "30"],
    ],
)
def test_build_thread_modes_are_mutually_exclusive(args):
    import scripts.build_threads as build_threads

    with pytest.raises(SystemExit):
        build_threads.build_parser().parse_args(args)


def test_stories_only_recent_days_must_be_positive():
    import scripts.build_threads as build_threads

    with pytest.raises(ValueError, match="days must be positive"):
        build_threads.rebuild_recent_stories(days=0, now=NOW)
