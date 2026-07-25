from __future__ import annotations

import gc
from pathlib import Path
import weakref

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_embedding_cycle_prepares_all_story_eligible_articles_before_indexing():
    from scripts.run_embedding_worker import run_embedding_cycle

    calls: list[tuple[str, dict[str, object]]] = []

    report = run_embedding_cycle(
        days=30,
        prepare_limit=500,
        batch_size=50,
        index_limit=500,
        prepare=lambda **kwargs: calls.append(("prepare", kwargs))
        or {"enqueued": 7},
        index=lambda **kwargs: calls.append(("index", kwargs))
        or {"indexed": 7},
    )

    assert calls == [
        (
            "prepare",
            {
                "days": 30,
                "limit": 500,
                "story_eligible_articles": True,
            },
        ),
        ("index", {"batch_size": 50, "limit": 500}),
    ]
    assert report == {
        "prepare": {"enqueued": 7},
        "index": {"indexed": 7},
    }


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("days", 0),
        ("prepare_limit", 0),
        ("batch_size", 0),
        ("index_limit", 0),
    ),
)
def test_embedding_cycle_rejects_non_positive_bounds(field: str, value: int):
    from scripts.run_embedding_worker import run_embedding_cycle

    options = {
        "days": 30,
        "prepare_limit": 500,
        "batch_size": 50,
        "index_limit": 500,
    }
    options[field] = value

    with pytest.raises(ValueError, match=field):
        run_embedding_cycle(
            **options,
            prepare=lambda **_kwargs: {},
            index=lambda **_kwargs: {},
        )


def test_embedding_loop_sleeps_between_completed_cycles_only():
    from scripts.run_embedding_worker import run_loop

    calls: list[str] = []
    sleeps: list[float] = []
    ticks = iter((10.0, 12.5, 20.0, 22.0))

    reports = run_loop(
        interval=10,
        cycle=lambda: calls.append("cycle") or {"ok": len(calls)},
        sleep=sleeps.append,
        monotonic=lambda: next(ticks),
        max_cycles=2,
    )

    assert calls == ["cycle", "cycle"]
    assert sleeps == [7.5]
    assert reports == [{"ok": 1}, {"ok": 2}]


def test_embedding_loop_continues_after_one_failed_cycle():
    from scripts.run_embedding_worker import run_loop

    attempts = 0
    sleeps: list[float] = []

    def cycle():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("provider unavailable")
        return {"indexed": 3}

    reports = run_loop(
        interval=5,
        cycle=cycle,
        sleep=sleeps.append,
        monotonic=iter((1.0, 1.5, 7.0, 8.0)).__next__,
        max_cycles=2,
    )

    assert attempts == 2
    assert sleeps == [4.5]
    assert reports == [{"error": "provider unavailable"}, {"indexed": 3}]


def test_unbounded_embedding_loop_does_not_retain_prior_reports():
    from scripts.run_embedding_worker import run_loop

    class Report(dict):
        pass

    class StopLoop(Exception):
        pass

    refs = []

    def cycle():
        report = Report(indexed=len(refs) + 1)
        refs.append(weakref.ref(report))
        return report

    def stop_after_second_cycle(_seconds):
        if len(refs) < 2:
            return
        gc.collect()
        assert refs[0]() is None
        raise StopLoop

    with pytest.raises(StopLoop):
        run_loop(
            interval=5,
            cycle=cycle,
            sleep=stop_after_second_cycle,
            monotonic=iter((0.0, 0.5, 5.0, 5.5)).__next__,
        )


def test_embedding_worker_is_in_analyzer_image_and_production_compose():
    dockerfile = (ROOT / "Dockerfile.analyzer").read_text()
    services = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]

    assert "scripts/run_embedding_worker.py" in dockerfile
    worker = services["embedding-worker"]
    assert worker["build"]["dockerfile"] == "Dockerfile.analyzer"
    assert worker["command"] == (
        "python scripts/run_embedding_worker.py --loop --interval 300 "
        "--days 30 --prepare-limit 500 --batch 50 --index-limit 500"
    )
    assert worker["restart"] == "unless-stopped"
    assert worker["environment"]["HTTPS_PROXY"] == "${HTTPS_PROXY:-}"
    assert worker["environment"]["OPENROUTER_EMBEDDING_DIMENSIONS"] == (
        "${OPENROUTER_EMBEDDING_DIMENSIONS:-1024}"
    )
