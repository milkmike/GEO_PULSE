"""A country-independent monitor remains useful when paid stages are disabled."""
from datetime import datetime, timezone
from decimal import Decimal

from src import global_signal_monitor as monitor

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def setup(monkeypatch):
    events = []
    snapshot = {'as_of': NOW.isoformat(), 'countries': [
        {'code': 'RS', 'name_ru': 'Сербия', 'sampled_articles_7d': 1},
        {'code': 'ET', 'name_ru': 'Эфиопия', 'sampled_articles_7d': 0},
        {'code': 'AD', 'name_ru': 'Андорра', 'sampled_articles_7d': 0}],
        'articles': [], 'limits': {'counts_are_bounded': True}}
    monkeypatch.setattr(monitor, 'load_global_monitoring', lambda **kw: snapshot)
    monkeypatch.setattr(monitor, 'run_source_research', lambda countries, **kw: {'status': 'ok', 'countries_attempted': 0})
    monkeypatch.setattr(monitor.store, 'current_screenings', lambda articles: [])
    monkeypatch.setattr(monitor, 'plan_candidates', lambda records, articles, **kw: [])
    monkeypatch.setattr(monitor.store, 'known_work_keys', lambda: set())
    monkeypatch.setattr(monitor.store, 'sync_candidates', lambda rows, **kw: events.append(('queue', rows)) or 0)
    monkeypatch.setattr(monitor.store, 'queue_summary', lambda **kw: {})
    monkeypatch.setattr(monitor.store, 'save_monitor_run', lambda payload: events.append(('save', payload)))
    return events


def test_unpaid_monitor_updates_every_country_without_models(monkeypatch):
    events = setup(monkeypatch)
    monkeypatch.setattr(monitor, 'run_screening_cycle', lambda **kw: (_ for _ in ()).throw(AssertionError('paid call')))
    monkeypatch.setattr(monitor, 'write_draft', lambda *a, **kw: (_ for _ in ()).throw(AssertionError('writer')))
    result = monitor.run_global_cycle(as_of=NOW)
    assert result['screening']['status'] == 'disabled'
    assert [row['code'] for row in events[-1][1]['countries']] == ['RS', 'ET', 'AD']
    assert result['scope_count'] == 3


def test_screening_outage_does_not_erase_coverage_or_cached_leads(monkeypatch):
    events = setup(monkeypatch)
    def fail(**kw):
        raise OSError('secret or provider body must not enter public report')
    monkeypatch.setattr(monitor, 'run_screening_cycle', fail)
    result = monitor.run_global_cycle(as_of=NOW, budget_usd=Decimal('2'))
    assert result['screening'] == {'status': 'blocked', 'reason': 'screening_preflight_failed'}
    assert any(name == 'queue' for name, _ in events)
    assert events[-1][0] == 'save' and len(events[-1][1]['countries']) == 3
    assert 'secret' not in str(result)


def test_writer_is_optional_and_never_releases_a_dossier(monkeypatch):
    events = setup(monkeypatch)
    monkeypatch.setattr(monitor, 'run_screening_cycle', lambda **kw: {'status': 'ok'})
    claimed = {'id': 'a' * 64, 'context': {'as_of': NOW.isoformat(), 'articles': []}}
    monkeypatch.setattr(monitor.store, 'claim_context', lambda **kw: claimed)
    monkeypatch.setattr(monitor, 'write_draft', lambda *a, **kw: {'draft': {'a': 1}})
    monkeypatch.setattr(monitor.store, 'save_private_draft', lambda item, result: events.append(('private', (item, result))))
    result = monitor.run_global_cycle(as_of=NOW, budget_usd=Decimal('2'), max_drafts=1)
    assert result['writer']['drafts'] == 1
    assert any(name == 'private' for name, _ in events)


def test_provider_failure_stops_writer_batch_and_retains_error_state(monkeypatch):
    events = setup(monkeypatch)
    monkeypatch.setattr(monitor, 'run_screening_cycle', lambda **kw: {'status': 'ok'})
    monkeypatch.setattr(monitor.store, 'claim_context', lambda **kw: {'id': 'a' * 64, 'context': {}})
    def fail(*a, **kw):
        raise ValueError('invalid quote containing sensitive article text')
    monkeypatch.setattr(monitor, 'write_draft', fail)
    monkeypatch.setattr(monitor.store, 'block_work', lambda item, reason: events.append(('blocked', reason)))
    result = monitor.run_global_cycle(as_of=NOW, budget_usd=Decimal('2'), max_drafts=2)
    assert result['writer']['status'] == 'blocked' and result['writer']['calls'] == 1
    assert ('blocked', 'draft_failed') in events


def test_bad_bounds_fail_before_reading_country_data(monkeypatch):
    import pytest
    monkeypatch.setattr(monitor, 'load_global_monitoring', lambda **kw: (_ for _ in ()).throw(AssertionError('DB read')))
    for kwargs in ({'budget_usd': Decimal('20')}, {'max_drafts': 3}, {'max_screen_calls': 0}, {'max_source_searches': 5}, {'auto_sources': 'yes'}):
        with pytest.raises(ValueError):
            monitor.run_global_cycle(**kwargs)


def test_source_research_failure_cannot_erase_global_coverage(monkeypatch):
    events = setup(monkeypatch)
    def fail(*args, **kwargs):
        raise OSError('untrusted remote body')
    monkeypatch.setattr(monitor, 'run_source_research', fail)
    result = monitor.run_global_cycle(as_of=NOW)
    assert result['source_research'] == {'status': 'blocked', 'reason': 'research_unavailable'}
    assert events[-1][0] == 'save' and result['scope_count'] == 3
    assert 'untrusted' not in str(result)


def test_automatic_sources_continue_when_models_are_disabled(monkeypatch):
    events = setup(monkeypatch)
    def research(countries, *, as_of, max_searches, auto_activate):
        return {'status': 'ok', 'sources_activated': int(auto_activate),
                'countries_attempted': 1, 'feeds_validated': 1}
    monkeypatch.setattr(monitor, 'run_source_research', research)
    monkeypatch.setattr(monitor, 'run_screening_cycle', lambda **kw: (_ for _ in ()).throw(AssertionError('paid call')))
    result = monitor.run_global_cycle(as_of=NOW, auto_sources=True)
    assert result['source_research']['sources_activated'] == 1
    assert result['source_activation']['enabled'] is True
    assert result['screening']['status'] == 'disabled'
    assert len(events[-1][1]['countries']) == 3


def test_analysis_counts_are_per_local_sample_not_event_country(monkeypatch):
    setup(monkeypatch)
    monkeypatch.setattr(monitor.store, 'current_screenings', lambda articles: [
        {'article': {'id': 1, 'country_code': 'RS'},
         'classification': {'country': 'ET', 'signal': 'none'}}])
    result = monitor.run_global_cycle(as_of=NOW)
    rows = {row['code']: row for row in result['countries']}
    assert rows['RS']['screened_articles_7d'] == 1
    assert rows['RS']['analysis_state'] == 'sample_complete'
    assert rows['ET']['screened_articles_7d'] == 0
    assert rows['ET']['analysis_state'] == 'no_sample'


def test_monitor_uses_configured_fixed_campaign(monkeypatch):
    setup(monkeypatch)
    monkeypatch.setenv('EARLY_SIGNAL_CAMPAIGN', 'early-signals-2026-10-12-world')
    calls = []
    monkeypatch.setattr(monitor, 'run_screening_cycle', lambda **kw: calls.append(kw) or {'status': 'ok'})
    monitor.run_global_cycle(as_of=NOW, budget_usd=Decimal('2'))
    assert calls[0]['campaign'] == 'early-signals-2026-10-12-world'


def test_local_observation_gets_background_translation_in_same_budget(monkeypatch):
    setup(monkeypatch)
    article = {'id': 1, 'country_code': 'RS', 'title': 'New university exchange'}
    monkeypatch.setattr(monitor.store, 'current_screenings', lambda articles: [
        {'article': article, 'classification': {'signal': 'change', 'mechanism': 'education',
                                              'stage': 'proposal', 'country': 'RS'}}])
    monkeypatch.setattr(monitor, 'run_screening_cycle', lambda **kw: {'status': 'ok'})
    calls = []
    monkeypatch.setattr(monitor, 'run_translation_cycle', lambda **kw: calls.append(kw) or {'translated': 1})
    result = monitor.run_global_cycle(as_of=NOW, budget_usd=Decimal('2'))
    assert calls[0]['articles'] == [article]
    assert calls[0]['max_calls'] == 1 and calls[0]['budget_usd'] == Decimal('2')
    assert result['translations']['translated'] == 1


def test_cli_automatic_source_flag_uses_environment_and_explicit_override(monkeypatch):
    import sys
    from scripts import build_early_signals as cli
    calls = []
    monkeypatch.setenv('AUTO_SOURCE_ONBOARDING', '1')
    monkeypatch.setattr(cli, 'locked_global_cycle', lambda **kw: calls.append(kw) or {'status': 'ok'})
    monkeypatch.setattr(sys, 'argv', ['build_early_signals.py', 'monitor', '--budget-usd', '0'])
    cli.main()
    assert calls[-1]['auto_sources'] is True and calls[-1]['budget_usd'] == 0
    monkeypatch.setattr(sys, 'argv', ['build_early_signals.py', 'monitor', '--no-auto-sources'])
    cli.main()
    assert calls[-1]['auto_sources'] is False
