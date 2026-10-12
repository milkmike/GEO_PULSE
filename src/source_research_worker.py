"""Unpaid discovery and opt-in verified feed activation from coverage gaps."""
from datetime import datetime, timezone
import time

from src import source_research_store as store
from src.source_discovery import DiscoveryUnavailable, discover_publishers
from src.source_feed_validation import FeedUnsafe, FeedUnavailable, find_valid_feed
from src.signal_workbench import instant


def run_source_research(countries, *, as_of, max_searches=4, auto_activate=False):
    if type(max_searches) is not int or not 0 <= max_searches <= 4:
        raise ValueError('max_searches must be 0..4')
    if type(auto_activate) is not bool:
        raise ValueError('auto_activate must be boolean')
    now = instant(as_of)
    if now > datetime.now(timezone.utc):
        raise ValueError('future source research')
    synced = store.sync_gaps(countries, as_of=now)
    result = {'status': 'ok', 'countries_attempted': 0, 'leads_saved': 0,
              'feeds_validated': 0, 'sources_activated': 0, 'feeds_repaired': 0,
              'admission_failures': 0, 'synced': synced}
    deadline = time.monotonic() + 60
    if auto_activate:
        lead = store.claim_activation(as_of=datetime.now(timezone.utc))
        if lead is not None:
            result['countries_attempted'] = 1
            try:
                feed = find_valid_feed(lead['website'], now=datetime.now(timezone.utc),
                                       deadline=min(deadline, time.monotonic() + 20))
                result['feeds_validated'] = int(feed is not None)
                admission = store.finish_activation(lead, feed_url=feed,
                    reason=None if feed else 'no_valid_feed', as_of=datetime.now(timezone.utc))
                result['sources_activated'] = int(admission['status'] == 'active')
                result['feeds_repaired'] = int(admission['status'] == 'repaired')
                result['admission_failures'] = int(admission['status'] not in ('active', 'repaired'))
            except (FeedUnsafe, FeedUnavailable) as exc:
                reason = 'unsafe' if isinstance(exc, FeedUnsafe) else 'unavailable'
                store.finish_activation(lead, reason=reason, as_of=datetime.now(timezone.utc))
                result['admission_failures'] = 1
            except Exception:
                # No third-party body or exception detail enters persistent state.
                store.finish_activation(lead, reason='unavailable', as_of=datetime.now(timezone.utc))
                result['admission_failures'] = 1
    for _ in range(min(max_searches, 1 if auto_activate else 4)):
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
