"""Unpaid discovery work driven by global coverage gaps, never source activation."""
from datetime import datetime, timezone
import time

from src import source_research_store as store
from src.source_discovery import DiscoveryUnavailable, discover_publishers
from src.signal_workbench import instant


def run_source_research(countries, *, as_of, max_searches=4):
    if type(max_searches) is not int or not 0 <= max_searches <= 4:
        raise ValueError('max_searches must be 0..4')
    now = instant(as_of)
    if now > datetime.now(timezone.utc):
        raise ValueError('future source research')
    synced = store.sync_gaps(countries, as_of=now)
    result = {'status': 'ok', 'countries_attempted': 0, 'leads_saved': 0, 'synced': synced}
    deadline = time.monotonic() + 60
    for _ in range(max_searches):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        task = store.claim_discovery(as_of=datetime.now(timezone.utc))
        if task is None:
            break
        result['countries_attempted'] += 1
        try:
            leads = discover_publishers(task['country_code'], max_leads=5,
                                       timeout_seconds=min(15, remaining))
            result['leads_saved'] += store.save_leads(task, leads, as_of=datetime.now(timezone.utc))
        except DiscoveryUnavailable as exc:
            reason = exc.reason if exc.reason in ('rate_limited', 'unavailable', 'ambiguous_country') else 'unavailable'
            store.block_task(task, reason, as_of=datetime.now(timezone.utc))
            result['status'] = 'blocked'
            if reason == 'rate_limited':
                break
        except Exception:
            # Neither network bodies nor third-party exception text enter the report.
            store.block_task(task, 'unavailable', as_of=datetime.now(timezone.utc))
            result['status'] = 'blocked'
    result['queue'] = store.summary(as_of=datetime.now(timezone.utc))
    return result
