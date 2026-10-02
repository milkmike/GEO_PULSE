"""Hourly worldwide nomination, independent of analyst-provided country prompts."""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from src.db import get_session
from src.global_monitoring import load_global_monitoring
from src.global_signal_planner import plan_candidates
from src import global_signal_store as store
from src.early_signal_worker import CAMPAIGN, _bounds, run_screening_cycle, write_draft
from src.signal_workbench import instant


def run_global_cycle(*, as_of=None, budget_usd=Decimal('0'), campaign=CAMPAIGN,
                     max_screen_calls=4, max_drafts=0):
    _bounds(budget_usd, max_screen_calls)
    if type(max_drafts) is not int or not 0 <= max_drafts <= 2:
        raise ValueError('max_drafts must be 0..2')
    now = instant(as_of) if as_of else datetime.now(timezone.utc)
    if now > datetime.now(timezone.utc):
        raise ValueError('future monitor')
    snapshot = load_global_monitoring(as_of=now)
    screening = {'status': 'disabled'}
    if budget_usd:
        try:
            screening = run_screening_cycle(articles=snapshot['articles'], as_of=now,
                       budget_usd=budget_usd, campaign=campaign, max_calls=max_screen_calls)
        except Exception:
            # Provider/body/credential detail never enters the public health view.
            screening = {'status': 'blocked', 'reason': 'screening_preflight_failed'}
    records = store.current_screenings(snapshot['articles'])
    candidates = plan_candidates(records, snapshot['articles'], as_of=now, defer_keys=store.known_work_keys())
    nominated = store.sync_candidates(candidates, as_of=now, current_articles=snapshot['articles'])
    writer = {'status': 'disabled', 'calls': 0, 'drafts': 0}
    if budget_usd and max_drafts and screening.get('status') in ('ok', 'budget_exhausted'):
        writer['status'] = 'ok'
        for _ in range(max_drafts):
            item = store.claim_context(as_of=now, current_articles=snapshot['articles'])
            if item is None:
                break
            writer['calls'] += 1
            try:
                result = write_draft(item['context'], budget_usd=budget_usd, campaign=campaign)
                store.save_private_draft(item, result)
                writer['drafts'] += 1
            except Exception:
                store.block_work(item, 'draft_failed')
                writer['status'] = 'blocked'
                break
    queue = store.queue_summary(as_of=now)
    countries = [{**row, 'work': queue.get(row['code'], {})} for row in snapshot['countries']]
    report = {'as_of': now.isoformat(), 'status': 'ok', 'scope_count': len(countries),
              'countries': countries, 'limits': snapshot['limits'],
              'screening': screening, 'writer': writer, 'nominated': nominated,
              'unknown_geography_work': queue.get('unknown', {}),
              'notice': 'Страна источника показывает охват. Она не определяет страну события. Отсутствие данных не означает отсутствия изменений.'}
    store.save_monitor_run(report)
    return report


def locked_global_cycle(**kwargs):
    _bounds(kwargs.get('budget_usd', Decimal('0')), kwargs.get('max_screen_calls', 4))
    with get_session() as session:
        acquired = session.execute(text("SELECT pg_try_advisory_lock(hashtext('geopulse-early-signals'))")).scalar()
        if not acquired:
            return {'status': 'running'}
        try:
            return run_global_cycle(**kwargs)
        finally:
            session.execute(text("SELECT pg_advisory_unlock(hashtext('geopulse-early-signals'))"))
