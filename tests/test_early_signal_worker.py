"""Paid worker contracts: persistent reservations precede every request."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import re

import pytest

from src import early_signal_worker as worker
from src import early_signals
from src import decision_model
from tests.test_early_signals import article, response
from tests.test_signal_hypotheses import article as hypothesis_article, dossier as make_draft

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def setup_screen(monkeypatch):
    events=[]
    monkeypatch.setenv('OPENROUTER_API_KEY','test')
    monkeypatch.setenv(decision_model.KEY_ENV,'test')
    monkeypatch.setattr(worker.store,'load_screenings',lambda keys:{})
    monkeypatch.setattr(worker.store,'save_screenings',lambda rows:events.append(('save',rows)))
    monkeypatch.setattr(worker.budget,'get_attempted_pair_keys',lambda campaign:set())
    monkeypatch.setattr(worker.budget,'get_budget',lambda campaign:2)
    def reserve(*a,**k):
        assert all(re.fullmatch('[0-9a-f]{64}', key) for key in k['pair_keys'])
        events.append(('reserve',k))
        return 'call'
    monkeypatch.setattr(worker.budget,'reserve_request',reserve)
    monkeypatch.setattr(worker.budget,'finish_request',lambda *a:events.append(('finish',a)))
    monkeypatch.setattr(worker,'check_tariff',lambda:events.append(('tariff',)))
    monkeypatch.setattr(worker,'track_api_call',lambda **k:events.append(('track',k)))
    def request(payload,*a):
        events.append(('request',))
        return {'status':'ok','data':response(payload)}
    monkeypatch.setattr(worker,'_request',request)
    return events


def test_disabled_screening_never_reads_sources_or_requests(monkeypatch):
    monkeypatch.setattr(worker,'load_articles',lambda **k:pytest.fail('disabled source read'))
    assert worker.run_screening_cycle()['status']=='disabled'


def test_missing_selected_key_never_reads_sources_or_spends(monkeypatch):
    monkeypatch.delenv(decision_model.KEY_ENV, raising=False)
    monkeypatch.setattr(worker, 'load_articles', lambda **k: pytest.fail('missing key source read'))
    monkeypatch.setattr(worker.budget, 'reserve_request', lambda *a, **k: pytest.fail('missing key spend'))
    assert worker.run_screening_cycle(budget_usd=Decimal('2'))['status'] == 'missing_key'


def test_screening_reserves_persistent_cap_then_saves_and_settles(monkeypatch):
    events=setup_screen(monkeypatch)
    stats=worker.run_screening_cycle(articles=[article()],as_of=NOW,budget_usd=Decimal('2'))
    assert stats['screened']==1 and stats['calls']==1
    names=[x[0] for x in events]
    assert names.index('reserve')<names.index('request')<names.index('save')<names.index('finish')
    assert events[names.index('finish')][1]==('call',.0003,'ok')
    assert events[names.index('reserve')][1]['reservation_usd']==Decimal('.10')


def test_ambiguous_failure_keeps_hold_and_has_no_retry(monkeypatch):
    events=setup_screen(monkeypatch)
    def fail(*a):
        events.append(('request',))
        raise TimeoutError('do not print credential detail')
    monkeypatch.setattr(worker,'_request',fail)
    stats=worker.run_screening_cycle(articles=[article()],as_of=NOW,budget_usd=Decimal('2'))
    assert stats['status']=='error' and stats['calls']==1
    assert stats['error']=='transport_error'
    assert ('finish',('call',None,'error')) in events
    assert not any(x[0]=='save' for x in events)


def test_cached_or_attempted_sources_do_not_spend(monkeypatch):
    events=setup_screen(monkeypatch)
    monkeypatch.setattr(worker.budget,'get_attempted_pair_keys',lambda campaign:{early_signals.source_key(article())})
    stats=worker.run_screening_cycle(articles=[article()],as_of=NOW,budget_usd=Decimal('2'))
    assert stats['calls']==0 and not any(x[0]=='request' for x in events)


def test_malformed_provider_decision_has_specific_reason(monkeypatch):
    events=setup_screen(monkeypatch)
    monkeypatch.setattr(worker,'_request',lambda *a:{'status':'ok','data':{'answers':{},'usage':{'cost':.0003}}})
    stats=worker.run_screening_cycle(articles=[article()],as_of=NOW,budget_usd=Decimal('2'))
    assert stats['error']=='unexpected questions' and stats['status']=='error'
    assert ('finish',('call',.0003,'invalid_response')) in events


def test_writer_requires_prepaid_reservation_and_stop_reason(monkeypatch):
    events=setup_screen(monkeypatch)
    evidence=[hypothesis_article()]
    context={'articles':evidence,'as_of':NOW.isoformat()}
    draft=make_draft()
    class Result:
        def raise_for_status(self): pass
        def json(self): return {'choices':[{'finish_reason':'length','message':{'content':json.dumps(draft)}}], 'usage':{'cost':.005}}
    monkeypatch.setattr(worker.httpx,'post',lambda *a,**k:events.append(('writer-request',k['json'])) or Result())
    with pytest.raises(ValueError,match='incomplete_response'):
        worker.write_draft(context,budget_usd=Decimal('2'))
    names=[x[0] for x in events]
    assert names.index('reserve')<names.index('writer-request')
    payload=events[names.index('writer-request')][1]
    assert payload['max_tokens']==2000 and payload['provider']['allow_fallbacks'] is False
    assert ('finish',('call',.005,'invalid_response')) in events


def test_invalid_bounds_fail_before_any_request():
    with pytest.raises(ValueError): worker.run_screening_cycle(budget_usd=Decimal('21'))
    with pytest.raises(ValueError): worker.run_screening_cycle(budget_usd=Decimal('2'),max_calls=99)


def test_disabled_locked_cycle_never_opens_database(monkeypatch):
    monkeypatch.setattr(worker,'get_session',lambda:pytest.fail('disabled lock DB'))
    assert worker.locked_screening_cycle()['status']=='disabled'


def test_writer_malformed_response_is_not_transport_error(monkeypatch):
    events=setup_screen(monkeypatch)
    class Result:
        def raise_for_status(self): pass
        def json(self): return {'choices':[], 'usage':{'cost':.005}}
    monkeypatch.setattr(worker.httpx,'post',lambda *a,**k:Result())
    with pytest.raises(ValueError,match='invalid_response'):
        worker.write_draft({'articles':[hypothesis_article()],'as_of':NOW.isoformat()},budget_usd=Decimal('2'))
    assert ('finish',('call',.005,'invalid_response')) in events


def test_already_screened_top_rows_do_not_starve_unseen_local_reports(monkeypatch):
    events=setup_screen(monkeypatch)
    seen=[article(i,source_id=1,publisher='Gazette') for i in range(1,81)]
    unseen=article(81,source_id=1,publisher='Gazette')
    monkeypatch.setattr(worker.budget,'get_attempted_pair_keys',lambda campaign:{early_signals.source_key(a) for a in seen})
    stats=worker.run_screening_cycle(articles=seen+[unseen],as_of=NOW,budget_usd=Decimal('2'))
    assert stats['screened']==1
    assert next(e[1] for e in events if e[0]=='save')[0]['article']['id']==81


def test_imported_cached_rows_do_not_starve_unseen_local_reports(monkeypatch):
    events=setup_screen(monkeypatch)
    seen=[article(i,source_id=1,publisher='Gazette') for i in range(1,81)]
    unseen=article(81,source_id=1,publisher='Gazette')
    cached={early_signals.source_key(a): {'article': a} for a in seen}
    monkeypatch.setattr(worker.store,'load_screenings',lambda keys:{key:cached[key] for key in keys if key in cached})
    stats=worker.run_screening_cycle(articles=seen+[unseen],as_of=NOW,budget_usd=Decimal('2'))
    assert stats['screened']==1 and stats['cached']==80
    assert next(e[1] for e in events if e[0]=='save')[0]['article']['id']==81


def test_invalid_cost_reaches_persistent_halt_guard(monkeypatch):
    events=setup_screen(monkeypatch)
    monkeypatch.setattr(worker,'_request',lambda *a:{'status':'ok','data':{'answers':{},'usage':{'cost':-1}}})
    stats=worker.run_screening_cycle(articles=[article()],as_of=NOW,budget_usd=Decimal('2'))
    assert stats['error']=='invalid cost'
    assert ('finish',('call',-1,'invalid_response')) in events
    assert not any(x[0]=='save' for x in events)


def test_chat_transport_failure_keeps_known_overage_for_halt(monkeypatch):
    events=setup_screen(monkeypatch)
    monkeypatch.setattr(worker,'_request',lambda *a:{'status':'invalid_response','usage':{'cost':.12}})
    stats=worker.run_screening_cycle(articles=[article()],as_of=NOW,budget_usd=Decimal('2'))
    assert stats['status']=='error'
    assert ('finish',('call',.12,'error')) in events
    assert not any(x[0]=='save' for x in events)
