"""Source research queue contract; live portion requires disposable PostgreSQL."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src import source_research_store as store


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "scripts/migrations/042_source_research_queue.sql"
NOW = datetime.now(timezone.utc)


def _lead(code="AD", website="https://daily.ad/"):
    return {"provider": "wikidata", "entity_id": "Q100", "name": "Andorra Daily",
            "website": website, "provenance_url": "https://www.wikidata.org/wiki/Q100",
            "country_entity": "Q228", "claimed_country": code, "revision": 123}


def test_leads_reject_forged_country_and_unsafe_metadata():
    with pytest.raises(ValueError, match="mismatch"):
        store._lead(_lead("AO"), "AD")
    for website in ("http://127.0.0.1/private", "https://localhost/", "file:///tmp/x"):
        with pytest.raises(ValueError):
            store._lead(_lead(website=website), "AD")
    with pytest.raises(ValueError, match="provenance"):
        store._lead({**_lead(), "provenance_url": "https://example.org/claim"}, "AD")


def test_sync_rejects_missing_gap_field_before_db(monkeypatch):
    monkeypatch.setattr(store, "get_session", lambda: pytest.fail("DB must not open"))
    with pytest.raises(ValueError, match="source_gaps"):
        store.sync_gaps([{"code": "AD"}], as_of=NOW)
    with pytest.raises(ValueError, match="country gaps"):
        store.sync_gaps([{"code": "AD", "source_gaps": ["no_active_sources"]},
                         {"code": "AD", "source_gaps": []}], as_of=NOW)


def _database(monkeypatch):
    dsn = os.getenv("GEO_PULSE_TEST_DATABASE_URL")
    if not dsn or os.getenv("GEO_PULSE_TEST_DATABASE_RESET") != "1":
        pytest.skip("requires explicitly disposable PostgreSQL database")
    psycopg2 = pytest.importorskip("psycopg2")
    connection = psycopg2.connect(dsn)
    connection.autocommit = True
    try:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS source_research_leads,source_research_tasks")
            sql = MIGRATION.read_text()
            cursor.execute(sql)
            cursor.execute(sql)  # Both migration and data/init.sql are safe to reapply.
            cursor.execute("""SELECT to_regclass('source_research_tasks'),
                to_regclass('source_research_leads'),
                to_regclass('source_research_tasks_discovery_idx')""")
            assert all(cursor.fetchone())
    finally:
        connection.close()
    factory = sessionmaker(bind=create_engine(dsn))

    @contextmanager
    def sessions():
        with factory.begin() as session:
            yield session

    monkeypatch.setattr(store, "get_session", sessions)
    return sessions


def test_gap_lifecycle_fair_claim_idempotent_leads_and_recovery(monkeypatch):
    sessions = _database(monkeypatch)
    countries = [
        {"code": "AD", "source_gaps": ["no_active_sources", "no_qualifying_sample_in_7d"]},
        {"code": "AO", "source_gaps": ["no_active_sources"]},
    ]
    assert store.sync_gaps(countries, as_of=NOW)["created"] == 3
    assert store.sync_gaps(countries, as_of=NOW)["created"] == 0
    first = store.claim_discovery(as_of=NOW)
    assert first["country_code"] == "AD"
    assert first["gap_type"] == "no_active_sources"
    assert first["lease_until"] <= datetime.now(timezone.utc) + timedelta(minutes=31)
    second = store.claim_discovery(as_of=NOW)
    assert second["country_code"] == "AO"
    assert store.claim_discovery(as_of=NOW) is None
    # A monitor refresh does not revoke a live lease; a later cycle reconciles it.
    assert store.sync_gaps([{"code": "AO", "source_gaps": []}],
                           as_of=NOW + timedelta(milliseconds=1))["resolved"] == 0

    with pytest.raises(ValueError):
        store.save_leads(first, [_lead(website="http://127.0.0.1/")], as_of=NOW)
    assert store.save_leads(first, [_lead()], as_of=NOW) == 1
    with pytest.raises(ValueError, match="stale"):
        store.save_leads(first, [_lead()], as_of=NOW)
    assert store.save_leads(second, [], as_of=NOW) == 0
    assert store.claim_discovery(as_of=NOW) is None  # Seven-day cooldown.
    summary = store.summary(as_of=datetime.now(timezone.utc))
    assert summary["totals"]["needs_review"] == 1
    assert summary["totals"]["blocked"] == 1
    assert summary["lead_count"] == 1
    assert "website" not in str(summary)

    # A missing gap is resolved without losing prior research or other gap work.
    changed = [{"code": "AD", "source_gaps": ["no_qualifying_sample_in_7d"]}]
    assert store.sync_gaps(changed, as_of=NOW + timedelta(seconds=1))["resolved"] == 1
    with sessions() as session:
        assert session.execute(text("SELECT count(*) FROM source_research_leads")).scalar_one() == 1
    assert store.sync_gaps(countries, as_of=NOW + timedelta(seconds=2))["updated"] >= 1
    with sessions() as session:
        row = session.execute(text("""SELECT status,next_attempt_at FROM source_research_tasks
            WHERE country_code='AD' AND gap_type='no_active_sources'""")).one()
        assert row.status == "queued" and row.next_attempt_at > datetime.now(timezone.utc)
        session.execute(text("""UPDATE source_research_tasks SET next_attempt_at=now()-interval '1 second'
            WHERE country_code='AD' AND gap_type='no_active_sources'"""))
    retried = store.claim_discovery(as_of=NOW)
    assert retried["country_code"] == "AD"
    assert store.save_leads(retried, [_lead()], as_of=NOW) == 0  # Duplicate idempotent.

    # A crashed lease is claimable again only after it expires; old token loses.
    with sessions() as session:
        session.execute(text("""UPDATE source_research_tasks SET status='queued',next_attempt_at=NULL
            WHERE country_code='AO' AND gap_type='no_active_sources'"""))
    interrupted = store.claim_discovery(as_of=NOW)
    assert interrupted["country_code"] == "AO"
    with sessions() as session:
        session.execute(text("UPDATE source_research_tasks SET lease_until=now()-interval '1 second' WHERE id=:id"),
                        {"id": interrupted["id"]})
    recovered = store.claim_discovery(as_of=NOW)
    assert recovered["id"] == interrupted["id"]
    with pytest.raises(ValueError, match="stale"):
        store.block_task(interrupted, "unavailable", as_of=NOW)
    store.block_task(recovered, "rate_limited", as_of=NOW)
    with sessions() as session:
        row = session.execute(text("SELECT status,last_reason,attempts FROM source_research_tasks WHERE id=:id"),
                              {"id": recovered["id"]}).one()
        assert (row.status, row.last_reason, row.attempts) == ("blocked", "rate_limited", 3)


def test_bootstrap_schema_matches_migration():
    sql = MIGRATION.read_text()
    bootstrap = (ROOT / "data/init.sql").read_text()
    for table in ("source_research_tasks", "source_research_leads"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
        assert f"CREATE TABLE IF NOT EXISTS {table}" in bootstrap
