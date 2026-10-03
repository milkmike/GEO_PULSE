"""Provider transport, grounding and cost recovery; no network or paid calls."""
import json
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from src import decision_model as dm


def configure(monkeypatch, provider):
    monkeypatch.setattr(dm, 'PROVIDER', provider)
    for name, value in zip(('MODEL', 'SERVICE', 'KEY_ENV', 'URL', 'ENDPOINT'), dm._PROVIDERS[provider]):
        monkeypatch.setattr(dm, name, value)


def payload():
    return {'model': dm.MODEL, 'state': {'articles': {'article_1': {
        'title': 'Молдова обсуждает поставки газа', 'excerpt': 'Решение пока не принято.'}}},
        'questions': {'article_1_country': {'type': 'choice', 'instructions':
            "Use only state.articles['article_1']; where does the action happen?",
            'criteria': {'MD': 'Moldova', 'US': 'United States', 'unknown': 'Unclear'}}}}


def answer(choice='MD', **changes):
    return {'type': 'choice', 'choice': choice, 'confidence': .95,
            'confidence_kind': 'self_reported', 'evidence': [
                {'article_id': 'article_1', 'quote': 'Молдова обсуждает поставки газа'}]} | changes


def envelope(content=None, **changes):
    content = content if content is not None else json.dumps({'answers': {'article_1_country': answer()}})
    return {'model': dm.MODEL, 'choices': [{'finish_reason': 'stop', 'message': {'content': content}}],
            'usage': {'prompt_tokens': 500, 'completion_tokens': 100, 'cost': .001}} | changes


def test_chat_uses_price_caps_and_no_reasoning_or_provider_fallback(monkeypatch):
    configure(monkeypatch, 'openrouter')
    body = dm.build_request(payload())
    assert body['model'] == 'deepseek/deepseek-v4-flash'
    assert body['provider'] == {'max_price': {'prompt': 1, 'completion': 2, 'request': 0},
                                'allow_fallbacks': False, 'require_parameters': True}
    assert body['reasoning'] == {'enabled': False}
    assert body['response_format'] == {'type': 'json_object'}
    assert body['max_tokens'] <= 4000
    assert 'tools' not in body
    assert 'do not output probabilities' in body['messages'][0]['content']
    assert Decimal(dm.MAX_CHAT_BYTES * 2 + 2000) / 1000000 + Decimal(body['max_tokens'] * 2) / 1000000 < dm.RESERVATION_USD


def test_native_provider_uses_own_key_and_fixed_destination(monkeypatch):
    configure(monkeypatch, 'deepseek')
    body = dm.build_request(payload())
    assert dm.KEY_ENV == 'DEEPSEEK_API_KEY' and dm.URL == 'https://api.deepseek.com/chat/completions'
    assert body['model'] == 'deepseek-flash' and body['thinking'] == {'type': 'disabled'}
    assert 'provider' not in body and 'reasoning' not in body
    result = dm.parse_chat_response(envelope(), payload())
    assert result['usage']['cost'] is None and result['usage']['cost_kind'] == 'unknown_native_bill'


def test_gemini_reserve_is_separate_and_includes_all_generated_tokens(monkeypatch):
    configure(monkeypatch, 'gemini')
    body = dm.build_request(payload())
    assert dm.KEY_ENV == 'GEMINI_API_KEY' and 'generativelanguage.googleapis.com' in dm.URL
    assert body['generationConfig']['responseMimeType'] == 'application/json'
    data = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': json.dumps(
            {'answers': {'article_1_country': answer()}})}]}}],
            'usageMetadata': {'promptTokenCount': 100, 'candidatesTokenCount': 200, 'thoughtsTokenCount': 300}}
    result = dm.parse_chat_response(data, payload())
    assert result['usage']['completion_tokens'] == 500
    assert result['usage']['cost'] is None


def test_criteria_are_deduplicated_for_world_country_catalog(monkeypatch):
    configure(monkeypatch, 'openrouter')
    data = payload()
    data['questions']['another_question'] = dict(data['questions']['article_1_country'])
    prompt = dm.build_request(data)['messages'][0]['content']
    assert prompt.count('United States') == 1
    assert 'publisher geography' in prompt


def test_chat_scores_never_become_probabilities(monkeypatch):
    configure(monkeypatch, 'openrouter')
    parsed = dm.parse_chat_response(envelope(), payload())
    result = parsed['answers']['article_1_country']
    assert result['confidence_kind'] == 'self_reported' and result['probabilities'] == {}
    assert dm.supported_choice(result, payload()['questions']['article_1_country']['criteria'])


@pytest.mark.parametrize('bad', [answer(choice='FR'), answer(confidence=True), answer(confidence=float('nan')),
    answer(probabilities={'MD': 1}), answer(evidence=[]), answer(confidence_kind='native'),
    answer(evidence=[{'article_id': 'article_2', 'quote': 'Молдова обсуждает поставки газа'}]),
    answer(evidence=[{'article_id': 'article_1', 'quote': 'United States signed the agreement'}])])
def test_invalid_or_fabricated_evidence_rejects_whole_batch(monkeypatch, bad):
    configure(monkeypatch, 'openrouter')
    with pytest.raises(ValueError):
        dm.parse_chat_response(envelope(json.dumps({'answers': {'article_1_country': bad}})), payload())


def test_abstention_does_not_require_a_fabricated_quote():
    decision = answer(choice='unknown', evidence=[])
    assert dm.parse_choice(decision, {'MD', 'unknown'})['choice'] == 'unknown'


def test_duplicate_json_keys_and_missing_questions_are_rejected(monkeypatch):
    configure(monkeypatch, 'openrouter')
    for text in ('{"answers":{},"answers":{}}', '{"answers":{}}', '```json\n{}\n```'):
        with pytest.raises(ValueError):
            dm.parse_chat_response(envelope(text), payload())


def test_native_probability_validation_stays_strict():
    native = {'type': 'choice', 'choice': 'MD', 'confidence': .9, 'probabilities': {'MD': .9, 'unknown': .1}}
    assert dm.supported_choice(native, {'MD', 'unknown'})
    native['probabilities']['MD'] = .3
    with pytest.raises(ValueError, match='probabilities'):
        dm.parse_choice(native, {'MD', 'unknown'})


class Stream:
    status_code = 200
    def __init__(self, data): self.data = data
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def iter_bytes(self): yield json.dumps(self.data).encode()


def test_invalid_content_keeps_known_bill_for_campaign_halt(monkeypatch):
    configure(monkeypatch, 'openrouter')
    data = envelope('invalid JSON', usage={'cost': .20, 'prompt_tokens': 100})
    monkeypatch.setattr(dm.httpx, 'stream', lambda *a, **k: Stream(data))
    result = dm.http_request(payload(), 'dummy-key', 1)
    assert result['status'] == 'invalid_response'
    assert result['usage']['cost'] == .20


def test_provider_error_never_reads_body_or_retries(monkeypatch):
    configure(monkeypatch, 'openrouter')
    calls = []
    class Failure(Stream):
        status_code = 403
        def iter_bytes(self): pytest.fail('provider error body must not be read')
    def stream(*args, **kwargs):
        calls.append(kwargs)
        return Failure({})
    monkeypatch.setattr(dm.httpx, 'stream', stream)
    assert dm.http_request(payload(), 'secret-do-not-print', 1) == {'status': 'provider_error', 'http_status': 403}
    assert len(calls) == 1 and calls[0]['follow_redirects'] is False


def test_child_uses_stdin_for_key_and_enforces_total_deadline(monkeypatch):
    configure(monkeypatch, 'openrouter')
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=b'{"status":"timeout"}')
    monkeypatch.setattr(dm.subprocess, 'run', run)
    assert dm.request(payload(), 'secret-example', 2)['status'] == 'timeout'
    args, kwargs = calls[0]
    assert all('secret-example' not in arg for arg in args)
    assert json.loads(kwargs['input'])['api_key'] == 'secret-example'
    assert kwargs['timeout'] == 2


def test_oversized_request_fails_before_network_or_subprocess(monkeypatch):
    configure(monkeypatch, 'openrouter')
    monkeypatch.setattr(dm.subprocess, 'run', lambda *a, **k: pytest.fail('oversized request sent'))
    data = payload()
    data['state']['articles']['article_1']['excerpt'] = 'x' * 25000
    with pytest.raises(ValueError, match='request'):
        dm.request(data, 'test', 1)


def test_native_preflight_does_not_reuse_openrouter_key(monkeypatch):
    configure(monkeypatch, 'deepseek')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'unrelated-secret')
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    monkeypatch.setattr(dm.httpx, 'get', lambda *a, **k: pytest.fail('wrong key sent'))
    with pytest.raises(ValueError, match='missing_decision_key'):
        dm.check_tariff()


def test_preflight_accepts_only_an_enforced_eligible_price_route(monkeypatch):
    configure(monkeypatch, 'openrouter')
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test')
    data = {'data': {'id': dm.MODEL, 'endpoints': [{'pricing': {'prompt': '.000001', 'completion': '.000002', 'request': '0'}}]}}
    class Response:
        def raise_for_status(self): pass
        def json(self): return data
    monkeypatch.setattr(dm.httpx, 'get', lambda *a, **k: Response())
    dm.check_tariff()
    data['data']['endpoints'][0]['pricing']['completion'] = '.00001'
    with pytest.raises(ValueError, match='tariff'):
        dm.check_tariff()
