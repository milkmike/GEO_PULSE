from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.pipeline import briefs


class FakeResult:
    def __init__(self, *, row=None, count=0):
        self.row = row
        self.count = count

    def fetchone(self):
        return self.row

    def scalar_one(self):
        return self.count


class FakeSession:
    def __init__(self, *, row=None, topic_count=0):
        self.row = row
        self.topic_count = topic_count

    def execute(self, statement, params=None):
        if "FROM briefs" in str(statement):
            return FakeResult(row=self.row)
        return FakeResult(count=self.topic_count)


class FakeSessionContext:
    def __init__(self, row=None, *, topic_count=0):
        self.session = FakeSession(row=row, topic_count=topic_count)

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc, traceback):
        return False


def cached_topic_row():
    return SimpleNamespace(
        content="cached culture brief",
        model="qwen",
        created_at=datetime(2026, 7, 16, 13, 0, tzinfo=timezone.utc),
        meta={"citations": [{"n": 1}]},
    )


def test_read_cached_topic_brief_never_generates(monkeypatch):
    monkeypatch.setattr(briefs, "get_session", lambda: FakeSessionContext(cached_topic_row()))
    monkeypatch.setattr(briefs, "chat", lambda *args, **kwargs: pytest.fail("LLM called"))

    result = briefs.read_cached_topic_brief("culture_sport")

    assert result["content"] == "cached culture brief"
    assert result["cached"] is True


def test_topic_has_inputs_distinguishes_empty_topic(monkeypatch):
    monkeypatch.setattr(briefs, "get_session", lambda: FakeSessionContext(topic_count=3))

    assert briefs.topic_has_inputs("culture_sport") is True
