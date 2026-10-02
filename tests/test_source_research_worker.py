"""Global source research is bounded, unpaid and cannot activate publishers."""
from datetime import datetime, timezone

import pytest

from src import source_research_worker as worker

NOW = datetime(2026, 10, 2, 10, tzinfo=timezone.utc)


def setup(monkeypatch, tasks):
    events = []
    pending = iter(tasks)
    monkeypatch.setattr(worker.store, 'sync_gaps', lambda countries, **kw: events.append(('sync', countries)) or {'queued': len(countries)})
    monkeypatch.setattr(worker.store, 'claim_discovery', lambda **kw: next(pending, None))
    monkeypatch.setattr(worker.store, 'save_leads', lambda task, leads, **kw: events.append(('leads', task['country_code'], leads)) or len(leads))
    monkeypatch.setattr(worker.store, 'block_task', lambda task, reason, **kw: events.append(('blocked', task['country_code'], reason)))
    monkeypatch.setattr(worker.store, 'summary', lambda **kw: {'queued': 1})
    return events


def test_country_inventory_drives_discovery_without_analyst_prompt(monkeypatch):
    events = setup(monkeypatch, [{'country_code': 'AD'}, {'country_code': 'AO'}])
    monkeypatch.setattr(worker, 'discover_publishers', lambda code, **kw: [{'entity_id': code, 'website': 'https://example.org'}])
    result = worker.run_source_research([{'code': 'AD'}, {'code': 'AO'}], as_of=NOW, max_searches=1)
    assert result['countries_attempted'] == 1 and result['leads_saved'] == 1
    assert events[0] == ('sync', [{'code': 'AD'}, {'code': 'AO'}])
    assert [event[1] for event in events if event[0] == 'leads'] == ['AD']


def test_zero_searches_still_preserves_gap_work(monkeypatch):
    events = setup(monkeypatch, [])
    monkeypatch.setattr(worker, 'discover_publishers', lambda *a, **kw: pytest.fail('network'))
    result = worker.run_source_research([{'code': 'AD'}], as_of=NOW, max_searches=0)
    assert result['countries_attempted'] == 0 and events[0][0] == 'sync'


def test_rate_limiting_stops_batch_and_keeps_unattempted_countries(monkeypatch):
    events = setup(monkeypatch, [{'country_code': 'AD'}, {'country_code': 'AO'}])
    def limited(*args, **kwargs):
        raise worker.DiscoveryUnavailable('rate_limited')
    monkeypatch.setattr(worker, 'discover_publishers', limited)
    result = worker.run_source_research([], as_of=NOW, max_searches=4)
    assert result['status'] == 'blocked' and result['countries_attempted'] == 1
    assert events[-1] == ('blocked', 'AD', 'rate_limited')


def test_untrusted_provider_error_body_is_not_saved_or_exposed(monkeypatch):
    events = setup(monkeypatch, [{'country_code': 'AD'}])
    def fail(*args, **kwargs):
        raise OSError('credential / untrusted body')
    monkeypatch.setattr(worker, 'discover_publishers', fail)
    result = worker.run_source_research([], as_of=NOW)
    assert ('blocked', 'AD', 'unavailable') in events
    assert 'credential' not in str(result) + str(events)


def test_whole_cycle_deadline_does_not_start_another_country(monkeypatch):
    setup(monkeypatch, [{'country_code': 'AD'}, {'country_code': 'AO'}])
    ticks = iter([0, 0, 61])
    monkeypatch.setattr(worker.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(worker, 'discover_publishers', lambda *a, **kw: [])
    result = worker.run_source_research([], as_of=NOW, max_searches=4)
    assert result['countries_attempted'] == 1


@pytest.mark.parametrize('count', [-1, 5, True, 1.5])
def test_invalid_bounds_fail_before_any_io(monkeypatch, count):
    monkeypatch.setattr(worker.store, 'sync_gaps', lambda *a, **kw: pytest.fail('DB'))
    with pytest.raises(ValueError):
        worker.run_source_research([], as_of=NOW, max_searches=count)
