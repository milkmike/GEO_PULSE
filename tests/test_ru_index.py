"""RRI v1.1 confidence, versioned deltas and public evidence regressions."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace as Row

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.countries import COUNTRIES
from src.engine import ru_index as rri
from src.engine.explanations import RriPoint, decompose_rri_shift
from src.api.routes import world

NOW = datetime.now(timezone.utc)


class Result:
    def __init__(self, rows=()):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class Session:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((str(sql), params))
        return Result(next(self.responses))


@contextmanager
def session_context(session):
    yield session


@pytest.mark.parametrize('count,confidence', [(0, 0), (1, .05), (5, .25), (20, 1), (40, 1)])
def test_temperature_confidence_from_live_reading(count, confidence):
    session = Session([Row(temperature=-66.7, article_count=count, time=NOW)])
    media, details = rri.media_component(COUNTRIES['BA'], session)
    assert media == -66.7
    assert details == {'source': 'temperature', 'article_count': count,
                       'articles': count, 'days_with_volume': None, 'confidence': confidence}


@pytest.mark.parametrize('days', range(8))
def test_gdelt_confidence_counts_days_with_positive_volume(days):
    rows = [Row(day=NOW.date() - timedelta(days=i), tone_avg=-2,
                volume=3 if i < days else 0) for i in range(7)]
    # An old temperature must fall back to GDELT.
    session = Session([Row(temperature=100, article_count=40, time=NOW - timedelta(days=3))], rows)
    media, details = rri.media_component(COUNTRIES['BA'], session)
    assert media == pytest.approx(-24)
    assert details['days_with_volume'] == days
    assert details['confidence'] == pytest.approx(days / 7)
    assert details['articles'] is None
    sql, params = session.calls[1]
    assert 'day > :window_end - 7 AND day <= :window_end' in sql
    assert params['window_end'] == NOW.date()


def test_gdelt_count_excludes_null_tone_old_future_and_duplicate_days():
    def day(offset, tone=-1, volume=1):
        return Row(day=NOW.date() + timedelta(days=offset), tone_avg=tone, volume=volume)
    rows = [day(-7), day(1), day(-6), day(-6), day(-5, None), day(-4, volume=None),
            day(-3, volume=-1), day(0)]
    assert rri.gdelt_days_with_volume(rows, NOW) == 2


@pytest.mark.parametrize('tier,media_weight', [(1, .55), (2, .4)])
@pytest.mark.parametrize('confidence', [0, .05, .25, 1])
def test_effective_weights_and_boost(tier, media_weight, confidence):
    country = {**COUNTRIES['BA'], 'tier': tier}
    score, weights, rule = rri.score_components(country, 5.4, -66.7, 3, confidence)
    assert weights['media'] == pytest.approx(media_weight * confidence)
    assert sum(weights.values()) == pytest.approx(1)
    assert score == round((1 - media_weight * confidence) * 5.4 - media_weight * confidence * 66.7 + 3, 2)
    assert rule is None


def snapshot(monkeypatch, media=-66.7, articles=1):
    session = Session([])
    monkeypatch.setattr(rri, 'get_session', lambda: session_context(session))
    monkeypatch.setattr(rri, 'structural_baseline', lambda *args: (5.4, {}))
    monkeypatch.setattr(rri, 'media_component', lambda *args: (media, {
        'source': 'temperature' if media is not None else 'none', 'article_count': articles,
        **rri.media_evidence('temperature' if media is not None else 'none', articles=articles),
    }))
    monkeypatch.setattr(rri, 'event_boost', lambda *args: (0, {}))
    return rri.calculate_ru_index('BA'), session


def test_bosnia_sparse_reading_and_version_filtered_history(monkeypatch):
    point, session = snapshot(monkeypatch)
    assert point['score'] == 3.96  # v1: -23.44
    assert point['version'] == 'v1.1'
    assert point['details']['low_data'] is True
    assert point['details']['weights']['media'] == pytest.approx(.02)
    assert point['delta_24h'] is None and point['delta_7d'] is None
    sql, params = session.calls[0]
    assert 'AND version = :version' in sql
    assert params['version'] == 'v1.1'


@pytest.mark.parametrize('articles', [20, 40])
def test_full_evidence_preserves_v1_score(monkeypatch, articles):
    point, _ = snapshot(monkeypatch, articles=articles)
    assert point['score'] == round(.6 * 5.4 + .4 * -66.7, 2)
    assert point['details']['low_data'] is False


@pytest.mark.parametrize('articles,low_data', [(0, True), (4, True), (5, False)])
def test_low_data_threshold(monkeypatch, articles, low_data):
    point, _ = snapshot(monkeypatch, articles=articles)
    assert point['details']['low_data'] is low_data


def test_no_media_is_structural_without_low_data(monkeypatch):
    point, _ = snapshot(monkeypatch, media=None, articles=None)
    assert point['score'] == 5.4
    assert point['details']['low_data'] is False
    assert point['details']['weights'] == {'structural': 1., 'media': 0.}
    assert rri.score_components(COUNTRIES['BA'], 5.4, None, 15, 0)[0] == 5.4


def test_missing_media_reading():
    assert rri.media_component(COUNTRIES['BA'], Session([], [])) == (
        None, {'source': 'none', 'articles': None, 'days_with_volume': None, 'confidence': 0})


@pytest.mark.parametrize('code,structural,media,expected,rule', [
    ('UA', 0, 100, -75, 'war_cap'), ('BY', -100, -100, 25, 'union_state_floor'),
    ('FR', 0, 100, -5, 'sanctions_cap'), ('KZ', -100, -100, -25, 'csto_floor'),
])
def test_hard_bounds_unchanged(code, structural, media, expected, rule):
    score, _, bound = rri.score_components(COUNTRIES[code], structural, media, 0, 1)
    assert (score, bound) == (expected, rule)


def test_deltas_use_nearest_eligible_history_in_eight_days():
    history = [(NOW - timedelta(hours=h), value) for h, value in [
        (200, -100), (168, 1), (126, 2), (125, 3), (24, 4), (18, 6), (17, 7), (0, 99), (-1, 100),
    ]]
    assert rri.deltas_from_history(NOW, 10, history) == (4, 8)
    assert rri.deltas_from_history(NOW, 10, []) == (None, None)
    assert rri.deltas_from_history(NOW, 10, [(NOW - timedelta(hours=18), 6)]) == (4, None)
    assert rri.deltas_from_history(NOW, 10, [(NOW - timedelta(days=8), 6)]) == (None, None)


def test_v11_explanation_reconciles_changing_effective_weights():
    def point(at, articles):
        evidence = rri.media_evidence('temperature', articles=articles)
        score, weights, _ = rri.score_components(COUNTRIES['BA'], 5.4, -66.7, 0, evidence['confidence'])
        return RriPoint('BA', at, score, 5.4, -66.7, 0, 'v1.1', articles, None,
                        {'weights': weights, 'media': evidence})
    exact = decompose_rri_shift(point(NOW - timedelta(days=1), 1), point(NOW, 20))
    assert exact['total_delta'] == pytest.approx(-27.4)
    assert sum(exact[key] for key in ('structural_delta', 'media_delta', 'boost_delta',
                                    'calculation_adjustment_delta', 'rounding_residual')) == pytest.approx(exact['total_delta'])


@pytest.mark.parametrize('low_data,confidence', [(True, .05), (False, 1), (False, None)])
def test_countries_api_and_warm_cache_share_evidence(monkeypatch, low_data, confidence):
    details = {} if confidence is None else {'media': {'confidence': confidence}, 'low_data': low_data}
    row = Row(country_code='BA', score=3.96, structural=5.4, media=-66.7,
              level='neutral', delta_24h=None, delta_7d=None, article_count=1,
              gdelt_volume=None, gdelt_tone=None, time=NOW, details=details)
    monkeypatch.setattr(world, 'get_session', lambda: session_context(Session([row])))
    monkeypatch.setattr(rri, 'get_session', lambda: session_context(Session([row])))
    monkeypatch.setattr(world, '_cache_get', lambda *args: None)
    app = FastAPI()
    app.include_router(world.router)
    response = TestClient(app).get('/api/v2/countries')
    assert response.status_code == 200
    item = response.json()['countries'][0]
    assert (item['low_data'], item['media_confidence']) == (low_data, confidence)
    from src import queue
    cached = {}
    monkeypatch.setattr(queue, 'get_redis', lambda: Row(setex=lambda key, ttl, val: cached.update(json.loads(val))))
    rri.warm_cache()
    assert cached['countries'] == [item]
    monkeypatch.setattr(world, '_cache_get', lambda *args: cached)
    assert TestClient(app).get('/api/v2/countries').json()['countries'] == [item]


def test_dossier_returns_media_evidence(monkeypatch):
    row = Row(score=3.96, structural=5.4, media=-66.7, boost=0,
              level='neutral', delta_24h=None, delta_7d=None, time=NOW, version='v1.1',
              details={'media': {'confidence': .05}, 'low_data': True})
    monkeypatch.setattr(world, 'get_session', lambda: session_context(Session([row], [], [], [], [])))
    monkeypatch.setattr(world, 'load_signal_article_previews', lambda *args, **kwargs: {})
    app = FastAPI()
    app.include_router(world.router)
    response = TestClient(app).get('/api/v2/countries/BA')
    assert response.status_code == 200
    assert response.json()['index']['media_confidence'] == .05
    assert response.json()['index']['low_data'] is True


def test_worker_waits_for_replay_before_calculating(monkeypatch):
    from scripts import calc_ru_index as worker
    calls = []
    reports = iter([None, {'rows': 9}])
    monkeypatch.setattr('sys.argv', ['calc_ru_index.py'])
    monkeypatch.setattr(worker, 'wait_for_db', lambda: calls.append('db'))
    monkeypatch.setattr(worker, 'ensure_countries_in_db', lambda: calls.append('registry'))
    monkeypatch.setattr(worker, 'recompute_ru_index', lambda: (calls.append('replay'), next(reports))[1])
    monkeypatch.setattr(worker.time, 'sleep', lambda seconds: calls.append(('sleep', seconds)))
    monkeypatch.setattr(worker, 'calc_all', lambda: calls.append('calculate'))
    worker.main()
    assert calls == ['db', 'registry', 'replay', ('sleep', 5), 'replay', 'calculate']


def test_worker_keeps_calculating_when_replay_fails(monkeypatch):
    from scripts import calc_ru_index as worker
    calls = []

    def failing_replay():
        calls.append('replay')
        raise RuntimeError('replay broke')

    monkeypatch.setattr('sys.argv', ['calc_ru_index.py'])
    monkeypatch.setattr(worker, 'wait_for_db', lambda: calls.append('db'))
    monkeypatch.setattr(worker, 'ensure_countries_in_db', lambda: calls.append('registry'))
    monkeypatch.setattr(worker, 'recompute_ru_index', failing_replay)
    monkeypatch.setattr(worker, 'calc_all', lambda: calls.append('calculate'))
    worker.main()
    assert calls == ['db', 'registry', 'replay', 'calculate']


def test_replay_uses_saved_inputs_and_historical_gdelt_window():
    from scripts.recompute_ru_index import _replay_row
    country = COUNTRIES['BA']
    row = Row(country_code='BA', time=NOW, score=-23.44, structural=5.4,
              media=-66.7, boost=0, article_count=1,
              details={'media': {'source': 'temperature'}, 'bound_rule': 'old_rule'})
    replayed = _replay_row(row, country, [], [(NOW - timedelta(days=1), 5.4)])
    assert replayed['score'] == 3.96 and replayed['delta_24h'] == -1.44
    details = json.loads(replayed['details'])
    assert 'bound_rule' not in details
    assert details['media']['articles'] == 1 and details['low_data'] is True
    gdelt_row = Row(**{**vars(row), 'media': -24, 'article_count': None,
                      'details': {'media': {'source': 'gdelt'}}})
    days = [Row(day=NOW.date() + timedelta(days=offset), tone_avg=-2, volume=1)
            for offset in [-7, -6, 0, 1]]
    replayed = _replay_row(gdelt_row, country, days, [])
    details = json.loads(replayed['details'])
    assert details['media']['days_with_volume'] == 2
    assert details['media']['confidence'] == pytest.approx(2 / 7)
    assert details['media']['articles'] is None
    assert replayed['score'] == rri.score_components(country, 5.4, -24, 0, 2 / 7)[0]


@pytest.mark.parametrize("structural,media", [(5.4, -14.5), (15, -26.9), (25, -56.3), (50, -98.1)])
def test_full_tier1_evidence_preserves_v1_rounding(structural, media):
    country = {**COUNTRIES['BA'], 'tier': 1}
    score, weights, _ = rri.score_components(country, structural, media, 0, 1)
    assert score == round(.45 * structural + .55 * media, 2)
    assert weights == {'structural': .45, 'media': .55}
