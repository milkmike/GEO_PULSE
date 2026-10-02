"""Durable private research queue for monitor coverage gaps.

Monitor timestamps describe the observed coverage snapshot. Research, leases,
and retry dates are database wall-clock timestamps, never replayed snapshot time.
"""
from __future__ import annotations

import re
from uuid import uuid4

from sqlalchemy import text

from src.db import get_session
from src.monitoring_registry import MONITORING_COUNTRIES
from src.signal_hypotheses import _url
from src.signal_workbench import instant

GAP_TYPES = frozenset({
    "no_active_sources", "no_successful_direct_fetch", "no_qualifying_sample_in_7d",
})
STATUSES = ("queued", "researching", "needs_review", "blocked", "resolved")
BLOCK_REASONS = frozenset({"no_leads", "rate_limited", "unavailable", "ambiguous_country"})
_ENTITY = re.compile(r"Q[1-9][0-9]*\Z")
_REVISION = re.compile(r"[1-9][0-9]*\Z")


def _country(value):
    if not isinstance(value, str) or value not in MONITORING_COUNTRIES:
        raise ValueError("invalid monitored country")
    return value


def _lead(value, country):
    if not isinstance(value, dict) or value.get("provider") != "wikidata":
        raise ValueError("invalid lead provider")
    if value.get("claimed_country") != country:
        raise ValueError("lead country mismatch")
    entity = value.get("entity_id")
    country_entity = value.get("country_entity")
    if not isinstance(entity, str) or not _ENTITY.fullmatch(entity):
        raise ValueError("invalid lead entity")
    if not isinstance(country_entity, str) or not _ENTITY.fullmatch(country_entity):
        raise ValueError("invalid lead country entity")
    name = value.get("name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 240:
        raise ValueError("invalid lead name")
    revision = value.get("revision")
    if revision is not None:
        revision = str(revision)
        if not _REVISION.fullmatch(revision) or len(revision) > 24:
            raise ValueError("invalid lead revision")
    website = _url(value.get("website"))
    provenance = _url(value.get("provenance_url"))
    if len(website) > 2048 or len(provenance) > 2048:
        raise ValueError("lead URL too long")
    if provenance != f"https://www.wikidata.org/wiki/{entity}":
        raise ValueError("invalid Wikidata provenance")
    return {"provider": "wikidata", "entity_id": entity, "name": name.strip(),
            "website": website, "provenance_url": provenance,
            "country_entity": country_entity, "claimed_country": country,
            "revision": revision}


def sync_gaps(countries, *, as_of):
    """Reconcile only supplied country rows; omitted countries stay untouched."""
    observed = instant(as_of)
    if not isinstance(countries, list) or len(countries) > len(MONITORING_COUNTRIES):
        raise ValueError("invalid country snapshot")
    by_country = {}
    for row in countries:
        if not isinstance(row, dict) or "source_gaps" not in row:
            raise ValueError("source_gaps required")
        code = _country(row.get("code"))
        gaps = row["source_gaps"]
        if (code in by_country or not isinstance(gaps, list) or len(gaps) > len(GAP_TYPES)
                or any(not isinstance(gap, str) for gap in gaps)
                or len(set(gaps)) != len(gaps) or not set(gaps) <= GAP_TYPES):
            raise ValueError("invalid country gaps")
        by_country[code] = set(gaps)
    counts = {"created": 0, "updated": 0, "resolved": 0}
    with get_session() as session:
        for code, gaps in by_country.items():
            for gap in sorted(gaps):
                row = session.execute(text("""
                    INSERT INTO source_research_tasks(country_code,gap_type,status,
                      gap_observed_at,last_observed_at)
                    VALUES(:country,:gap,'queued',:observed,:observed)
                    ON CONFLICT(country_code,gap_type) DO UPDATE SET
                      last_observed_at=EXCLUDED.last_observed_at,
                      gap_observed_at=CASE WHEN source_research_tasks.status='resolved'
                        THEN EXCLUDED.gap_observed_at ELSE source_research_tasks.gap_observed_at END,
                      status=CASE
                        WHEN source_research_tasks.status='resolved' THEN 'queued'
                        WHEN source_research_tasks.status IN ('blocked','needs_review')
                          AND source_research_tasks.next_attempt_at<=now() THEN 'queued'
                        ELSE source_research_tasks.status END,
                      updated_at=now()
                    WHERE source_research_tasks.last_observed_at<=EXCLUDED.last_observed_at
                    RETURNING (xmax=0) AS created
                """), {"country": code, "gap": gap, "observed": observed}).mappings().first()
                if row:
                    counts["created" if row["created"] else "updated"] += 1
            resolved = session.execute(text("""
                UPDATE source_research_tasks SET status='resolved',lease_until=NULL,
                  lease_token=NULL,last_observed_at=:observed,updated_at=now()
                WHERE country_code=:country AND NOT (gap_type=ANY(CAST(:gaps AS text[])))
                  AND last_observed_at<=:observed AND status<>'resolved'
                  AND NOT (status='researching' AND lease_until>now())
                RETURNING id
            """), {"country": code, "gaps": list(gaps), "observed": observed}).all()
            counts["resolved"] += len(resolved)
    return counts


def claim_discovery(*, as_of):
    """Claim one due missing-source country, recovering expired leases."""
    instant(as_of)
    token = uuid4()
    with get_session() as session:
        row = session.execute(text("""
            WITH candidate AS (
              SELECT id FROM source_research_tasks
              WHERE gap_type='no_active_sources'
                AND ((status='queued' AND (next_attempt_at IS NULL OR next_attempt_at<=now()))
                  OR (status='researching' AND lease_until<=now()))
              ORDER BY (attempted_at IS NOT NULL),attempted_at NULLS FIRST,country_code,id
              LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            UPDATE source_research_tasks AS task SET status='researching',
              attempted_at=now(),lease_until=now()+interval '30 minutes',
              lease_token=:token,attempts=attempts+1,last_reason=NULL,updated_at=now()
            FROM candidate WHERE task.id=candidate.id
            RETURNING task.id,task.country_code,task.gap_type,task.lease_token,
              task.lease_until,task.attempts,task.gap_observed_at
        """), {"token": token}).mappings().first()
    return dict(row) if row else None


def _claim(task):
    if not isinstance(task, dict) or type(task.get("id")) is not int or task["id"] <= 0:
        raise ValueError("invalid research task")
    _country(task.get("country_code"))
    token = task.get("lease_token")
    if not token:
        raise ValueError("missing lease token")
    return {"id": task["id"], "country": task["country_code"], "token": str(token)}


def save_leads(task, leads, *, as_of):
    """Finish a live claim; duplicate leads remain one unverified candidate."""
    instant(as_of)
    claim = _claim(task)
    if not isinstance(leads, list) or len(leads) > 5:
        raise ValueError("at most five leads")
    clean = [_lead(lead, claim["country"]) for lead in leads]
    if len({(lead["entity_id"], lead["website"]) for lead in clean}) != len(clean):
        raise ValueError("duplicate leads in response")
    with get_session() as session:
        updated = session.execute(text("""
            UPDATE source_research_tasks SET status=:status,researched_at=now(),
              next_attempt_at=now()+interval '7 days',last_reason=:reason,
              lease_until=NULL,lease_token=NULL,updated_at=now()
            WHERE id=:id AND country_code=:country AND lease_token=CAST(:token AS uuid)
              AND status='researching' AND lease_until>now()
            RETURNING id
        """), {**claim, "status": "needs_review" if clean else "blocked",
                "reason": None if clean else "no_leads"}).first()
        if not updated:
            raise ValueError("stale research lease")
        inserted = 0
        for lead in clean:
            added = session.execute(text("""
                INSERT INTO source_research_leads(task_id,country_code,provider,entity_id,
                  name,website,provenance_url,country_entity,revision,claimed_country)
                VALUES(:id,:country,:provider,:entity_id,:name,:website,:provenance_url,
                  :country_entity,:revision,:claimed_country)
                ON CONFLICT(country_code,provider,entity_id,website) DO NOTHING
                RETURNING id
            """), {**claim, **lead}).first()
            inserted += int(added is not None)
    return inserted


def block_task(task, reason, *, as_of):
    """Record only a content-free failure code and a seven-day cooldown."""
    instant(as_of)
    claim = _claim(task)
    if reason not in BLOCK_REASONS:
        raise ValueError("invalid research failure code")
    with get_session() as session:
        changed = session.execute(text("""
            UPDATE source_research_tasks SET status='blocked',researched_at=now(),
              next_attempt_at=now()+interval '7 days',last_reason=:reason,
              lease_until=NULL,lease_token=NULL,updated_at=now()
            WHERE id=:id AND country_code=:country AND lease_token=CAST(:token AS uuid)
              AND status='researching' AND lease_until>now()
            RETURNING id
        """), {**claim, "reason": reason}).first()
        if not changed:
            raise ValueError("stale research lease")


def summary(*, as_of):
    """Private aggregate: never return candidate websites or provider text."""
    observed = instant(as_of)
    with get_session() as session:
        rows = session.execute(text("""
            SELECT country_code,status,count(*) AS n FROM source_research_tasks
            WHERE last_observed_at<=:observed GROUP BY country_code,status
        """), {"observed": observed}).mappings().all()
        lead_count = session.execute(text("SELECT count(*) FROM source_research_leads")).scalar_one()
    countries = {}
    totals = {status: 0 for status in STATUSES}
    for row in rows:
        count = int(row["n"])
        countries.setdefault(row["country_code"].strip(), {})[row["status"]] = count
        totals[row["status"]] += count
    return {"as_of": observed.isoformat(), "totals": totals,
            "countries": countries, "lead_count": int(lead_count)}
