"""Hourly worldwide nomination, independent of analyst-provided country prompts."""
from datetime import datetime, timezone
from decimal import Decimal
from collections import defaultdict

from sqlalchemy import text

from src.db import get_session
from src.global_monitoring import load_global_monitoring
from src.global_signal_planner import plan_candidates
from src import global_signal_store as store
from src.early_signal_worker import (configured_campaign, _validated_campaign, _bounds,
                                     run_screening_cycle, write_draft)
from src.signal_workbench import instant
from src.source_research_worker import run_source_research
from src.agenda_translation import run_translation_cycle


def run_global_cycle(*, as_of=None, budget_usd=Decimal('0'), campaign=None,
                     max_screen_calls=4, max_drafts=0, max_source_searches=4,
                     auto_sources=False):
    _bounds(budget_usd, max_screen_calls)
    campaign = configured_campaign() if campaign is None else _validated_campaign(campaign)
    if type(auto_sources) is not bool:
        raise ValueError('auto_sources must be bool')
    if type(max_drafts) is not int or not 0 <= max_drafts <= 2:
        raise ValueError('max_drafts must be 0..2')
    if type(max_source_searches) is not int or not 0 <= max_source_searches <= 4:
        raise ValueError('max_source_searches must be 0..4')
    now = instant(as_of) if as_of else datetime.now(timezone.utc)
    if now > datetime.now(timezone.utc):
        raise ValueError('future monitor')
    snapshot = load_global_monitoring(as_of=now)
    try:
        research = run_source_research(snapshot['countries'], as_of=now,
                                       max_searches=max_source_searches,
                                       auto_activate=auto_sources)
    except Exception:
        # Source discovery is independent of collection and paid interpretation.
        research = {'status': 'blocked', 'reason': 'research_unavailable'}
    screening = {'status': 'disabled'}
    if budget_usd:
        try:
            screening = run_screening_cycle(articles=snapshot['articles'], as_of=now,
                       budget_usd=budget_usd, campaign=campaign, max_calls=max_screen_calls)
        except Exception:
            # Provider/body/credential detail never enters the public health view.
            screening = {'status': 'blocked', 'reason': 'screening_preflight_failed'}
    records = store.current_screenings(snapshot['articles'])
    translations = {'status': 'disabled', 'calls': 0, 'translated': 0}
    if budget_usd:
        # Local change need not mention Russia or qualify for the agenda feed.
        # Translate its source headline in the same fixed campaign, never in GET.
        translation_candidates = [record['article'] for record in records
            if record['classification'].get('signal') == 'change'
            and record['classification'].get('country') not in ('none', 'unknown')
            and record['classification'].get('mechanism') != 'unknown'
            and record['classification'].get('stage') in ('proposal', 'decision', 'implementation')]
        if translation_candidates:
            try:
                translations = run_translation_cycle(articles=translation_candidates,
                    budget_usd=budget_usd, campaign=campaign, max_calls=1)
            except Exception:
                translations = {'status': 'blocked', 'reason': 'translation_unavailable'}
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
    screened = defaultdict(set)
    for record in records:
        article = record['article']
        screened[article.get('country_code')].add(article['id'])
    countries = []
    for row in snapshot['countries']:
        sampled = row.get('sampled_articles_7d', 0)
        count = min(sampled, len(screened[row['code']]))
        state = ('no_sample' if not sampled else 'awaiting_analysis' if not count
                 else 'sample_complete' if count == sampled else 'sample_partial')
        countries.append({**row, 'work': queue.get(row['code'], {}),
                          'screened_articles_7d': count, 'analysis_state': state})
    report = {'as_of': now.isoformat(), 'status': 'ok', 'scope_count': len(countries),
              'countries': countries, 'limits': snapshot['limits'],
              'screening': screening, 'writer': writer, 'nominated': nominated,
              'translations': translations,
              'source_research': research,
              'source_activation': {'enabled': auto_sources},
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
