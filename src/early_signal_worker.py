"""Background early-signal screening and explicit-context draft writing.

Both stages share a persistent campaign cap. Writer drafts never publish
without a separate editorial release. Public API requests never call models.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import math
import os

import httpx
from sqlalchemy import text

from src import agenda_budget as budget, early_signal_store as store
from src.agenda_store import load_articles
from src.decision_model import check_tariff
from src import decision_model
from src.api_tracker import track_api_call
from src.monitoring_registry import MONITORING_COUNTRIES as COUNTRIES
from src.db import get_session
from src.early_signals import MODEL, encode, parse_response, prepare_payload, select_candidates, source_key
from src.decision_model import request as _request
from src.signal_hypotheses import prepare_prompt, validate_dossier
from src.signal_workbench import article_snapshot, instant, request_hash

CAMPAIGN = 'early-signals-2026-10-02'
WRITER_MODEL = 'qwen/qwen3.6-flash'
WRITER_MODELS = {WRITER_MODEL, 'deepseek/deepseek-v4-flash'}
WRITER_VERSION = 'early-signal-writer-v2'


def _bounds(amount, max_calls=10):
    if (not isinstance(amount, Decimal) or not amount.is_finite() or not 0 <= amount <= 2
            or type(max_calls) is not int or not 1 <= max_calls <= 10):
        raise ValueError('Invalid early-signal budget or call bound')


def _cost(data):
    usage = data.get('usage') or {}
    if not isinstance(usage, dict):
        raise ValueError('invalid usage')
    value = usage.get('cost')
    if value is not None and (type(value) not in (float, int) or not math.isfinite(value) or value < 0):
        raise ValueError('invalid cost')
    return value, usage


def _track(model, endpoint, usage, cost, outcome, reason=None, *, service='openrouter'):
    track_api_call(service=service, endpoint=endpoint, model=model,
                   script='build_early_signals.py',
                   tokens_in=usage.get('prompt_tokens', usage.get('input_tokens', 0)),
                   tokens_out=usage.get('completion_tokens', 0),
                   cost=cost if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0 else None,
                   status='ok' if outcome == 'ok' else 'error', error=reason)


def run_screening_cycle(*, budget_usd=Decimal('0'), campaign=CAMPAIGN,
                        max_calls=10, articles=None, as_of=None):
    _bounds(budget_usd, max_calls)
    stats = {'status': 'ok', 'calls': 0, 'screened': 0, 'changes': 0, 'uncertain': 0}
    if not budget_usd:
        return {**stats, 'status': 'disabled'}
    key = os.environ.get(decision_model.KEY_ENV)
    if not key:
        return {**stats, 'status': 'missing_key', 'provider': decision_model.PROVIDER,
                'model': MODEL}
    now = instant(as_of) if as_of else datetime.now(timezone.utc)
    raw = articles if articles is not None else load_articles(hours=168)
    attempted = budget.get_attempted_pair_keys(campaign)
    # Remove completed/unknown attempts before fair selection, so yesterday's
    # unchanged top articles cannot crowd out unseen smaller local reports.
    available = []
    if len(raw) > 30000:
        raise ValueError('too many input articles')
    for article in raw:
        try:
            snapshot = article_snapshot(article, screened=True)
            if source_key(snapshot) not in attempted:
                available.append(snapshot)
        except (KeyError, TypeError, ValueError):
            continue
    # Imported/replayed decisions need not appear in this campaign's paid ledger.
    # Remove them across the entire bounded corpus before the top-80 admission.
    keys = [source_key(a) for a in available]
    cached = {}
    for offset in range(0, len(keys), 80):
        cached.update(store.load_screenings(keys[offset:offset + 80]))
    candidates = select_candidates([a for a in available if source_key(a) not in cached], as_of=now)
    pending = candidates[:]
    stats.update(selected=len(candidates), cached=len(cached))
    codes = sorted(c for c in COUNTRIES if c != 'RU')
    tariff_checked = False
    while pending and stats['calls'] < max_calls:
        payload, selected = prepare_payload(pending, codes)
        if not tariff_checked:
            check_tariff()
            tariff_checked = True
        call_id = budget.reserve_request(campaign, budget_usd, request_hash(payload),
                pair_keys=[source_key(a) for a in selected], reservation_usd=decision_model.RESERVATION_USD)
        if not call_id:
            return {**stats, 'status': 'budget_exhausted'}
        stats['calls'] += 1
        cost, usage, outcome, reason = None, {}, 'error', 'transport_error'
        try:
            result = _request(payload, key, 45 if decision_model.PROVIDER != 'jev' else 10)
            if isinstance(result.get('usage'), dict):
                usage = result['usage']
                cost = usage.get('cost')
            if result.get('status') != 'ok':
                raise OSError('provider_error')
            data = result['data']
            outcome, reason = 'invalid_response', 'invalid_response'
            cost = (data.get('usage') or {}).get('cost') if isinstance(data.get('usage'), dict) else None
            cost, usage = _cost(data)
            records, _ = parse_response(data, selected, codes)
            if cost is not None and cost > float(decision_model.RESERVATION_USD):
                raise ValueError('screening_cost_exceeds_reservation')
            for record in records:
                record['snapshot_hash'] = request_hash(record['article'])
            store.save_screenings(records)
            stats['screened'] += len(records)
            stats['changes'] += sum(r['classification']['signal'] == 'change' for r in records)
            stats['uncertain'] += sum(r['classification']['signal'] == 'uncertain' for r in records)
            outcome, reason = 'ok', None
        except ValueError as exc:
            # Core validators use fixed messages, never interpolate source text.
            reason = str(exc) if outcome == 'invalid_response' else 'transport_error'
            stats.update(status='error', error=reason)
        except Exception:
            stats.update(status='error', error=reason)
        finally:
            budget.finish_request(call_id, cost, outcome)
            _track(MODEL, decision_model.ENDPOINT, usage, cost, outcome, reason,
                   service=decision_model.SERVICE)
        if outcome != 'ok':
            break
        ids = {a['id'] for a in selected}
        pending = [a for a in pending if a['id'] not in ids]
    stats['remaining_budget_usd'] = budget.get_budget(campaign)
    stats.update(model=MODEL, provider=decision_model.PROVIDER)
    return stats


def locked_screening_cycle(**kwargs):
    _bounds(kwargs.get('budget_usd', Decimal('0')), kwargs.get('max_calls', 10))
    if not kwargs.get('budget_usd', Decimal('0')):
        return {'status': 'disabled', 'calls': 0, 'screened': 0}
    with get_session() as session:
        acquired = session.execute(text("SELECT pg_try_advisory_lock(hashtext('geopulse-early-signals'))")).scalar()
        if not acquired:
            return {'status': 'running'}
        try:
            return run_screening_cycle(**kwargs)
        finally:
            session.execute(text("SELECT pg_advisory_unlock(hashtext('geopulse-early-signals'))"))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def write_draft(context, *, budget_usd=Decimal('0'), campaign=CAMPAIGN, model=WRITER_MODEL):
    """One prepaid request; validates quotations and stores no public release."""
    _bounds(budget_usd)
    if not budget_usd:
        raise ValueError('writer_disabled')
    if model not in WRITER_MODELS:
        raise ValueError('unapproved_writer_model')
    now = instant(context['as_of'])
    if now > datetime.now(timezone.utc):
        raise ValueError('future_context')
    prompt = prepare_prompt(context['articles'], as_of=now)
    key = os.environ.get('OPENROUTER_API_KEY')
    if not key:
        raise ValueError('missing_key')
    fingerprint = hashlib.sha256((WRITER_VERSION + model + prompt).encode()).hexdigest()
    call_id = budget.reserve_request(campaign, budget_usd, fingerprint,
                                    pair_keys=[fingerprint], reservation_usd=Decimal('.10'))
    if not call_id:
        raise ValueError('writer_already_attempted_or_budget_exhausted')
    # 24KB prompt + 2000 output, conservative 2 tokens/byte + 2000 envelope
    # at $1/$2 per million, zero request fee: <=$0.054 under the $0.10 hold.
    payload = {'model': model, 'messages': [{'role': 'user', 'content': prompt}],
               'max_tokens': 2000, 'temperature': 0, 'reasoning': {'enabled': False},
               'provider': {'max_price': {'prompt': 1, 'completion': 2, 'request': 0},
                            'allow_fallbacks': False, 'require_parameters': True}}
    cost, usage, outcome, reason = None, {}, 'error', 'transport_error'
    try:
        response = httpx.post('https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': 'Bearer ' + key}, json=payload, timeout=45)
        response.raise_for_status()
        outcome, reason = 'invalid_response', 'invalid_response'
        data = response.json()
        cost = (data.get('usage') or {}).get('cost') if isinstance(data.get('usage'), dict) else None
        cost, usage = _cost(data)
        choice = data['choices'][0]
        if cost is not None and cost > .10:
            raise ValueError('writer_cost_exceeds_reservation')
        if choice.get('finish_reason') != 'stop':
            raise ValueError('incomplete_response')
        content = choice['message']['content']
        if not isinstance(content, str) or len(content.encode()) > 20000:
            raise ValueError('invalid_response_size')
        draft = json.loads(content, object_pairs_hook=_unique_object)
        validated = validate_dossier(draft, context['articles'], as_of=now)
        outcome, reason = 'ok', None
        return {'draft': draft, 'validated': validated, 'model': model, 'cost_usd': cost}
    except ValueError as exc:
        reason = str(exc) if outcome == 'invalid_response' else 'transport_error'
        raise ValueError(reason) from None
    except Exception:
        raise ValueError(reason) from None
    finally:
        budget.finish_request(call_id, cost, outcome)
        _track(model, '/chat/completions', usage, cost, outcome, reason)
