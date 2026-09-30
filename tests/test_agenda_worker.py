from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock
import pytest


def test_disabled_campaign_never_calls_provider(monkeypatch):
    from src import agenda_worker as worker
    monkeypatch.setattr(worker.store,'record_run',Mock())
    monkeypatch.setattr(worker.store,'load_articles',lambda: pytest.fail('disabled worker scanned articles'))
    monkeypatch.setattr(worker,'_request',lambda *a:pytest.fail('disabled worker called provider'))
    assert worker.run_cycle(budget_usd=Decimal('0'))['status']=='disabled'


def test_provider_price_guard_rejects_changed_or_unknown_tariff():
    from src.agenda_worker import validate_tariff
    with pytest.raises(ValueError):
        validate_tariff({'data':[]})
    with pytest.raises(ValueError):
        validate_tariff({'data':[{'id':'typesafe/jev-1.13','pricing':{'prompt':'.01','completion':'0'}}]})
    validate_tariff({'data':[{'id':'typesafe/jev-1.13','pricing':{'prompt':'.000000042','completion':'0','request':'0'}}]})


@pytest.fixture
def harness(monkeypatch):
    from src import agenda_worker as worker
    now=datetime.now(timezone.utc)
    articles=[{'id':i,'source_id':i,'country_code':'RU','title':f'Самолет Flydubai посадили пилоты {i}',
               'excerpt':'Пилоты посадили самолет после инцидента в кабине.',
               'published_at':now,'collected_at':now,'content_hash':str(i)} for i in (1,2)]
    group={'anchor':articles[0],'candidates':articles[1:]}
    page=Mock(return_value={'groups':[group],'next_cursor':22,'group_cursors':[{'cursor':5,'next_cursor':6}]})
    monkeypatch.setattr(worker.store,'load_articles',lambda:articles)
    monkeypatch.setattr(worker.store,'load_groups',lambda:[])
    monkeypatch.setattr(worker.store,'get_discovery_state',lambda:{'cursor':5,'known_pair_keys':set(),'cached_pair_keys':set()})
    monkeypatch.setattr(worker.budget,'get_attempted_pair_keys',lambda campaign:set())
    monkeypatch.setattr(worker,'candidate_group_page',page)
    monkeypatch.setattr(worker.store,'get_cached_decisions',lambda keys:{})
    monkeypatch.setattr(worker,'check_tariff',Mock())
    reserve=Mock(return_value='reservation')
    monkeypatch.setattr(worker.budget,'reserve_request',reserve)
    settled=Mock()
    monkeypatch.setattr(worker.budget,'finish_request',settled)
    monkeypatch.setattr(worker.budget,'get_budget',lambda *a:2.9)
    saved=Mock()
    attached=Mock(return_value=7)
    monkeypatch.setattr(worker.store,'save_decisions',saved)
    monkeypatch.setattr(worker.store,'attach_decisions',attached)
    monkeypatch.setattr(worker.store,'record_run',Mock())
    monkeypatch.setattr(worker,'track_api_call',Mock())
    monkeypatch.setenv('OPENROUTER_API_KEY','test')
    return worker,articles,page,reserve,settled,saved,attached


def response(payload):
    return {'status':'ok','data':{'answers':{key:{'type':'choice','choice':'same_event','confidence':.99,
       'probabilities':{'same_event':.97,'development':.01,'unrelated':.01,'uncertain':.01}}
       for key in payload['questions']},'usage':{'cost':.00005,'input_tokens':1000}}}


def test_transport_failure_retains_reservation_and_never_attaches(monkeypatch,harness):
    worker,articles,page,reserve,settled,saved,attached=harness
    monkeypatch.setattr(worker,'_request',lambda *a:{'status':'timeout'})
    assert worker.run_cycle(budget_usd=Decimal('3'))['status']=='error'
    settled.assert_called_once_with('reservation',None,'timeout')
    saved.assert_not_called()
    attached.assert_not_called()
    assert reserve.call_args.kwargs['pair_keys']==[worker.pair_cache_key(*articles)]


def test_success_commits_reservation_before_network_and_persists_membership(monkeypatch,harness):
    worker,articles,page,reserve,settled,saved,attached=harness
    def request(payload,*args):
        reserve.assert_called_once()
        saved.assert_not_called()
        return response(payload)
    monkeypatch.setattr(worker,'_request',request)
    result=worker.run_cycle(budget_usd=Decimal('3'))
    assert result['status']=='ok' and result['accepted']==1 and result['calls']==1
    assert result['discovery_cursor']==22
    settled.assert_called_once_with('reservation',.00005,'ok')
    saved.assert_called_once()
    attached.assert_called_once()


def test_exhausted_campaign_makes_no_paid_call(monkeypatch,harness):
    worker,articles,page,reserve,settled,saved,attached=harness
    reserve.return_value=None
    monkeypatch.setattr(worker,'_request',lambda *a:pytest.fail('exhausted campaign made request'))
    assert worker.run_cycle(budget_usd=Decimal('3'))['status']=='budget_exhausted'
    settled.assert_not_called()


def test_crashed_attempt_excluded_but_successful_cached_decision_can_recover(monkeypatch,harness):
    worker,articles,page,reserve,settled,saved,attached=harness
    monkeypatch.setattr(worker.store,'get_discovery_state',lambda:{'cursor':9,'known_pair_keys':{'rejected'},'cached_pair_keys':{'successful','rejected'}})
    monkeypatch.setattr(worker.budget,'get_attempted_pair_keys',lambda campaign:{'crashed','successful','rejected'})
    page.return_value={'groups':[],'next_cursor':10,'group_cursors':[]}
    worker.run_cycle(budget_usd=Decimal('3'))
    assert page.call_args.kwargs['known_pair_keys']=={'crashed','rejected'}
    assert page.call_args.kwargs['cursor']==9
    reserve.assert_not_called()


def test_cached_success_attaches_without_new_charge(monkeypatch,harness):
    worker,articles,page,reserve,settled,saved,attached=harness
    payload,pairs=worker.prepare_pair_payload(articles[0],articles[1:])
    decisions,_=worker.parse_pair_response(response(payload)['data'],pairs)
    monkeypatch.setattr(worker.store,'get_cached_decisions',lambda keys:{decisions[0]['cache_key']:decisions[0]})
    monkeypatch.setattr(worker,'_request',lambda *a:pytest.fail('cached decision was re-billed'))
    result=worker.run_cycle(budget_usd=Decimal('3'))
    assert result['accepted']==1 and result['calls']==0
    attached.assert_called_once()
    reserve.assert_not_called()


def test_tariff_reads_jev_provider_endpoints_not_chat_catalog(monkeypatch):
    from src import agenda_worker as worker
    reply=Mock()
    reply.json.return_value={'data':{'id':'typesafe/jev-1.13','endpoints':[
        {'provider_name':'TypeSafe','pricing':{'prompt':'0.000000042','completion':'0','discount':0}}]}}
    get=Mock(return_value=reply)
    monkeypatch.setattr(worker.httpx,'get',get)
    monkeypatch.setenv('OPENROUTER_API_KEY','test-key')
    worker.check_tariff()
    assert get.call_args.args[0]=='https://openrouter.ai/api/v1/models/typesafe/jev-1.13/endpoints'
    assert get.call_args.kwargs['headers']=={'Authorization':'Bearer test-key'}


@pytest.mark.parametrize('data',[
    {'id':'typesafe/jev-latest','endpoints':[{'pricing':{'prompt':'0','completion':'0'}}]},
    {'id':'typesafe/jev-1.13','endpoints':[]},
    {'id':'typesafe/jev-1.13','endpoints':[{'pricing':{'prompt':'.000000042','completion':'0'}},{'pricing':{'prompt':'1','completion':'0'}}]},
])
def test_endpoint_tariff_rejects_wrong_model_missing_or_expensive_provider(monkeypatch,data):
    from src import agenda_worker as worker
    reply=Mock();reply.json.return_value={'data':data}
    monkeypatch.setattr(worker.httpx,'get',Mock(return_value=reply))
    monkeypatch.setenv('OPENROUTER_API_KEY','test-key')
    with pytest.raises(ValueError):worker.check_tariff()
