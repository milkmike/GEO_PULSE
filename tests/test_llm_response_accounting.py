"""Paid empty completions must neither look successful nor abort fallback."""
import json
from decimal import Decimal

import httpx
import pytest

from src import llm, embeddings
from src.api_tracker import calculate_cost


@pytest.fixture
def provider(monkeypatch):
    records, payloads, cache = [], [], {}
    monkeypatch.setattr(llm, 'OPENROUTER_API_KEY', 'test')
    monkeypatch.setattr(llm, 'OLLAMA_URL', '')
    monkeypatch.setattr(llm, '_openrouter_blocked_until', 0)
    monkeypatch.setattr(llm, '_empty_response_blocked_until', {}, raising=False)
    monkeypatch.setattr(llm, 'track_api_call', lambda **kw: records.append(kw))
    monkeypatch.setattr(llm, '_cache_get', cache.get)
    monkeypatch.setattr(llm, '_cache_set', lambda k, v, ttl: cache.update({k: v}))
    clock = [100.0]
    monkeypatch.setattr(llm.time, 'monotonic', lambda: clock[0])

    def install(first):
        def post(url, **kw):
            payloads.append(kw['json'])
            data = first if kw['json']['model'] == 'broken' else {
                'choices': [{'message': {'content': '  Useful brief  '}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'cost': .001},
            }
            return httpx.Response(200, json=data, request=httpx.Request('POST', url))
        monkeypatch.setattr(llm.httpx, 'post', post)
    return install, records, payloads, cache, clock


@pytest.mark.parametrize('choice', [
    {'message': {'content': None}, 'finish_reason': 'length'},
    {'message': {'content': '  '}, 'finish_reason': 'stop'},
    {'message': {}, 'finish_reason': 'stop'},
    None,
])
def test_paid_empty_completion_falls_back_and_records_cost_once(provider, choice):
    install, records, payloads, cache, _ = provider
    install({'choices': [choice] if choice else [], 'usage': {
        'prompt_tokens': 200, 'completion_tokens': 2000, 'cost': .003,
        'completion_tokens_details': {'reasoning_tokens': 2000},
    }})
    assert llm.chat('test', models=['broken', 'working'], cache_ttl=60) == ('Useful brief', 'working')
    assert len(records) == 2
    assert records[0]['status'] == 'error'
    assert records[0]['cost'] == .003
    assert records[0]['tokens_out'] == 2000
    assert 'empty_content' in records[0]['error']
    assert 'reasoning_tokens=2000' in records[0]['error']
    assert records[1]['status'] == 'ok'
    assert json.loads(next(iter(cache.values())))['model'] == 'working'


def test_empty_response_cooldown_skips_paid_repeats_then_recovers(provider):
    install, records, payloads, _, clock = provider
    install({'choices': [{'message': {'content': None}}], 'usage': {'cost': .003}})
    for prompt in ('first', 'second'):
        assert llm.chat(prompt, models=['broken', 'working'], script='briefs.py')[1] == 'working'
    assert [p['model'] for p in payloads] == ['broken', 'working', 'working']
    clock[0] += 7201
    llm.chat('third', models=['broken', 'working'], script='briefs.py')
    assert [p['model'] for p in payloads][-2:] == ['broken', 'working']


def test_all_empty_responses_raise_llm_error_without_caching(provider):
    install, records, _, cache, _ = provider
    install({'choices': [{'message': {'content': None}}], 'usage': {'cost': .003}})
    with pytest.raises(llm.LLMError):
        llm.chat('test', models=['broken'], cache_ttl=60)
    assert not cache
    assert len(records) == 1


@pytest.mark.parametrize('model', ['qwen/qwen3.7-plus', 'deepseek/deepseek-v4-flash', 'qwen/qwen3.6-flash'])
def test_briefs_reserve_output_budget_for_visible_text(provider, model):
    install, _, payloads, _, _ = provider
    install({})
    llm.chat('brief', script='briefs.py', models=[model])
    assert payloads[0]['reasoning'] == {'enabled': False}


@pytest.mark.parametrize('batch', [False, True])
def test_embedding_provider_charge_is_preserved(monkeypatch, batch):
    records = []
    monkeypatch.setattr(embeddings, '_get_api_config', lambda: (
        'https://openrouter.ai/api/v1/embeddings', {}, 'openai/text-embedding-3-small', 2))
    monkeypatch.setattr(embeddings, '_add_proxy_fields', lambda p, m: p)
    monkeypatch.setattr(embeddings, 'track_api_call', lambda **kw: records.append(kw))
    monkeypatch.setattr(embeddings.time, 'sleep', lambda _: None)
    monkeypatch.setattr(embeddings.httpx, 'post', lambda url, **kw: httpx.Response(
        200, request=httpx.Request('POST', url), json={
            'data': [{'index': 0, 'embedding': [1., 0.]}],
            'usage': {'prompt_tokens': 100, 'cost': .000002},
        }))
    result = embeddings.generate_embeddings_batch(['text']) if batch else embeddings.generate_embedding('text')
    assert result == ([[1., 0.]] if batch else [1., 0.])
    assert records[0]['cost'] == .000002


def test_openrouter_embedding_fallback_estimate_is_not_zero():
    assert calculate_cost('openrouter', 'openai/text-embedding-3-small', 1000000, 0) == Decimal('.02')
