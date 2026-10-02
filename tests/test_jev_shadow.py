from __future__ import annotations

import copy
import json
import time
import subprocess
from types import SimpleNamespace
from contextlib import contextmanager
from datetime import datetime, timezone

import httpx
import pytest


def mock_http(monkeypatch, handler):
    # Keep the actual HTTP adapter and JSON parser; replace external HTTP only.
    import src.jev as jev
    import src.jev_http as worker

    client = httpx.Client
    monkeypatch.setattr(worker.httpx, "Client", lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(jev, "_request", worker.send)


def test_every_jev_request_sends_server_price_guard(monkeypatch):
    import src.jev as jev

    calls = []
    def run(*args, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        return SimpleNamespace(stdout=b'{"status":"timeout"}')
    monkeypatch.setattr(jev.subprocess, "run", run)
    payload = {"model": jev.MODEL, "state": {}, "questions": {},
               "provider": {"max_price": {"prompt": 99}, "allow_fallbacks": True}}
    assert jev._request(payload, "test-only", 5) == {"status": "timeout"}
    assert calls[0]["payload"]["provider"] == {
        "max_price": {"prompt": 0.1, "completion": 0, "request": 0},
        "allow_fallbacks": False,
    }
    assert payload["provider"]["allow_fallbacks"] is True  # Caller was not mutated.


def test_jev_request_rejects_wrong_model_or_oversize_before_transport(monkeypatch):
    import src.jev as jev
    monkeypatch.setattr(jev.subprocess, "run", lambda *a, **k: pytest.fail("unguarded request"))
    with pytest.raises(ValueError):
        jev._request({"model": "other/model", "state": {}, "questions": {}}, "test-only", 5)
    with pytest.raises(ValueError):
        jev._request({"model": jev.MODEL, "state": {"text": "x" * 24_000},
                      "questions": {}}, "test-only", 5)


def articles():
    return [
        {"article_id": 1, "country_code": "KZ", "title": "Министры подписали договор",
         "event_key": "подписание договора астана", "excerpt": "Договор подписан 29 сентября.",
         "published_at": datetime(2026, 9, 29, tzinfo=timezone.utc)},
        {"article_id": 2, "country_code": "KZ", "title": "В Астане подписан договор",
         "event_key": "договор подписан астана", "excerpt": "Переговоры завершились подписанием.",
         "published_at": datetime(2026, 9, 29, tzinfo=timezone.utc)},
    ]


def response_for(request, *, choice="same_event", confidence=0.91):
    payload = json.loads(request.content)
    return httpx.Response(200, json={
        "answers": {key: {"type": "choice", "choice": choice,
                          "confidence": confidence,
                          "probabilities": {"same_event": 0.91, "different_event": 0.06,
                                            "insufficient_evidence": 0.03}}
                    for key in payload["questions"]},
        "usage": {"cost": 0.000084, "prompt_tokens": 2000},
    })


@pytest.fixture(autouse=True)
def isolate_settings(monkeypatch):
    monkeypatch.delenv("JEV_STORY_MODE", raising=False)
    monkeypatch.delenv("JEV_STORY_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only-key")


def test_off_never_opens_network_or_changes_articles(monkeypatch):
    from src.jev import review_story_pairs

    rows = articles()
    before = copy.deepcopy(rows)
    mock_http(monkeypatch, lambda _: pytest.fail("default-off reviewer made a paid request"))
    report = review_story_pairs(rows, {"KZ:event": rows})
    assert report == {"mode": "off", "status": "disabled", "decisions": []}
    assert rows == before


def test_shadow_uses_decisions_api_and_keeps_baseline(monkeypatch, caplog):
    from src.jev import review_story_pairs

    monkeypatch.setenv("JEV_STORY_MODE", "shadow")
    rows = articles()
    clusters = {"KZ:first": [rows[0]], "KZ:second": [rows[1]]}
    before = copy.deepcopy((rows, clusters))

    def handle(request):
        assert str(request.url) == "https://openrouter.ai/api/alpha/decisions"
        payload = json.loads(request.content)
        assert payload["model"] == "typesafe/jev-1.13"
        assert "Договор подписан" in json.dumps(payload, ensure_ascii=False)
        assert "baseline" not in json.dumps(payload)
        return response_for(request)

    mock_http(monkeypatch, handle)
    with caplog.at_level("INFO"):
        report = review_story_pairs(rows, clusters)
    assert report["status"] == "ok"
    assert report["cost_usd"] == 0.000084
    assert report["decisions"][0]["choice"] == "same_event"
    assert report["decisions"][0]["baseline_same_cluster"] is False
    assert report["decisions"][0]["article_ids"] == [1, 2]
    assert (rows, clusters) == before
    assert "jev_story_shadow" in caplog.text
    assert "test-only-key" not in caplog.text
    assert "Договор подписан" not in caplog.text


@pytest.mark.parametrize("status", [401, 402, 429, 500])
def test_provider_error_is_bounded_and_never_retried(monkeypatch, status):
    from src.jev import review_story_pairs

    monkeypatch.setenv("JEV_STORY_MODE", "shadow")
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, text="secret provider response")

    mock_http(monkeypatch, handle)
    report = review_story_pairs(articles(), {})
    assert report["status"] == "provider_error"
    assert report["http_status"] == status
    assert report["decisions"] == []
    assert len(requests) == 1
    assert "secret" not in json.dumps(report)


@pytest.mark.parametrize("answer", [
    {"type": "choice", "choice": "merge_everything", "confidence": 0.9},
    {"type": "choice", "choice": "same_event", "confidence": float("nan")},
    {"type": "choice", "choice": "same_event", "confidence": 1.1},
    {"type": "choice", "choice": "same_event"},
    {"type": "score", "choice": "same_event", "confidence": 0.9},
])
def test_invalid_decisions_are_rejected(monkeypatch, answer):
    from src.jev import review_story_pairs

    monkeypatch.setenv("JEV_STORY_MODE", "shadow")

    def handle(request):
        keys = json.loads(request.content)["questions"]
        return httpx.Response(200, content=json.dumps({"answers": dict.fromkeys(keys, answer)}))

    mock_http(monkeypatch, handle)
    report = review_story_pairs(articles(), {})
    assert report["status"] == "invalid_response"
    assert report["decisions"] == []


def test_request_has_hard_pair_and_utf8_bounds(monkeypatch):
    from src.jev import review_story_pairs

    monkeypatch.setenv("JEV_STORY_MODE", "shadow")
    rows = [dict(articles()[0], article_id=i, excerpt="世界" * 2000) for i in range(1, 401)]

    def handle(request):
        payload = json.loads(request.content)
        assert 0 < len(payload["questions"]) <= 20
        assert len(request.content) <= 24000
        assert len(payload["state"]["articles"]) <= 40
        return response_for(request)

    mock_http(monkeypatch, handle)
    report = review_story_pairs(rows, {})
    assert report["status"] == "ok"
    assert 0 < len(report["decisions"]) <= 20


@pytest.mark.parametrize("setting,value,status", [
    ("JEV_STORY_MODE", "apply", "invalid_config"),
    ("OPENROUTER_API_KEY", "", "missing_key"),
    ("JEV_STORY_TIMEOUT_SECONDS", "nan", "invalid_config"),
])
def test_invalid_config_never_calls_provider(monkeypatch, setting, value, status):
    from src.jev import review_story_pairs

    monkeypatch.setenv("JEV_STORY_MODE", "shadow")
    monkeypatch.setenv(setting, value)
    mock_http(monkeypatch, lambda _: pytest.fail("invalid config made a request"))
    report = review_story_pairs(articles(), {})
    assert report["status"] == status
    assert report["decisions"] == []


def test_hourly_thread_path_reviews_after_commit_and_preserves_memberships(monkeypatch):
    import scripts.build_threads as builder

    rows = articles()
    for row in rows:
        row["has_embedding"] = False
    active = []
    saved = []
    reviewed = []

    @contextmanager
    def session():
        active.append(True)
        yield object()
        active.pop()

    monkeypatch.setattr(builder, "get_session", session)
    monkeypatch.setattr(builder, "fetch_articles", lambda *args, **kwargs: rows)
    monkeypatch.setattr(builder, "cluster_pass1_trgm", lambda *args: {"KZ:event": rows})
    monkeypatch.setattr(builder, "upsert_thread", lambda *args, **kwargs: saved.append(args[3]) or 42)

    def review(given, clusters):
        assert not active, "network review holds the database transaction open"
        reviewed.append(given)
        return {"decisions": [{"choice": "different_event"}]}

    monkeypatch.setattr(builder, "review_story_pairs", review, raising=False)
    ids = builder.rebuild_recent_threads(use_llm_dedup=False, use_llm_pair_judge=False)
    assert ids == {42}
    assert saved == [rows]
    assert reviewed == [rows]


def test_blocked_dns_cannot_extend_hourly_pipeline_deadline(monkeypatch, tmp_path):
    from src.jev import review_story_pairs

    monkeypatch.setenv("JEV_STORY_MODE", "shadow")
    monkeypatch.setenv("JEV_STORY_TIMEOUT_SECONDS", "0.4")
    marker = tmp_path / "dns-started"
    original_popen = subprocess.Popen
    children = []
    # Run the real worker in a real process, replacing only OS DNS with a hang.
    code = (
        "import socket,time,runpy,sys,pathlib\n"
        "def blocked(*args,**kwargs):\n"
        f" pathlib.Path({str(marker)!r}).write_text('started')\n"
        " time.sleep(10)\n"
        " raise socket.gaierror('simulated')\n"
        "socket.getaddrinfo=blocked\n"
        "runpy.run_path(sys.argv[1],run_name='__main__')\n"
    )

    def popen(args, **kwargs):
        assert "test-only-key" not in str(args)
        child = original_popen([args[0], "-c", code, args[1]], **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", popen)
    started = time.monotonic()
    report = review_story_pairs(articles(), {})
    assert report["status"] == "timeout"
    assert time.monotonic() - started < 1.5
    assert marker.exists(), "worker did not reach the blocked DNS fixture"
    assert len(children) == 1 and children[0].poll() is not None


def test_country_context_cannot_contaminate_pair_baseline(monkeypatch):
    from src.jev import review_story_pairs

    monkeypatch.setenv("JEV_STORY_MODE", "shadow")
    kz = articles()
    am = [dict(a, country_code="AM") for a in kz]
    rows = am + kz
    clusters = {"AM:first": [am[0]], "AM:second": [am[1]], "KZ:joint": kz}

    def handle(request):
        payload = json.loads(request.content)
        assert len(payload["state"]["articles"]) == 4
        return response_for(request)

    mock_http(monkeypatch, handle)
    report = review_story_pairs(rows, clusters)
    assert report["status"] == "ok"
    observed = {d["country_code"]: d["baseline_same_cluster"] for d in report["decisions"]}
    assert observed == {"AM": False, "KZ": True}
