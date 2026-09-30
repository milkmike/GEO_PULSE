"""Budgeted article-first Jev discovery; network never runs in a public request."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from decimal import Decimal

import httpx
from sqlalchemy import text

from src import agenda_store as store, agenda_budget as budget
from src.agenda_discovery import (MODEL, MAX_REQUEST_BYTES, candidate_group_page,
    prepare_pair_payload, parse_pair_response, pair_cache_key, accepted_decision)
from src.api_tracker import track_api_call
from src.db import get_session
from src.jev import _request

logger=logging.getLogger(__name__)
CAMPAIGN='jev-agenda-2026-09-30'


def validate_tariff(data):
    model=next((m for m in data.get('data',[]) if m.get('id')==MODEL),None)
    if not model:
        raise ValueError('Jev model absent from catalog')
    prices=model.get('pricing',{})
    values=[Decimal(str(prices.get(key,'0' if key=='request' else 'NaN'))) for key in ('prompt','completion','request')]
    if not all(v.is_finite() and v>=0 for v in values) or values[0]>Decimal('.0000001') or values[1:]!=[Decimal(0),Decimal(0)]:
        raise ValueError('Jev tariff exceeds reserved bound')


def check_tariff():
    # Decisions models are absent from the chat model list. Query the official
    # per-model provider catalog and require every eligible provider to fit.
    response=httpx.get(f'https://openrouter.ai/api/v1/models/{MODEL}/endpoints',
                      headers={'Authorization':'Bearer '+os.environ['OPENROUTER_API_KEY']},timeout=10)
    response.raise_for_status()
    data=response.json().get('data')
    if not isinstance(data,dict) or data.get('id')!=MODEL:
        raise ValueError('Unexpected Jev provider catalog')
    endpoints=data.get('endpoints')
    if not isinstance(endpoints,list) or not endpoints:
        raise ValueError('Jev has no available provider')
    for endpoint in endpoints:
        if not isinstance(endpoint,dict):
            raise ValueError('Invalid Jev provider metadata')
        validate_tariff({'data':[{'id':MODEL,'pricing':endpoint.get('pricing',{})}]})


def run_cycle(*,budget_usd=Decimal('0'),campaign=CAMPAIGN,max_calls=20):
    stats={'articles_scanned':0,'candidate_groups':0,'decisions':0,'accepted':0,'calls':0}
    if not budget_usd.is_finite() or not 0<=budget_usd<=3 or not 1<=max_calls<=20:
        raise ValueError('Invalid budget or call bound')
    if budget_usd==0:
        store.record_run('disabled',stats)
        return {**stats,'status':'disabled'}
    if not os.environ.get('OPENROUTER_API_KEY'):
        store.record_run('error',{**stats,'error':'missing_key'})
        return {**stats,'status':'error'}
    articles=store.load_articles()
    existing=store.load_groups()
    by_id={a['id']:a for a in articles}
    for group in existing:
        by_id[group['anchor']['id']]=group['anchor']
    discovery=store.get_discovery_state()
    attempted=budget.get_attempted_pair_keys(campaign)
    excluded=set(discovery['known_pair_keys']) | (attempted-set(discovery['cached_pair_keys']))
    page=candidate_group_page(articles,existing,known_pair_keys=excluded,cursor=discovery['cursor'])
    groups=page['groups']
    stats['discovery_cursor']=page['next_cursor']
    stats.update(articles_scanned=len(articles),candidate_groups=len(groups))
    status='ok'
    tariff_checked=False
    stop=False
    for group_index,group in enumerate(groups):
        anchor=group['anchor']
        candidates=group['candidates']
        cache=store.get_cached_decisions([pair_cache_key(anchor,a) for a in candidates])
        decisions=list(cache.values())
        pending=[a for a in candidates if pair_cache_key(anchor,a) not in cache]
        while pending and stats['calls']<max_calls:
            payload,pairs=prepare_pair_payload(anchor,pending)
            if not pairs:
                break
            encoded=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
            if len(encoded)>MAX_REQUEST_BYTES:
                raise ValueError('Payload exceeds budget envelope')
            if not tariff_checked:
                check_tariff()
                tariff_checked=True
            request_id=budget.reserve_request(campaign,budget_usd,hashlib.sha256(encoded).hexdigest(),
                                              pair_keys=[p['cache_key'] for p in pairs.values()])
            if request_id is None:
                status='budget_exhausted'
                stop=True
                break
            stats['calls']+=1
            cost=None
            outcome='error'
            usage={}
            try:
                response=_request(payload,os.environ['OPENROUTER_API_KEY'],5)
                outcome=response.get('status','error')
                if outcome!='ok':
                    raise ValueError('Provider did not return a decision')
                data=response['data']
                usage=data.get('usage') or {}
                cost=usage.get('cost')
                fresh,cost=parse_pair_response(data,pairs)
                store.save_decisions(fresh)
                decisions.extend(fresh)
            except (ValueError,TypeError,KeyError,subprocess.SubprocessError,OSError):
                if outcome=='ok':
                    outcome='invalid_response'
                status='error'
                stop=True
            finally:
                budget.finish_request(request_id,cost,outcome)
                track_api_call(service='openrouter',endpoint='/alpha/decisions',model=MODEL,
                    script='build_agendas.py',tokens_in=usage.get('prompt_tokens',usage.get('input_tokens',0)),
                    cost=cost if isinstance(cost,(int,float)) and not isinstance(cost,bool) and 0<=cost<=.1 else None,
                    status='ok' if outcome=='ok' else 'error',error=None if outcome=='ok' else outcome)
            if stop:
                break
            attempted={p['article_id'] for p in pairs.values()}
            pending=[a for a in pending if a['id'] not in attempted]
        stats['decisions']+=len(decisions)
        if decisions:
            stats['accepted']+=sum(accepted_decision(d) for d in decisions)
            store.attach_decisions(anchor,decisions,by_id)
        if stop or stats['calls']>=max_calls:
            # Resume this anchor if its candidate chunk was interrupted.
            position=page['group_cursors'][group_index]
            stats['discovery_cursor']=position['cursor'] if pending and not stop else position['next_cursor']
            break
    stats['remaining_budget_usd']=budget.get_budget(campaign)
    store.record_run(status,stats)
    return {**stats,'status':status}


def locked_cycle(**kwargs):
    with get_session() as session:
        acquired=session.execute(text("SELECT pg_try_advisory_lock(hashtext('geopulse-agenda-worker'))")).scalar()
        if not acquired:
            return {'status':'running'}
        try:
            return run_cycle(**kwargs)
        except Exception as exc:
            # Do not copy provider payloads, article text or credentials into logs.
            store.record_run('error',{'error':type(exc).__name__})
            logger.error('Agenda cycle failed: %s',type(exc).__name__)
            return {'status':'error'}
        finally:
            session.execute(text("SELECT pg_advisory_unlock(hashtext('geopulse-agenda-worker'))"))
