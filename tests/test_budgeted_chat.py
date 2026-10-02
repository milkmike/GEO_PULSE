from decimal import Decimal

import httpx
import pytest


def test_budget_reserved_before_request_and_price_ceiling_sent(monkeypatch):
    from src.budgeted_chat import BudgetedChat
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        raise httpx.ReadTimeout("unknown provider outcome")
    monkeypatch.setattr(httpx, "post", post)
    client = BudgetedChat("test", Decimal("0.10"))
    with pytest.raises(Exception):
        client.chat("Russia treaty", max_tokens=350)
    with pytest.raises(Exception):
        client.chat("Second call must not run", max_tokens=350)
    assert len(calls) == 1
    assert calls[0]["provider"]["max_price"] == {"prompt": 1, "completion": 2, "request": 0}
    assert calls[0]["provider"]["allow_fallbacks"] is False
    assert calls[0]["reasoning"] == {"enabled": False}
    assert client.requests[0]["error_reason"] == "transport_error"


def test_malformed_provider_usage_is_recorded_without_response_text(monkeypatch):
    from src.budgeted_chat import BudgetedChat
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs: httpx.Response(200,
        request=httpx.Request("POST", url), json={
            "choices": [{"finish_reason": "stop", "message": {"content": "secret source text"}}],
            "usage": ["malformed"]}))
    client = BudgetedChat("test", Decimal("0.10"))
    with pytest.raises(Exception):
        client.chat("Sample")
    assert client.requests[0]["error_reason"] == "invalid_provider_usage"
    assert "secret source text" not in str(client.requests[0])


def test_large_input_rejected_before_network(monkeypatch):
    from src.budgeted_chat import BudgetedChat
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("unbounded request"))
    with pytest.raises(ValueError):
        BudgetedChat("test", Decimal("5")).chat("x" * 32001)


@pytest.mark.parametrize("body", ["я" * 3000, "中" * 3000], ids=["ru", "zh"])
def test_budget_client_accepts_real_multilingual_analysis_prompt(monkeypatch, body):
    from src.budgeted_chat import BudgetedChat
    from src.pipeline.sentiment import analyze_sentiment
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": '{"is_relevant":false}'}}], "usage": {"cost": .0003}})
    monkeypatch.setattr(httpx, "post", post)
    result = analyze_sentiment("Russia treaty", body, "Test", "KZ",
        chat_fn=BudgetedChat("test", Decimal(".1")).chat)
    assert result["is_relevant"] is False
    assert len(calls) == 1


def test_provider_cost_recorded_without_releasing_reserved_allowance(monkeypatch):
    from src.budgeted_chat import BudgetedChat
    monkeypatch.setattr(httpx, "post", lambda *a, **k: httpx.Response(200,
        request=httpx.Request("POST", "https://openrouter.ai"), json={
            "choices": [{"message": {"content": "{}"}}], "usage": {"cost": .0003}}))
    client = BudgetedChat("test", Decimal("0.10"))
    assert client.chat("Sample")[0] == "{}"
    assert client.actual_cost == Decimal("0.0003")
    assert client.reserved == Decimal("0.10")
    with pytest.raises(Exception):
        client.chat("Cannot reuse tiny charge for unbounded retries")


def test_short_production_analysis_disables_reasoning_and_records_cost(monkeypatch):
    from src import llm
    calls, logs = [], []
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": "{}"}}], "usage": {"cost": .00007}})
    monkeypatch.setattr(llm.httpx, "post", post)
    monkeypatch.setattr(llm, "track_api_call", lambda **kwargs: logs.append(kwargs))
    assert llm._call_openrouter("deepseek/deepseek-v4-flash", "Test", 350, None, "analyze.py") == "{}"
    assert calls[0]["reasoning"] == {"enabled": False}
    assert logs[0]["cost"] == .00007
