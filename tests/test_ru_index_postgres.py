"""RRI replay against an isolated PostgreSQL database bootstrapped from init.sql.

Uses GEO_PULSE_TEST_DATABASE_URL + GEO_PULSE_TEST_DATABASE_RESET=1, like Radar.
"""
from datetime import datetime, timedelta, timezone
import json

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from contextlib import contextmanager

from tests.test_radar_cycle_postgres import radar_engine
from scripts.recompute_ru_index import RECOMPUTE_LOCK, recompute_ru_index
from src.engine import ru_index as rri, signals


NOW = datetime.now(timezone.utc).replace(microsecond=0)


@pytest.fixture(autouse=True)
def isolated_redis(monkeypatch):
    # A scratch database must never invalidate an operator's real Redis cache.
    from src import queue
    monkeypatch.setattr(queue, 'get_redis', lambda: type('Redis', (), {
        'delete': lambda self, key: None})())


def seed(engine):
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO countries(code, name_ru, name_en, iso3, region)
            VALUES ('BA', 'Босния и Герцеговина', 'Bosnia', 'BIH', 'europe'),
                   ('BR', 'Бразилия', 'Brazil', 'BRA', 'americas'),
                   ('NZ', 'Новая Зеландия', 'New Zealand', 'NZL', 'oceania')
        """))
        for code, source in [('BA', 'temperature'), ('BR', 'temperature'), ('NZ', 'gdelt')]:
            for days in [7, 1, 0]:
                # BA: a single article appeared after structural-only snapshots.
                no_media = code == 'BA' and days > 0
                structural = 5.4 if code != 'NZ' else -20
                media = None if no_media else (-66.7 if code == 'BA' else (-10 if code == 'BR' else 0))
                count = 1 if code == 'BA' and not no_media else (20 if code == 'BR' else None)
                score = structural if media is None else round(.6 * structural + .4 * media, 2)
                conn.execute(text("""
                    INSERT INTO ru_index(time, country_code, score, structural, media, boost,
                        level, delta_24h, delta_7d, version, article_count, details)
                    VALUES (:at, :cc, :score, :structural, :media, 0, :level, -28.84, -28.84,
                            'v1', :count, CAST(:details AS jsonb))
                """), {'at': NOW - timedelta(days=days), 'cc': code, 'score': score,
                        'structural': structural, 'media': media, 'level': rri.level_for(score),
                        'count': count, 'details': json.dumps({
                            'media': {'source': 'none' if no_media else source},
                            'structural': {'kept': True}, 'weights': {'structural': .6, 'media': .4},
                        })})
        # Empty volume is evidence of no observations, even with a zero tone.
        conn.execute(text("""
            INSERT INTO gdelt_daily(day, country_code, tone_avg, volume)
            VALUES (:day, 'NZ', 0, 0), (:future, 'NZ', 10, 100)
        """), {'day': NOW.date(), 'future': NOW.date() + timedelta(days=1)})


def rows(engine):
    with engine.connect() as conn:
        return [dict(r) for r in conn.execute(text('SELECT * FROM ru_index ORDER BY country_code, time')).mappings()]


def test_backup_replay_deltas_cache_and_idempotence(radar_engine, monkeypatch):
    seed(radar_engine)
    original = rows(radar_engine)
    refreshes = []
    report = recompute_ru_index(engine=radar_engine, dry_run=True,
                                refresh_cache=lambda: refreshes.append(True))
    assert report['rows'] == 9 and report['countries'] == 3
    assert report['countries_changing_level'] == 2
    assert report['largest_current_changes'][0]['country_code'] == 'BA'
    assert report['largest_current_changes'][0]['new_score'] == 3.96
    assert rows(radar_engine) == original
    with radar_engine.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('ru_index_v1_backup')")).scalar() is None

    from src import queue
    deletes = []
    monkeypatch.setattr(queue, 'get_redis', lambda: type('Redis', (), {
        'delete': lambda self, key: deletes.append(key)})())
    actual = recompute_ru_index(engine=radar_engine, refresh_cache=lambda: refreshes.append(True))
    assert actual['rows'] == 9
    assert actual['countries_changing_level'] == report['countries_changing_level']
    assert refreshes == [True] and deletes == ['cache:v2:countries']
    updated = rows(radar_engine)
    assert all(r['version'] == 'v1.1' for r in updated)
    with radar_engine.connect() as conn:
        backup = [dict(r) for r in conn.execute(text(
            'SELECT * FROM ru_index_v1_backup ORDER BY country_code, time')).mappings()]
    assert backup == original
    by_code = {code: [r for r in updated if r['country_code'] == code] for code in ['BA', 'BR', 'NZ']}
    bosnia = by_code['BA'][-1]
    assert float(bosnia['score']) == 3.96
    assert float(bosnia['delta_24h']) == -1.44
    assert float(bosnia['delta_7d']) == -1.44
    assert bosnia['details']['low_data'] is True
    assert bosnia['details']['structural'] == {'kept': True}
    assert bosnia['details']['media']['articles'] == 1
    assert bosnia['details']['weights']['media'] == pytest.approx(.02)
    for country_rows in by_code.values():
        assert country_rows[0]['delta_24h'] is None
        assert country_rows[0]['delta_7d'] is None
    assert by_code['BR'][-1]['score'] == original[5]['score']
    assert by_code['NZ'][-1]['details']['media']['confidence'] == 0
    assert by_code['NZ'][-1]['details']['media']['days_with_volume'] == 0
    assert float(by_code['NZ'][-1]['score']) == -20
    assert by_code['NZ'][-1]['details']['low_data'] is True

    # The old -28.84 delta would have emitted shifts after TTL expiry. Replayed
    # deltas are small, so no artificial signal is produced, regardless of TTL.
    with Session(radar_engine) as session:
        assert signals.detect_index_shifts(session) == 0
    repeat = recompute_ru_index(engine=radar_engine, refresh_cache=lambda: None)
    assert repeat['rows'] == 0 and repeat['countries'] == 0
    assert rows(radar_engine) == updated
    with radar_engine.connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM ru_index_v1_backup')).scalar() == 9


def test_advisory_lock_prevents_parallel_replay(radar_engine):
    seed(radar_engine)
    with radar_engine.connect() as lock:
        lock.execute(text('SELECT pg_advisory_lock(:key)'), {'key': RECOMPUTE_LOCK})
        try:
            assert recompute_ru_index(engine=radar_engine, refresh_cache=lambda: None) is None
            assert all(row['version'] == 'v1' for row in rows(radar_engine))
        finally:
            lock.execute(text('SELECT pg_advisory_unlock(:key)'), {'key': RECOMPUTE_LOCK})
    assert recompute_ru_index(engine=radar_engine, refresh_cache=lambda: None)['rows'] == 9


def test_live_history_really_excludes_other_versions(radar_engine, monkeypatch):
    seed(radar_engine)

    @contextmanager
    def session_factory():
        with Session(radar_engine) as session:
            yield session

    monkeypatch.setattr(rri, 'get_session', session_factory)
    monkeypatch.setattr(rri, 'structural_baseline', lambda *args: (5.4, {}))
    monkeypatch.setattr(rri, 'event_boost', lambda *args: (0, {}))
    monkeypatch.setattr(rri, 'media_component', lambda *args: (-66.7, {
        'source': 'temperature', 'article_count': 1, **rri.media_evidence('temperature', articles=1)}))
    point = rri.calculate_ru_index('BA')
    assert point['delta_24h'] is None and point['delta_7d'] is None
    recompute_ru_index(engine=radar_engine, refresh_cache=lambda: None)
    point = rri.calculate_ru_index('BA')
    assert point['delta_24h'] == -1.44 and point['delta_7d'] == -1.44


def test_country_rollback_keeps_backup_and_releases_lock(radar_engine, monkeypatch):
    from scripts import recompute_ru_index as replay
    seed(radar_engine)
    original = rows(radar_engine)
    replay_row = replay._replay_row

    def fail_at_last_bosnia_row(row, *args):
        if row.country_code == 'BA' and row.time == NOW:
            raise RuntimeError('injected replay failure')
        return replay_row(row, *args)

    # Earlier batches must roll back together with the failing country's row.
    monkeypatch.setattr(replay, 'BATCH_SIZE', 1)
    monkeypatch.setattr(replay, '_replay_row', fail_at_last_bosnia_row)
    with pytest.raises(RuntimeError, match='injected replay failure'):
        replay.recompute_ru_index(engine=radar_engine, refresh_cache=lambda: None)
    assert rows(radar_engine) == original
    with radar_engine.connect() as conn:
        backup = [dict(r) for r in conn.execute(text(
            'SELECT * FROM ru_index_v1_backup ORDER BY country_code, time')).mappings()]
    assert backup == original
    monkeypatch.setattr(replay, '_replay_row', replay_row)
    assert replay.recompute_ru_index(engine=radar_engine, refresh_cache=lambda: None)['rows'] == 9


def test_country_outside_registry_is_skipped_not_fatal(radar_engine):
    seed(radar_engine)
    with radar_engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO ru_index(time, country_code, score, structural, media, boost,
                level, version, details)
            VALUES (:at, 'ZZ', 1, 1, NULL, 0, 'neutral', 'v1', '{}'::jsonb)
        """), {'at': NOW})

    report = recompute_ru_index(engine=radar_engine, refresh_cache=lambda: None)

    assert report['rows'] == 9 and report['countries'] == 3
    versions = {(r['country_code'], r['version']) for r in rows(radar_engine)}
    assert ('ZZ', 'v1') in versions
    assert {v for code, v in versions if code != 'ZZ'} == {'v1.1'}
