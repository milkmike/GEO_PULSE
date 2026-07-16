from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from scripts import generate_briefs
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


def test_generate_topic_briefs_isolates_one_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(generate_briefs, "TOPICS", {"a": "A", "b": "B", "c": "C"}, raising=False)

    def fake(topic, force=False):
        calls.append((topic, force))
        if topic == "b":
            raise RuntimeError("provider down")
        return {"content": topic}

    monkeypatch.setattr(generate_briefs, "generate_topic_brief", fake, raising=False)
    monkeypatch.setattr(generate_briefs.time, "sleep", lambda _seconds: None)

    assert generate_briefs.generate_topic_briefs(force=True) == {
        "generated": 2,
        "empty": 0,
        "failed": 1,
    }
    assert calls == [("a", True), ("b", True), ("c", True)]


def test_run_pass_orders_world_topics_then_countries(monkeypatch):
    calls = []
    monkeypatch.setattr(generate_briefs, "generate_world_brief", lambda force=False: calls.append(("world", force)))
    monkeypatch.setattr(
        generate_briefs,
        "generate_topic_briefs",
        lambda force=False: calls.append(("topics", force)) or {"generated": 2, "empty": 0, "failed": 1},
        raising=False,
    )
    monkeypatch.setattr(generate_briefs, "tier1_codes", lambda: ["AA", "BB"])
    monkeypatch.setattr(
        generate_briefs,
        "generate_country_brief",
        lambda code, max_age_hours, force=False: calls.append((code, max_age_hours, force)),
    )
    monkeypatch.setattr(generate_briefs.time, "sleep", lambda _seconds: None)

    generate_briefs.run_pass(country_max_age_hours=12, force=True)

    assert calls == [
        ("world", True),
        ("topics", True),
        ("AA", 12, True),
        ("BB", 12, True),
    ]


@pytest.mark.parametrize("loop", [False, True])
def test_topics_only_skips_world_and_country_work(monkeypatch, loop):
    calls = []
    argv = ["generate_briefs.py", "--topics-only", "--force"]
    if loop:
        argv.append("--loop")

    monkeypatch.setattr(generate_briefs, "wait_for_db", lambda: None)
    monkeypatch.setattr(
        generate_briefs,
        "generate_topic_briefs",
        lambda force=False: calls.append(("topics", force)),
        raising=False,
    )
    monkeypatch.setattr(
        generate_briefs,
        "generate_world_brief",
        lambda *args, **kwargs: pytest.fail("world brief called"),
    )
    monkeypatch.setattr(
        generate_briefs,
        "run_pass",
        lambda *args, **kwargs: pytest.fail("country pass called"),
    )
    monkeypatch.setattr("sys.argv", argv)

    if loop:
        monkeypatch.setattr(
            generate_briefs.time,
            "sleep",
            lambda _seconds: (_ for _ in ()).throw(SystemExit),
        )
        with pytest.raises(SystemExit):
            generate_briefs.main()
    else:
        generate_briefs.main()

    assert calls == [("topics", True)]
