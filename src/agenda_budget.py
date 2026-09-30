"""Persistent per-campaign Jev spend accounting, committed before network I/O.

Unknown outcomes retain their full reservation. A campaign's cap is never reset
or changed by a subsequent invocation, and suspect costs halt future calls.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import re
from uuid import uuid4

from sqlalchemy import text

from src.db import get_session

RESERVATION_USD = Decimal('0.10')
MAX_CAMPAIGN_USD = Decimal('3')


def reserve_request(campaign: str, limit_usd: Decimal, payload_hash: str, pair_keys=()) -> str | None:
    """Commit one ten-cent reservation, or return None if no spend is allowed.

    Invalid configuration raises ValueError without touching the database.
    An existing campaign requires exactly its original limit; changing a process
    environment variable cannot increase or reset a persisted authorization.
    Payloads and pair keys are single-attempt per campaign, even after a crash
    or timeout. A new payload cannot rebatch an already attempted pair.
    """
    if (not isinstance(limit_usd, Decimal) or not limit_usd.is_finite()
            or not 0 < limit_usd <= MAX_CAMPAIGN_USD):
        raise ValueError('campaign limit must be a finite Decimal in (0, 3]')
    if not isinstance(campaign, str) or not campaign.strip() or len(campaign) > 200:
        raise ValueError('invalid campaign')
    if not isinstance(payload_hash, str) or re.fullmatch(r'[0-9a-f]{64}', payload_hash) is None:
        raise ValueError('invalid payload hash')
    if (not isinstance(pair_keys, (list, tuple)) or len(pair_keys) > 20
            or any(not isinstance(key, str) or re.fullmatch(r'[0-9a-f]{64}', key) is None
                   for key in pair_keys)):
        raise ValueError('invalid pair keys')
    pair_keys = sorted(set(pair_keys))
    call_id = str(uuid4())
    with get_session() as session:
        session.execute(text('''
            INSERT INTO agenda_budget (campaign, limit_usd)
            VALUES (:campaign, :limit)
            ON CONFLICT (campaign) DO NOTHING
        '''), {'campaign': campaign, 'limit': limit_usd})
        # Serialize nomination and reservation across workers. Separate statements
        # after the lock see calls committed by a concurrent holder of this row.
        session.execute(text("SELECT campaign FROM agenda_budget WHERE campaign=:campaign FOR UPDATE"),
                        {'campaign': campaign}).scalar_one()
        attempted = session.execute(text("""
            SELECT 1 FROM agenda_budget_calls
            WHERE campaign=:campaign AND
              (payload_hash=:hash OR pair_keys ?| CAST(:pair_keys AS text[]))
            LIMIT 1
        """), {'campaign': campaign, 'hash': payload_hash, 'pair_keys': pair_keys}).scalar_one_or_none()
        if attempted is not None:
            return None
        authorized = session.execute(text('''
            UPDATE agenda_budget
            SET charged_usd = charged_usd + :reservation, updated_at = now()
            WHERE campaign = :campaign AND limit_usd = :limit
              AND halted = FALSE AND charged_usd + :reservation <= limit_usd
            RETURNING campaign
        '''), {'campaign': campaign, 'limit': limit_usd, 'reservation': RESERVATION_USD}).scalar_one_or_none()
        if authorized is None:
            return None
        session.execute(text('''
            INSERT INTO agenda_budget_calls (id, campaign, payload_hash, pair_keys, charged_usd)
            VALUES (:id, :campaign, :hash, CAST(:pair_keys AS jsonb), :reservation)
        '''), {'id': call_id, 'campaign': campaign, 'hash': payload_hash,
               'reservation': RESERVATION_USD, 'pair_keys': json.dumps(pair_keys)})
    return call_id


def finish_request(call_id: str, cost: float | None, status: str) -> None:
    """Settle once; refund only successful calls with valid bounded actual cost.

    Invalid, negative, nonfinite or unexpectedly large costs halt the campaign.
    Known finite positive overages are recorded even when larger than the cap.
    Missing cost and request errors keep the full conservative charge.
    """
    actual = None
    invalid = False
    if cost is not None:
        try:
            if isinstance(cost, bool) or not isinstance(cost, (int, float, Decimal)):
                raise ValueError('invalid cost')
            actual = Decimal(str(cost))
            if not actual.is_finite() or actual < 0:
                actual = None
                invalid = True
            elif actual > RESERVATION_USD:
                invalid = True
        except (InvalidOperation, ValueError):
            invalid = True
    safe_status = status if isinstance(status, str) and re.fullmatch(r'[a-z_]{1,40}', status) else 'invalid_status'
    if safe_status == 'reserved':
        safe_status = 'invalid_status'
    with get_session() as session:
        call = session.execute(text('''
            SELECT campaign, charged_usd, finished_at
            FROM agenda_budget_calls WHERE id = :id FOR UPDATE
        '''), {'id': call_id}).mappings().one_or_none()
        if call is None or call['finished_at'] is not None:
            return
        charge = RESERVATION_USD
        if actual is not None and actual > RESERVATION_USD:
            charge = actual
        elif not invalid and safe_status == 'ok' and actual is not None:
            charge = actual
        session.execute(text('''
            UPDATE agenda_budget
            SET charged_usd = charged_usd + :adjustment,
                halted = halted OR :invalid, updated_at = now()
            WHERE campaign = :campaign
        '''), {'adjustment': charge - call['charged_usd'], 'invalid': invalid,
               'campaign': call['campaign']})
        session.execute(text('''
            UPDATE agenda_budget_calls
            SET status = :status, charged_usd = :charge, actual_cost_usd = :actual,
                cost_invalid = :invalid, finished_at = now()
            WHERE id = :id
        '''), {'id': call_id, 'status': safe_status, 'charge': charge,
               'actual': actual, 'invalid': invalid})


def get_budget(campaign: str) -> float | None:
    """Remaining authorized USD, zero when halted, None for an unknown campaign."""
    with get_session() as session:
        row = session.execute(text('''
            SELECT limit_usd, charged_usd, halted FROM agenda_budget
            WHERE campaign = :campaign
        '''), {'campaign': campaign}).mappings().one_or_none()
        if row is None:
            return None
        return 0.0 if row['halted'] else float(max(Decimal(0), row['limit_usd'] - row['charged_usd']))


def get_attempted_pair_keys(campaign: str) -> set[str]:
    """Return at most 50,000 recent attempted pairs, including unknown outcomes.

    This bounded discovery hint never weakens the exhaustive reservation guard.
    """
    with get_session() as session:
        rows = session.execute(text("""
            SELECT pair.key FROM agenda_budget_calls c
            CROSS JOIN LATERAL jsonb_array_elements_text(c.pair_keys) AS pair(key)
            WHERE c.campaign=:campaign
            ORDER BY c.created_at DESC,c.id DESC,pair.key
            LIMIT 50000
        """), {'campaign': campaign}).scalars().all()
    return set(rows)
