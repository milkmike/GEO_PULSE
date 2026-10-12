"""Translations are background-only, bounded and share the discovery budget."""
import json
from decimal import Decimal
from unittest.mock import Mock

import httpx
import pytest


def rows(count=2):
    return [{'id':i,'title':'Самолёт сел в Табуке' if i==1 else f'Flight lands in Tabuk {i}'}
            for i in range(1,count+1)]


@pytest.mark.parametrize('response', [
    '{"1":"Самолёт сел в Табуке"}',
    '{"1":"Самолёт сел в Табуке","3":"Рейс сел"}',
    '{"1":"Самолёт сел в Табуке","2":"<script>alert(1)</script>"}',
    '{"1":"Самолёт сел в Табуке","2":"https://evil.example"}',
    '{"1":"Самолёт сел в Табуке","2":"Flight lands in Tabuk"}',
    '{"1":"Самолёт сел в Табуке","2":"Рейс сел","2":"Другой рейс"}',
    '```json\n{"1":"Самолёт сел в Табуке","2":"Рейс сел"}\n```',
])
def test_invalid_response_never_returns_partial_translations(response):
    from src.agenda_translation import parse_translations
    with pytest.raises(ValueError):
        parse_translations(response, rows())


def test_valid_response_keeps_article_identity_and_original_title():
    from src.agenda_translation import parse_translations, prepare_prompt
    articles=rows()
    parsed=parse_translations(json.dumps({'1':articles[0]['title'],'2':'Рейс сел в Табуке 2'}),articles)
    assert parsed==[{'article_id':a['id'],'source_title':a['title'],'title_ru':ru}
                    for a,ru in zip(articles,[articles[0]['title'],'Рейс сел в Табуке 2'])]
    prompt=prepare_prompt([{'id':3,'title':'Српски председник најавио посету'}])
    assert 'Српски председник' in prompt
    assert 'untrusted' in prompt and 'Russian' in prompt


def test_translation_uses_bounded_source_context_and_preserves_it_for_save_guard():
    from src.agenda_translation import prepare_prompt, parse_translations
    articles = [{'id': 1, 'title': 'Awazada Hazaryň suwunyň azalmagy boýunça komissiýa',
                 'excerpt': 'Hazar ekologiýa sammitinde deňziň suw derejesiniň peselmegi.'}]
    prompt = prepare_prompt(articles)
    inputs = json.loads(prompt.split('\nARTICLES:\n')[1])
    assert inputs[0]['excerpt'] == articles[0]['excerpt']
    assert 'disambiguate' in prompt and 'untrusted' in prompt
    parsed = parse_translations('{"1":"В Авазе обсудили обмеление Каспия"}', articles)
    assert parsed[0]['source_excerpt'] == articles[0]['excerpt']


@pytest.mark.parametrize('excerpt', ['я' * 2000, '\\"\n' * 1000, '🌍' * 2000])
def test_context_never_exceeds_the_prepaid_prompt_envelope(excerpt):
    from src.agenda_translation import prepare_prompt
    articles = [{'id': i, 'title': 'Новость ' + 'я' * 116, 'excerpt': excerpt}
                for i in range(1, 9)]
    prompt = prepare_prompt(articles)
    assert len(prompt.encode()) <= 6000
    inputs = json.loads(prompt.split('\nARTICLES:\n')[1])
    assert len(inputs) == 8
    assert all(row.get('excerpt') and excerpt.startswith(row['excerpt']) for row in inputs)


@pytest.fixture
def harness(monkeypatch):
    from src import agenda_translation as translation
    pending=Mock(return_value=rows())
    saved=Mock(side_effect=lambda translations, model: len(translations))
    reserve=Mock(return_value='reserved')
    finish=Mock()
    monkeypatch.setattr(translation.store,'load_translation_candidates',pending)
    monkeypatch.setattr(translation.store,'save_title_translations',saved)
    monkeypatch.setattr(translation.budget,'get_attempted_pair_keys',lambda _:set())
    monkeypatch.setattr(translation.budget,'reserve_request',reserve)
    monkeypatch.setattr(translation.budget,'finish_request',finish)
    monkeypatch.setattr(translation.budget,'get_budget',lambda _:0)
    monkeypatch.setattr(translation,'track_api_call',Mock())
    monkeypatch.setenv('OPENROUTER_API_KEY','test')
    return translation,pending,saved,reserve,finish


def test_budget_exhausted_never_calls_http(monkeypatch,harness):
    translation,pending,saved,reserve,finish=harness
    reserve.return_value=None
    monkeypatch.setattr(httpx,'post',lambda *a,**k:pytest.fail('paid call without reservation'))
    result=translation.run_translation_cycle(budget_usd=Decimal('3'),campaign='same-campaign')
    assert result['status']=='budget_exhausted'
    saved.assert_not_called()
    finish.assert_not_called()


def test_cache_hit_never_reserves_or_calls_http(monkeypatch,harness):
    translation,pending,saved,reserve,finish=harness
    pending.return_value=[]
    monkeypatch.setattr(httpx,'post',lambda *a,**k:pytest.fail('cached title retranslated'))
    assert translation.run_translation_cycle(budget_usd=Decimal('3'),campaign='same')['calls']==0
    reserve.assert_not_called()


@pytest.mark.parametrize('unsafe', [False,True])
def test_real_bounded_chat_reserves_first_and_settles_actual_usage(monkeypatch,harness,unsafe):
    translation,pending,saved,reserve,finish=harness
    def post(url,**kwargs):
        reserve.assert_called_once()
        assert kwargs['json']['max_tokens']==1000
        assert kwargs['json']['model']=='deepseek/deepseek-v4-flash'
        response_format=kwargs['json']['response_format']
        assert response_format['type']=='json_schema'
        assert response_format['json_schema']['strict'] is True
        schema=response_format['json_schema']['schema']
        assert schema['required']==['1','2'] and schema['additionalProperties'] is False
        content={'1':'Самолёт сел в Табуке','2':'<script>bad</script>' if unsafe else 'Рейс сел в Табуке 2'}
        return httpx.Response(200,request=httpx.Request('POST',url),json={
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps(content)}}],
            'usage':{'cost':.0003}})
    monkeypatch.setattr(httpx,'post',post)
    result=translation.run_translation_cycle(budget_usd=Decimal('3'),campaign='same')
    assert result['calls']==1
    assert reserve.call_args.args[:2]==('same',Decimal('3'))
    assert reserve.call_args.kwargs['reservation_usd'] == Decimal('.02')
    finish.assert_called_once_with('reserved',.0003,'invalid_response' if unsafe else 'ok')
    assert saved.call_count==(0 if unsafe else 1)


@pytest.mark.parametrize(('content', 'finish_reason', 'reason'), [
    ('{"1":', 'stop', 'invalid_json'),
    ('{"1":"Рейс сел"}', 'stop', 'validation_error'),
    ('{"1":"Рейс сел","2":"Рейс сел"}', 'length', 'incomplete_output'),
])
def test_failed_translation_reports_fixed_reason_and_keeps_hold(monkeypatch,harness,content,finish_reason,reason):
    translation,pending,saved,reserve,finish=harness
    logs=[]
    monkeypatch.setattr(translation,'track_api_call',lambda **kwargs:logs.append(kwargs))
    monkeypatch.setattr(httpx,'post',lambda url,**kwargs:httpx.Response(200,
        request=httpx.Request('POST',url),json={
            'choices':[{'finish_reason':finish_reason,'message':{'content':content}}],
            'usage':{'cost':.0003}}))
    stats=translation.run_translation_cycle(budget_usd=Decimal('3'),campaign='same')
    assert stats['last_error']==reason
    assert logs[0]['error']==reason
    finish.assert_called_once_with('reserved',.0003,'invalid_response')
    saved.assert_not_called()


def test_two_call_bound_and_attempted_titles_are_not_rebilled(monkeypatch,harness):
    translation,pending,saved,reserve,finish=harness
    pending.return_value=rows(25)
    monkeypatch.setattr(translation.budget,'get_attempted_pair_keys',lambda _:{translation.translation_key(rows()[0])})
    calls=[]
    def post(url,**kwargs):
        data=json.loads(kwargs['json']['messages'][0]['content'].split('\nARTICLES:\n')[1])
        calls.append(data)
        return httpx.Response(200,request=httpx.Request('POST',url),json={
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps({str(a['id']):'Рейс сел' for a in data})}}],
            'usage':{'cost':.0003}})
    monkeypatch.setattr(httpx,'post',post)
    assert translation.run_translation_cycle(budget_usd=Decimal('3'),campaign='same')['calls']==2
    assert [len(batch) for batch in calls]==[8,8]
    assert all(a['id']!=1 for batch in calls for a in batch)


def test_disabled_translation_does_not_touch_storage(monkeypatch):
    from src import agenda_translation as translation
    monkeypatch.setattr(translation.store,'load_translation_candidates',lambda:pytest.fail('disabled DB read'))
    monkeypatch.setattr(translation.budget,'get_attempted_pair_keys',lambda _:pytest.fail('disabled budget read'))
    assert translation.run_translation_cycle(budget_usd=Decimal('0'),campaign='same')['status']=='disabled'


@pytest.mark.parametrize('cost', [-1, float('nan'), True, 'invalid', .11])
def test_invalid_raw_cost_reaches_durable_budget_guard(monkeypatch,harness,cost):
    translation,pending,saved,reserve,finish=harness
    reply=Mock(status_code=200)
    reply.json.return_value={'choices':[{'finish_reason':'stop','message':{'content':'{}'}}], 'usage':{'cost':cost}}
    monkeypatch.setattr(httpx,'post',lambda *a,**k:reply)
    assert translation.run_translation_cycle(budget_usd=Decimal('3'),campaign='same')['status']=='error'
    assert finish.call_args.args[1] is cost
    saved.assert_not_called()


def test_timeout_retains_unknown_charge_and_attempted_titles_are_skipped(monkeypatch,harness):
    translation,pending,saved,reserve,finish=harness
    attempted=set()
    def reserve_call(*args,**kwargs):
        attempted.update(kwargs['pair_keys'])
        return 'reserved'
    reserve.side_effect=reserve_call
    monkeypatch.setattr(translation.budget,'get_attempted_pair_keys',lambda _:attempted)
    post=Mock(side_effect=httpx.ReadTimeout('unknown outcome'))
    monkeypatch.setattr(httpx,'post',post)
    assert translation.run_translation_cycle(budget_usd=Decimal('3'),campaign='same')['status']=='error'
    finish.assert_called_once_with('reserved',None,'error')
    assert translation.run_translation_cycle(budget_usd=Decimal('3'),campaign='same')['calls']==0
    post.assert_called_once()


def test_reservation_collision_skips_batch_without_false_exhaustion(monkeypatch,harness):
    translation,pending,saved,reserve,finish=harness
    pending.return_value=rows(9)
    reserve.side_effect=[None,'second']
    monkeypatch.setattr(translation.budget,'get_budget',lambda _:2)
    monkeypatch.setattr(httpx,'post',lambda url,**kwargs:httpx.Response(200,
        request=httpx.Request('POST',url),json={'choices':[{'finish_reason':'stop',
        'message':{'content':'{"9":"Рейс сел"}'}}],'usage':{'cost':.0003}}))
    result=translation.run_translation_cycle(budget_usd=Decimal('3'),campaign='same')
    assert result=={'status':'ok','calls':1,'translated':1}


def test_long_titles_pack_within_output_envelope(monkeypatch,harness):
    translation,pending,saved,reserve,finish=harness
    pending.return_value=[{'id':i,'title':'Foreign title '+('a'*186)} for i in range(1,14)]
    batches=[]
    def post(url,**kwargs):
        articles=json.loads(kwargs['json']['messages'][0]['content'].split('\nARTICLES:\n')[1])
        batches.append(articles)
        return httpx.Response(200,request=httpx.Request('POST',url),json={
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps({str(a['id']):'Перевод заголовка' for a in articles})}}],
            'usage':{'cost':.0003}})
    monkeypatch.setattr(httpx,'post',post)
    assert translation.run_translation_cycle(budget_usd=Decimal('3'),campaign='same')['calls']==2
    assert all(sum(len(a['title']) for a in batch)<=1000 for batch in batches)
    assert [len(batch) for batch in batches]==[5,5]


def test_explicit_world_candidates_use_current_cache_and_fair_selection(monkeypatch,harness):
    from src.signal_workbench import article_snapshot
    from tests.test_early_signals import article
    from datetime import datetime,timezone
    translation,pending,saved,reserve,finish=harness
    now=datetime.now(timezone.utc)
    supplied=[article_snapshot(article(i,country='RS',published_at=now,collected_at=now),screened=True)
              for i in range(1,25)]
    supplied.append(article_snapshot(article(25,country='GE',published_at=now,collected_at=now),screened=True))
    cache=Mock(return_value=supplied)
    monkeypatch.setattr(translation.store,'load_current_translation_candidates',cache)
    pending.side_effect=AssertionError('global translation queue must not be used')
    def post(url,**kwargs):
        batch=json.loads(kwargs['json']['messages'][0]['content'].split('\nARTICLES:\n')[1])
        assert any(a['id']==25 for a in batch)
        return httpx.Response(200,request=httpx.Request('POST',url),json={
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps({str(a['id']):'Перевод' for a in batch})}}],
            'usage':{'cost':.0003}})
    monkeypatch.setattr(httpx,'post',post)
    result=translation.run_translation_cycle(budget_usd=Decimal('3'),campaign='same',
                                             max_calls=1,articles=supplied)
    assert result['calls']==1 and result['translated']<=8
    cache.assert_called_once_with(supplied)
    assert reserve.call_args.kwargs['reservation_usd']==Decimal('.02')


def test_explicit_translation_missing_key_or_zero_budget_skips_db(monkeypatch):
    from src import agenda_translation as translation
    source=[{'id':1,'title':'Current title'}]
    monkeypatch.setattr(translation.store,'load_current_translation_candidates',
                        lambda *_:pytest.fail('no DB read'))
    monkeypatch.delenv('OPENROUTER_API_KEY',raising=False)
    assert translation.run_translation_cycle(budget_usd=Decimal('3'),campaign='same',articles=source)['status']=='missing_key'
    assert translation.run_translation_cycle(budget_usd=Decimal('0'),campaign='same',articles=source)['status']=='disabled'


def test_attempted_world_titles_cannot_hide_unseen_titles(monkeypatch,harness):
    from tests.test_early_signals import article
    from datetime import datetime,timezone
    translation,pending,saved,reserve,finish=harness
    now=datetime.now(timezone.utc)
    supplied=[{**article(i,country='RS',published_at=now,collected_at=now),
               'source_id':1,'source_name':'One publisher'} for i in range(1,91)]
    attempted={translation.translation_key(a) for a in supplied[10:]}
    assert all(translation.translation_key(a) in attempted
               for a in translation.select_candidates(supplied,as_of=now))
    monkeypatch.setattr(translation.budget,'get_attempted_pair_keys',lambda _:attempted)
    monkeypatch.setattr(translation.store,'load_current_translation_candidates',lambda articles:articles)
    def post(url,**kwargs):
        batch=json.loads(kwargs['json']['messages'][0]['content'].split('\nARTICLES:\n')[1])
        assert batch and all(a['id']<=10 for a in batch)
        return httpx.Response(200,request=httpx.Request('POST',url),json={
            'choices':[{'finish_reason':'stop','message':{'content':json.dumps({str(a['id']):'Перевод' for a in batch})}}],
            'usage':{'cost':.0003}})
    monkeypatch.setattr(httpx,'post',post)
    result=translation.run_translation_cycle(budget_usd=Decimal('2'),campaign='same',max_calls=1,articles=supplied)
    assert result['translated']>0


def test_corrected_context_retries_once_and_noop_save_is_not_counted(monkeypatch, harness):
    from datetime import datetime, timezone
    from tests.test_early_signals import article
    translation, pending, saved, reserve, finish = harness
    now = datetime.now(timezone.utc)
    stale = article(1, country='RS', published_at=now, collected_at=now)
    current = {**stale, 'excerpt': 'A corrected context'}
    attempted = {translation.translation_key(stale)}
    monkeypatch.setattr(translation.budget, 'get_attempted_pair_keys', lambda _: attempted)
    monkeypatch.setattr(translation.store, 'load_current_translation_candidates', lambda _: [current])
    def reserve_once(*args, **kwargs):
        attempted.update(kwargs['pair_keys'])
        return 'corrected'
    reserve.side_effect = reserve_once
    saved.side_effect = lambda *args: 0  # Another correction happened during HTTP.
    post = Mock(return_value=httpx.Response(200, request=httpx.Request('POST', 'https://openrouter.ai'),
        json={'choices': [{'finish_reason': 'stop', 'message': {'content': '{"1":"Перевод"}'}}],
              'usage': {'cost': .0003}}))
    monkeypatch.setattr(httpx, 'post', post)
    result = translation.run_translation_cycle(budget_usd=Decimal('2'), campaign='same', articles=[stale])
    assert result['calls'] == 1 and result['translated'] == 0
    assert saved.call_args.args[0][0]['source_excerpt'] == current['excerpt']
    assert translation.run_translation_cycle(budget_usd=Decimal('2'), campaign='same', articles=[stale])['calls'] == 0
    post.assert_called_once()


def test_empty_context_still_guards_a_concurrent_source_correction():
    from src.agenda_translation import parse_translations
    assert parse_translations('{"1":"Перевод"}', [{'id': 1, 'title': 'Title', 'excerpt': ''}])[0]['source_excerpt'] == ''
