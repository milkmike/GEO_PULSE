"""Budget regressions; real PostgreSQL tests use AGENDA_BUDGET_TEST_DATABASE_URL."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from decimal import Decimal
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src import agenda_budget as budget


@pytest.mark.parametrize('limit', [Decimal('0'), Decimal('-1'), Decimal('3.01'), Decimal('NaN'), Decimal('Infinity')])
def test_invalid_campaign_cap_rejected_before_database(limit):
    with pytest.raises(ValueError):
        budget.reserve_request('campaign', limit, 'a' * 64)


@pytest.mark.parametrize('reservation', [Decimal('0'), Decimal('-0.01'), Decimal('0.100001'),
                                          Decimal('NaN'), Decimal('Infinity'), Decimal('-Infinity'),
                                          .01, '0.01', None])
def test_invalid_reservation_rejected_before_database(reservation):
    with pytest.raises(ValueError):
        budget.reserve_request('campaign', Decimal('3'), 'a' * 64,
                               reservation_usd=reservation)


@pytest.fixture
def ledger(monkeypatch):
    url = os.environ.get('AGENDA_BUDGET_TEST_DATABASE_URL') or os.environ.get('GEO_PULSE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('requires explicit isolated PostgreSQL test database')
    schema = f'agenda_budget_test_{uuid4().hex}'
    admin_engine = create_engine(url)
    with admin_engine.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA {schema}')
    engine = create_engine(url, pool_size=12, max_overflow=12,
                           connect_args={'options': f'-csearch_path={schema}'})
    with engine.begin() as conn:
        conn.exec_driver_sql(Path('scripts/migrations/033_agenda_budget.sql').read_text())
    @contextmanager
    def session():
        with Session(engine) as db:
            with db.begin():
                yield db
    monkeypatch.setattr(budget, 'get_session', session)
    campaign = f'test-{uuid4()}'
    yield campaign, engine
    engine.dispose()
    with admin_engine.begin() as conn:
        conn.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
    admin_engine.dispose()


def reserve(campaign, cap='0.30'):
    return budget.reserve_request(campaign, Decimal(cap), uuid4().hex * 2)


def test_committed_reservation_and_actual_cost_refund_are_idempotent(ledger):
    campaign, engine = ledger
    assert budget.get_budget(campaign) is None
    call = reserve(campaign)
    assert call
    assert budget.get_budget(campaign) == pytest.approx(.20)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT status FROM agenda_budget_calls WHERE id = :id'), {'id': call}).scalar_one() == 'reserved'
    budget.finish_request(call, .025, 'ok')
    assert budget.get_budget(campaign) == pytest.approx(.275)
    budget.finish_request(call, .001, 'ok')
    assert budget.get_budget(campaign) == pytest.approx(.275)


@pytest.mark.parametrize('cost,status', [(None, 'timeout'), (None, 'ok'), (.01, 'provider_error')])
def test_unknown_or_failed_request_retains_full_reservation(ledger, cost, status):
    campaign, _ = ledger
    call = reserve(campaign)
    budget.finish_request(call, cost, status)
    assert budget.get_budget(campaign) == pytest.approx(.20)


@pytest.mark.parametrize('cost,status', [(None, 'timeout'), (None, 'ok'),
                                         (.001, 'provider_error')])
def test_small_reservation_unknown_or_failed_request_retains_original(ledger, cost, status):
    campaign, engine = ledger
    call = budget.reserve_request(campaign, Decimal('0.02'), 'a' * 64,
                                  reservation_usd=Decimal('0.01'))
    assert call
    budget.finish_request(call, cost, status)
    assert budget.get_budget(campaign) == pytest.approx(.01)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT charged_usd FROM agenda_budget_calls WHERE id=:id'),
                            {'id': call}).scalar_one() == Decimal('0.01')


def test_small_reservation_success_refunds_and_overage_halts(ledger):
    campaign, engine = ledger
    call = budget.reserve_request(campaign, Decimal('0.03'), 'a' * 64,
                                  reservation_usd=Decimal('0.01'))
    budget.finish_request(call, Decimal('0.0024'), 'ok')
    assert budget.get_budget(campaign) == pytest.approx(.0276)
    over = budget.reserve_request(campaign, Decimal('0.03'), 'b' * 64,
                                  reservation_usd=Decimal('0.01'))
    assert over
    budget.finish_request(over, Decimal('0.011'), 'ok')
    assert budget.get_budget(campaign) == 0
    assert budget.reserve_request(campaign, Decimal('0.03'), 'c' * 64,
                                  reservation_usd=Decimal('0.01')) is None
    with engine.connect() as conn:
        row = conn.execute(text('SELECT charged_usd, actual_cost_usd, cost_invalid '
                                'FROM agenda_budget_calls WHERE id=:id'), {'id': over}).one()
        assert row == (Decimal('0.011'), Decimal('0.011'), True)


@pytest.mark.parametrize('cost', [-.01, float('nan'), float('inf'), .11, True])
def test_invalid_or_over_reservation_cost_halts_campaign(ledger, cost):
    campaign, engine = ledger
    call = reserve(campaign)
    budget.finish_request(call, cost, 'ok')
    assert budget.get_budget(campaign) == 0
    assert reserve(campaign) is None
    if cost == .11:
        with engine.connect() as conn:
            assert conn.execute(text('SELECT charged_usd FROM agenda_budget WHERE campaign = :campaign'), {'campaign': campaign}).scalar_one() == Decimal('.11')


def test_campaign_cannot_be_reset_or_increased(ledger):
    campaign, _ = ledger
    assert reserve(campaign, '.10')
    assert reserve(campaign, '3') is None
    assert reserve(campaign, '.10') is None
    assert budget.get_budget(campaign) == 0


def test_concurrent_reservations_do_not_exceed_cap(ledger):
    campaign, engine = ledger
    with ThreadPoolExecutor(max_workers=12) as pool:
        calls = list(pool.map(lambda _: reserve(campaign), range(30)))
    assert sum(call is not None for call in calls) == 3
    assert budget.get_budget(campaign) == 0
    with engine.connect() as conn:
        assert conn.execute(text('SELECT SUM(charged_usd) FROM agenda_budget_calls WHERE campaign = :campaign'), {'campaign': campaign}).scalar_one() == Decimal('.30')


def test_concurrent_small_reservations_do_not_exceed_cap(ledger):
    campaign, engine = ledger
    with ThreadPoolExecutor(max_workers=12) as pool:
        calls = list(pool.map(lambda _: budget.reserve_request(
            campaign, Decimal('0.09'), uuid4().hex * 2,
            reservation_usd=Decimal('0.01')), range(30)))
    assert sum(call is not None for call in calls) == 9
    assert budget.get_budget(campaign) == 0
    with engine.connect() as conn:
        assert conn.execute(text('SELECT SUM(charged_usd) FROM agenda_budget_calls '
                                 'WHERE campaign=:campaign'), {'campaign': campaign}).scalar_one() == Decimal('0.09')


def test_concurrent_finish_refunds_once(ledger):
    campaign, _ = ledger
    call = reserve(campaign)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: budget.finish_request(call, 0, 'ok'), range(16)))
    assert budget.get_budget(campaign) == pytest.approx(.30)


@pytest.mark.parametrize('status', ['reserved', 'timeout', 'ok'])
def test_attempted_pairs_are_not_paid_twice_even_when_rebatched(ledger, status):
    campaign, _ = ledger
    first_key, second_key = 'a' * 64, 'b' * 64
    call = budget.reserve_request(campaign, Decimal('3'), 'c' * 64, pair_keys=[first_key])
    assert call
    if status != 'reserved':
        budget.finish_request(call, 0 if status == 'ok' else None, status)
    before = budget.get_budget(campaign)
    assert budget.reserve_request(campaign, Decimal('3'), 'd' * 64,
                                  pair_keys=[first_key, second_key]) is None
    assert budget.get_budget(campaign) == before
    assert budget.get_attempted_pair_keys(campaign) == {first_key}
    assert budget.reserve_request(campaign, Decimal('3'), 'e' * 64, pair_keys=[second_key])
    assert budget.get_attempted_pair_keys(campaign) == {first_key, second_key}


def test_identical_payload_is_not_paid_twice_without_pair_metadata(ledger):
    campaign, _ = ledger
    call = budget.reserve_request(campaign, Decimal('3'), 'a' * 64)
    assert call
    budget.finish_request(call, 0, 'ok')
    assert budget.reserve_request(campaign, Decimal('3'), 'a' * 64) is None
    assert budget.get_budget(campaign) == 3


def test_concurrent_rebatched_requests_only_reserve_shared_pair_once(ledger):
    campaign, engine = ledger
    with ThreadPoolExecutor(max_workers=12) as pool:
        calls = list(pool.map(lambda _: budget.reserve_request(
            campaign, Decimal('3'), uuid4().hex * 2, pair_keys=['a' * 64]), range(24)))
    assert sum(call is not None for call in calls) == 1
    assert budget.get_budget(campaign) == pytest.approx(2.9)
    with engine.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM agenda_budget_calls')).scalar_one() == 1


def test_attempt_history_is_scoped_to_campaign(ledger):
    campaign, _ = ledger
    assert budget.reserve_request(campaign, Decimal('3'), 'a' * 64, pair_keys=['b' * 64])
    assert budget.get_attempted_pair_keys('not-the-campaign') == set()


@pytest.mark.parametrize('pair_keys', ['a' * 64, [None], ['bad'], ['a' * 64] * 21])
def test_invalid_pair_metadata_rejected_before_database(pair_keys):
    with pytest.raises(ValueError):
        budget.reserve_request('campaign', Decimal('3'), 'a' * 64, pair_keys=pair_keys)


def test_attempt_history_keeps_only_latest_fifty_thousand_keys(ledger):
    campaign, engine = ledger
    with engine.begin() as conn:
        conn.execute(text('INSERT INTO agenda_budget(campaign,limit_usd) VALUES(:campaign,3)'), {'campaign': campaign})
        conn.execute(text('''
            INSERT INTO agenda_budget_calls(id,campaign,payload_hash,pair_keys,created_at)
            SELECT CAST(md5(batch::text) AS uuid),:campaign,
                repeat(md5(batch::text),2),
                (SELECT jsonb_agg(lpad(pair_no::text,64,'0'))
                 FROM generate_series((batch-1)*20+1,batch*20) AS pair_no),
                now()+make_interval(secs=>batch)
            FROM generate_series(1,2501) AS batch
        '''), {'campaign': campaign})
    keys = budget.get_attempted_pair_keys(campaign)
    assert len(keys) == 50000
    assert str(50020).zfill(64) in keys
    assert str(21).zfill(64) in keys
    assert str(20).zfill(64) not in keys
    # A key outside the bounded discovery hint must still be protected by the
    # exhaustive database reservation check.
    assert budget.reserve_request(campaign, Decimal('3'), 'f' * 64,
                                  pair_keys=[str(1).zfill(64)]) is None
