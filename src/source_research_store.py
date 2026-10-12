"""Durable private research queue for monitor coverage gaps.

Monitor timestamps describe the observed coverage snapshot. Research, leases,
and retry dates are database wall-clock timestamps, never replayed snapshot time.
"""
from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text

from src.db import get_session
from src.monitoring_registry import MONITORING_COUNTRIES
from src.signal_hypotheses import _url
from src.signal_workbench import instant
from src.collectors.publisher_attribution import feed_mode, is_aggregator_domain, normalize_publisher_domain

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
    """Claim one due country gap, favouring countries not recently researched."""
    instant(as_of)
    token = uuid4()
    with get_session() as session:
        row = session.execute(text("""
            WITH candidate AS (
              SELECT task.id FROM source_research_tasks task
              WHERE ((task.status='queued' AND (task.next_attempt_at IS NULL OR task.next_attempt_at<=now()))
                  OR (task.status='researching' AND task.lease_until<=now()))
                AND NOT EXISTS (SELECT 1 FROM source_research_tasks live
                  WHERE live.country_code=task.country_code AND live.id<>task.id
                    AND live.status='researching' AND live.lease_until>now())
              ORDER BY (SELECT max(prior.attempted_at) FROM source_research_tasks prior
                         WHERE prior.country_code=task.country_code) NULLS FIRST,
                CASE task.gap_type WHEN 'no_active_sources' THEN 0
                  WHEN 'no_successful_direct_fetch' THEN 1 ELSE 2 END,
                task.country_code,task.id
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


def claim_activation(*, as_of):
    """Claim one old or new lead, preferring countries least recently attempted."""
    instant(as_of)
    token = uuid4()
    with get_session() as session:
        row = session.execute(text("""
            WITH candidate AS (
              SELECT lead.id FROM source_research_leads lead
              WHERE ((lead.activation_status IN ('queued','blocked')
                       AND (lead.activation_next_attempt_at IS NULL OR lead.activation_next_attempt_at<=now()))
                  OR (lead.activation_status='validating' AND lead.activation_lease_until<=now()))
                AND EXISTS (SELECT 1 FROM source_research_tasks task
                    WHERE task.country_code=lead.country_code AND task.status<>'resolved')
              ORDER BY (lead.activation_attempted_at IS NOT NULL),
                       lead.activation_attempted_at NULLS FIRST,lead.country_code,lead.id
              LIMIT 1 FOR UPDATE SKIP LOCKED
            )
            UPDATE source_research_leads lead SET activation_status='validating',
              activation_attempted_at=now(),activation_lease_until=now()+interval '2 minutes',
              activation_lease_token=:token,activation_attempts=activation_attempts+1,
              activation_last_reason=NULL
            FROM candidate WHERE lead.id=candidate.id
            RETURNING lead.id,lead.country_code,lead.name,lead.website,
              lead.provenance_url,lead.entity_id,lead.revision,
              lead.activation_lease_token
        """), {"token": token}).mappings().first()
    return dict(row) if row else None


def _activation_claim(lead):
    if not isinstance(lead, dict) or type(lead.get("id")) is not int or lead["id"] <= 0:
        raise ValueError("invalid activation lead")
    _country(lead.get("country_code", "").strip())
    token = lead.get("activation_lease_token")
    if not token:
        raise ValueError("missing activation lease")
    return {"id": lead["id"], "token": str(token)}


def finish_activation(lead, *, feed_url=None, reason=None, as_of):
    """Atomically admit a proven same-domain feed or cool down a failed lead.

    A domain lock serializes automatic admissions. A same-country mapping may
    receive fresh evidence for the same source ID; assignments never change.
    """
    instant(as_of)
    claim = _activation_claim(lead)
    if (feed_url is None) == (reason is None):
        raise ValueError("feed or reason required")
    if reason is not None and reason not in {"unsafe", "unavailable", "no_valid_feed", "collision"}:
        raise ValueError("invalid activation reason")
    domain = normalize_publisher_domain(lead.get("website"))
    if (not domain or is_aggregator_domain(domain)
            or (feed_url is not None and normalize_publisher_domain(feed_url) != domain)):
        raise ValueError("feed domain mismatch")
    if feed_url is not None and len(feed_url) > 500:
        raise ValueError("feed URL too long")
    with get_session() as session:
        live = session.execute(text("""
            SELECT country_code,website,entity_id,provenance_url,name,revision
            FROM source_research_leads
            WHERE id=:id AND activation_lease_token=CAST(:token AS uuid)
              AND activation_status='validating' AND activation_lease_until>now()
            FOR UPDATE
        """), claim).mappings().first()
        if not live:
            raise ValueError("stale activation lease")
        if (live["website"] != lead.get("website") or live["country_code"].strip() != lead.get("country_code", "").strip()
                or live["entity_id"] != lead.get("entity_id")):
            raise ValueError("activation lead mismatch")
        source_id = None
        outcome = "blocked"
        if feed_url is not None:
            session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:domain))"), {"domain": domain})
            mapping = session.execute(text("""
                SELECT publisher_source_id,country_code,status FROM publisher_domains
                WHERE domain=:domain
            """), {"domain": domain}).mappings().first()
            sources = session.execute(text("""SELECT id,url,country_code,source_type,config,
                active,last_status FROM sources""")).mappings().all()
            matches = []
            for source in sources:
                config = source["config"] if isinstance(source["config"], dict) else {}
                aliases = config.get("publisher_domain_aliases", config.get("publisher_aliases", ()))
                if isinstance(aliases, str):
                    aliases = [aliases]
                if not isinstance(aliases, (list, tuple, set)):
                    aliases = []
                related = [source["url"], config.get("publisher_domain"), *aliases]
                if any(normalize_publisher_domain(value) == domain for value in related if isinstance(value, str)):
                    matches.append(source)
            if not matches and mapping is None:
                config = {"publisher_domain": domain,
                          "source_research": {"provider": "wikidata", "entity_id": live["entity_id"],
                              "revision": live["revision"], "website": live["website"],
                              "provenance_url": live["provenance_url"],
                              "validated_at": datetime.now(timezone.utc).isoformat()}}
                source_id = session.execute(text("""
                    INSERT INTO sources(name,url,country_code,source_type,language,config,
                                        active,tier,state_affiliated,propaganda_risk)
                    VALUES(:name,:feed,:country,'rss','und',CAST(:config AS jsonb),
                           true,'mainstream',false,'medium') RETURNING id
                """), {"name": live["name"][:100], "feed": feed_url,
                       "country": live["country_code"], "config": json.dumps(config)}).scalar_one()
                session.execute(text("""
                    INSERT INTO publisher_domains(domain,publisher_source_id,country_code,
                      status,method,confidence,evidence)
                    VALUES(:domain,:source,:country,'verified','wikidata_feed',1,
                           CAST(:evidence AS jsonb))
                """), {"domain": domain, "source": source_id,
                       "country": live["country_code"],
                       "evidence": json.dumps(config["source_research"])} )
                outcome = "active"
            elif len(matches) == 1:
                current = session.execute(text("""SELECT id,url,country_code,source_type,config,
                    active,last_status FROM sources WHERE id=:id FOR UPDATE"""),
                    {"id": matches[0]["id"]}).mappings().one()
                mapping = session.execute(text("""
                    SELECT publisher_source_id,country_code,status FROM publisher_domains
                    WHERE domain=:domain FOR UPDATE
                """), {"domain": domain}).mappings().first()
                existing_config = current["config"] if isinstance(current["config"], dict) else {}
                mapping_ok = (mapping is None or
                    (mapping["status"] == "verified"
                     and mapping["publisher_source_id"] == current["id"]
                     and mapping["country_code"].strip() == live["country_code"].strip()))
                broken = (current["active"] is False or
                          current["last_status"] not in (None, "ok", "empty_feed", "pending_validation"))
                if (mapping_ok and current["country_code"].strip() == live["country_code"].strip()
                        and current["source_type"] == "rss"
                        and normalize_publisher_domain(current["url"]) == domain
                        and feed_mode(current["url"], existing_config) == "publisher"
                        and current["url"] != feed_url and broken):
                    evidence = {"provider": "wikidata", "entity_id": live["entity_id"],
                        "revision": live["revision"], "website": live["website"],
                        "provenance_url": live["provenance_url"],
                        "validated_at": datetime.now(timezone.utc).isoformat(),
                        "previous_feed_url": current["url"]}
                    config = {**existing_config, "publisher_domain": domain,
                              "source_research": evidence}
                    source_id = current["id"]
                    session.execute(text("""UPDATE sources SET url=:feed,
                        config=CAST(:config AS jsonb),active=true,
                        last_status='pending_validation',last_error=NULL,
                        consecutive_failures=0 WHERE id=:id"""),
                        {"feed": feed_url, "config": json.dumps(config), "id": source_id})
                    if mapping is None:
                        session.execute(text("""INSERT INTO publisher_domains
                          (domain,publisher_source_id,country_code,status,method,confidence,evidence)
                          VALUES(:domain,:source,:country,'verified','wikidata_feed',1,
                                 CAST(:evidence AS jsonb))"""),
                            {"domain": domain, "source": source_id,
                             "country": live["country_code"], "evidence": json.dumps(evidence)})
                    else:
                        session.execute(text("""UPDATE publisher_domains
                            SET method='wikidata_feed',confidence=1,
                                evidence=CAST(:evidence AS jsonb),updated_at=now()
                            WHERE domain=:domain"""),
                            {"domain": domain, "evidence": json.dumps(evidence)})
                    outcome = "repaired"
                else:
                    reason = "collision"
            else:
                reason = "collision"
        lead_status = "active" if outcome in ("active", "repaired") else "blocked"
        session.execute(text("""
            UPDATE source_research_leads SET activation_status=:status,
              activation_next_attempt_at=CASE WHEN :status='active' THEN NULL
                  ELSE now()+interval '7 days' END,
              activation_last_reason=:reason,activated_source_id=:source,
              activation_lease_until=NULL,activation_lease_token=NULL
            WHERE id=:id
        """), {"id": claim["id"], "status": lead_status,
               "reason": reason, "source": source_id})
    return {"status": outcome, "source_id": source_id, "reason": reason}
