from decimal import Decimal

import pytest

from src import jev_evidence as verify
from src import source_segments


def article():
    return {'id': 7, 'title': 'Serbia and Russia expand research cooperation.',
            'excerpt': 'The Serbian university signed an agreement with a Russian university. No visa rules changed.'}


def proposed():
    return {'relevant': True, 'headline_ru': 'Сербия и Россия расширяют научное сотрудничество',
            'summary_ru': 'Сербский и российский университеты подписали соглашение.',
            'russia_explanation_ru': 'Соглашение с российским университетом.',
            'russia_evidence_quote': article()['title'], 'kind': 'cooperation',
            'countries': [{'code': 'RS', 'evidence_quote': article()['title']}],
            'positions': [], 'changes': [{'category': 'travel', 'change_ru': 'Россиянам отменили визы.',
                                        'evidence_quote': 'No visa rules changed.'}]}


def choice(label, labels):
    return {'type': 'choice', 'choice': label, 'confidence': .96,
            'probabilities': {key: .96 if key == label else .04 / (len(labels)-1) for key in labels}}


@pytest.fixture
def paid(monkeypatch):
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-only')
    monkeypatch.setattr(verify, 'check_tariff', lambda: None)
    monkeypatch.setattr(verify, 'track_api_call', lambda **kw: None)
    reservations, settlements, calls = [], [], []
    def reserve(*args, **kwargs):
        reservations.append((args, kwargs))
        return len(reservations)
    monkeypatch.setattr(verify.budget, 'reserve_request', reserve)
    monkeypatch.setattr(verify.budget, 'finish_request', lambda *args: settlements.append(args))
    def request(payload, key, timeout):
        calls.append(payload)
        if payload['state']['stage'] == 'select':
            lines = payload['state']['source']
            title = next(key for key, row in lines.items() if row['source_part'] == 'title')
            body = next(key for key, row in lines.items() if 'signed' in row['quote'])
            answers = {key: choice(body if key in ('summary', 'explanation') else title,
                                   question['criteria']) for key, question in payload['questions'].items()}
        else:
            answers = {key: choice('unsupported' if key == 'change_0' else 'supported', question['criteria'])
                       for key, question in payload['questions'].items()}
        return {'status': 'ok', 'data': {'answers': answers, 'usage': {'cost': .00001}}}
    monkeypatch.setattr(verify, '_request', request)
    return reservations, settlements, calls


def test_two_passes_copy_exact_fragments_and_prune_invented_effect(paid):
    result = verify.verify_annotation(article(), proposed(), campaign='test', budget_usd=Decimal('1'))
    assert result is not None and result['changes'] == []
    assert result['countries'][0]['code'] == 'RS'
    assert source_segments.validate_review(article(), result)
    reservations, settlements, calls = paid
    assert len(calls) == len(reservations) == len(settlements) == 2
    assert all(row[1]['reservation_usd'] == verify.RESERVATION_USD for row in reservations)
    assert all(payload['model'] == 'typesafe/jev-1.13' for payload in calls)
    assert 'russia_evidence_quote' not in calls[0]['state']['proposed']
    assert 'selected fragment' in calls[1]['questions']['headline']['instructions']
    assert calls[1]['state']['evidence']['country_0']['quote'] == article()['title']


def test_none_country_abstains_without_second_paid_call(paid, monkeypatch):
    reservations, settlements, calls = paid
    original = verify._request
    def request(*args):
        response = original(*args)
        question = args[0]['questions']['country_0']
        response['data']['answers']['country_0'] = choice('none', question['criteria'])
        return response
    monkeypatch.setattr(verify, '_request', request)
    assert verify.verify_annotation(article(), proposed(), campaign='test', budget_usd=Decimal('1')) is None
    assert len(calls) == 1 and settlements[0][-1] == 'ok'


def test_unknown_fragment_rejected_but_known_cost_is_settled(paid, monkeypatch):
    original = verify._request
    def request(*args):
        response = original(*args)
        response['data']['answers']['headline']['choice'] = 'invented'
        return response
    monkeypatch.setattr(verify, '_request', request)
    assert verify.verify_annotation(article(), proposed(), campaign='test', budget_usd=Decimal('1')) is None
    assert paid[1] == [(1, .00001, 'invalid_response')]


def test_provider_error_has_no_paid_retry_or_fallback(paid, monkeypatch):
    calls = []
    monkeypatch.setattr(verify, '_request', lambda *args: calls.append(args) or
                        {'status': 'provider_error', 'http_status': 403})
    assert verify.verify_annotation(article(), proposed(), campaign='test', budget_usd=Decimal('1')) is None
    assert len(calls) == 1 and paid[1] == [(1, None, 'provider_error')]


def test_missing_key_budget_or_tariff_never_sends_paid_request(paid, monkeypatch):
    monkeypatch.delenv('OPENROUTER_API_KEY')
    assert verify.verify_annotation(article(), proposed(), campaign='test', budget_usd=Decimal('1')) is None
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-only')
    assert verify.verify_annotation(article(), proposed(), campaign='test', budget_usd=Decimal('0')) is None
    monkeypatch.setattr(verify, 'check_tariff', lambda: (_ for _ in ()).throw(ValueError('tariff changed')))
    assert verify.verify_annotation(article(), proposed(), campaign='test', budget_usd=Decimal('1')) is None
    assert paid[0] == paid[1] == paid[2] == []


def test_self_reported_scores_cannot_impersonate_native_jev(paid, monkeypatch):
    original = verify._request
    def request(*args):
        response = original(*args)
        response['data']['answers']['headline']['confidence_kind'] = 'self_reported'
        return response
    monkeypatch.setattr(verify, '_request', request)
    assert verify.verify_annotation(article(), proposed(), campaign='test', budget_usd=Decimal('1')) is None


def test_wrong_country_and_unsupported_summary_are_removed(paid, monkeypatch):
    value = proposed()
    value['countries'].append({'code': 'US', 'evidence_quote': article()['title']})
    original = verify._request
    def request(*args):
        response = original(*args)
        if args[0]['state']['stage'] == 'verify':
            for key in ('country_1', 'summary'):
                response['data']['answers'][key] = choice('unsupported', args[0]['questions'][key]['criteria'])
        return response
    monkeypatch.setattr(verify, '_request', request)
    result = verify.verify_annotation(article(), value, campaign='test', budget_usd=Decimal('1'))
    assert result is not None and result['summary_ru'] == ''
    assert [c['code'] for c in result['countries']] == ['RS']
    assert 'country_US' not in result['evidence_review']['quotes']


def test_country_selection_prefers_explicit_country_over_company_in_title():
    source = article() | {'title': 'Srbijagas CEO says gas deal with Russia extended.',
                          'excerpt': 'Gazprom extended its gas agreement with Serbia.'}
    payload = verify.selection_payload(source, proposed())
    country = payload['questions']['country_0']['criteria']
    assert 't000000' not in country and 'e000000' in country and 'none' in country
    assert 't000000' in payload['questions']['headline']['criteria']
