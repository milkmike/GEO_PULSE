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
ACTIVATION_MIGRATION = ROOT / "scripts/migrations/043_source_activation.sql"
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
            cursor.execute("""CREATE TABLE IF NOT EXISTS sources (
                id SERIAL PRIMARY KEY,name VARCHAR(100) NOT NULL,url VARCHAR(500) NOT NULL,
                country_code CHAR(2) NOT NULL,source_type VARCHAR(20) NOT NULL,
                language VARCHAR(5),config JSONB,active BOOLEAN,tier VARCHAR(20),
                state_affiliated BOOLEAN,propaganda_risk VARCHAR(10))""")
            cursor.execute("""ALTER TABLE sources
                ADD COLUMN IF NOT EXISTS source_type VARCHAR(20),
                ADD COLUMN IF NOT EXISTS language VARCHAR(5),
                ADD COLUMN IF NOT EXISTS config JSONB,
                ADD COLUMN IF NOT EXISTS active BOOLEAN,
                ADD COLUMN IF NOT EXISTS tier VARCHAR(20),
                ADD COLUMN IF NOT EXISTS state_affiliated BOOLEAN,
                ADD COLUMN IF NOT EXISTS propaganda_risk VARCHAR(10),
                ADD COLUMN IF NOT EXISTS last_fetch_at TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS last_status VARCHAR(24),
                ADD COLUMN IF NOT EXISTS last_error TEXT,
                ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER DEFAULT 0""")
            cursor.execute("CREATE SEQUENCE IF NOT EXISTS source_research_test_source_id_seq")
            cursor.execute("ALTER TABLE sources ALTER COLUMN id SET DEFAULT nextval('source_research_test_source_id_seq')")
            cursor.execute("SELECT setval('source_research_test_source_id_seq',COALESCE((SELECT max(id) FROM sources),0)+1,false)")
            cursor.execute("""CREATE TABLE IF NOT EXISTS publisher_domains (
                domain TEXT PRIMARY KEY,publisher_source_id INTEGER NOT NULL REFERENCES sources(id),
                country_code CHAR(2) NOT NULL,status VARCHAR(16) NOT NULL,
                method VARCHAR(32) NOT NULL,confidence NUMERIC(4,3) NOT NULL,evidence JSONB NOT NULL)""")
            cursor.execute("""ALTER TABLE publisher_domains
                ADD COLUMN IF NOT EXISTS method VARCHAR(32),
                ADD COLUMN IF NOT EXISTS confidence NUMERIC(4,3),
                ADD COLUMN IF NOT EXISTS evidence JSONB,
                ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT now()""")
            cursor.execute("DROP TABLE IF EXISTS source_research_leads,source_research_tasks")
            sql = MIGRATION.read_text()
            cursor.execute(sql)
            cursor.execute(sql)  # Both migration and data/init.sql are safe to reapply.
            cursor.execute(ACTIVATION_MIGRATION.read_text())
            cursor.execute(ACTIVATION_MIGRATION.read_text())
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
    third = store.claim_discovery(as_of=NOW)
    assert third["country_code"] == "AD" and third["gap_type"] == "no_qualifying_sample_in_7d"
    assert store.claim_discovery(as_of=NOW) is None  # Other country gaps are cooling down.
    summary = store.summary(as_of=datetime.now(timezone.utc))
    assert summary["totals"]["needs_review"] == 1
    assert summary["totals"]["blocked"] == 1
    assert summary["lead_count"] == 1
    assert "website" not in str(summary)
    store.save_leads(third, [], as_of=NOW)

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


def test_activation_migration_and_default_off_interface():
    migration = (ROOT / "scripts/migrations/043_source_activation.sql").read_text()
    bootstrap = (ROOT / "data/init.sql").read_text()
    for column in ("activation_status", "activation_lease_token", "activation_next_attempt_at"):
        assert column in migration and column in bootstrap


def test_automatic_admission_is_atomic_and_does_not_override_domain(monkeypatch):
    sessions = _database(monkeypatch)
    with sessions() as session:
        session.execute(text("DELETE FROM publisher_domains WHERE domain='auto-publisher.example'"))
        session.execute(text("DELETE FROM sources WHERE url LIKE '%auto-publisher.example%'"))
    store.sync_gaps([{"code": "AD", "source_gaps": ["no_active_sources"]}], as_of=NOW)
    task = store.claim_discovery(as_of=NOW)
    assert store.save_leads(task, [_lead(website="https://auto-publisher.example/")], as_of=NOW) == 1
    first = store.claim_activation(as_of=NOW)
    assert first["country_code"].strip() == "AD"
    assert store.claim_activation(as_of=NOW) is None
    admitted = store.finish_activation(first, feed_url="https://auto-publisher.example/feed",
                                       as_of=NOW)
    assert admitted["status"] == "active"
    with pytest.raises(ValueError, match="stale"):
        store.finish_activation(first, feed_url="https://auto-publisher.example/feed", as_of=NOW)
    with sessions() as session:
        source = session.execute(text("""SELECT active,source_type,country_code,config FROM sources
            WHERE id=:id"""), {"id": admitted["source_id"]}).one()
        mapping = session.execute(text("""SELECT publisher_source_id,status,method FROM publisher_domains
            WHERE domain='auto-publisher.example'""")).one()
        assert source.active and source.source_type == "rss" and source.country_code.strip() == "AD"
        assert source.config["source_research"]["entity_id"] == "Q100"
        assert (mapping.publisher_source_id, mapping.status, mapping.method) == (
            admitted["source_id"], "verified", "wikidata_feed")

    store.sync_gaps([{"code": "AO", "source_gaps": ["no_active_sources"]}], as_of=NOW)
    task = store.claim_discovery(as_of=NOW)
    second_lead = {**_lead(code="AO", website="https://auto-publisher.example/"),
                   "entity_id": "Q101", "provenance_url": "https://www.wikidata.org/wiki/Q101"}
    assert store.save_leads(task, [second_lead], as_of=NOW) == 1
    second = store.claim_activation(as_of=NOW)
    assert store.finish_activation(second, feed_url="https://auto-publisher.example/rss",
                                   as_of=NOW)["reason"] == "collision"
    with sessions() as session:
        assert session.execute(text("SELECT count(*) FROM sources WHERE url LIKE '%auto-publisher.example%' ")).scalar_one() == 1
        mapping = session.execute(text("SELECT publisher_source_id FROM publisher_domains WHERE domain='auto-publisher.example'")).scalar_one()
        assert mapping == admitted["source_id"]


def test_broken_active_feed_gap_is_researched_without_no_sources_gap(monkeypatch):
    _database(monkeypatch)
    store.sync_gaps([{"code": "AD", "source_gaps": ["no_successful_direct_fetch"]}], as_of=NOW)
    claimed = store.claim_discovery(as_of=NOW)
    assert claimed is not None
    assert (claimed["country_code"].strip(), claimed["gap_type"]) == (
        "AD", "no_successful_direct_fetch")


def test_failed_single_same_country_rss_source_is_repaired_in_place(monkeypatch):
    sessions = _database(monkeypatch)
    domain = "repair-publisher.example"
    with sessions() as session:
        session.execute(text("DELETE FROM publisher_domains WHERE domain=:domain"), {"domain": domain})
        session.execute(text("DELETE FROM sources WHERE url LIKE :pattern"), {"pattern": f"%{domain}%"})
        source_id = session.execute(text("""INSERT INTO sources
            (name,url,country_code,source_type,language,config,active,last_status,last_error,consecutive_failures)
            VALUES('Historical Daily',:old,'AD','rss','ca',CAST(:config AS jsonb),true,'http_5xx','old failure',4)
            RETURNING id"""), {"old": f"https://{domain}/old.xml",
                              "config": '{"publisher_domain":"repair-publisher.example","custom":"keep"}'}).scalar_one()
        session.execute(text("""INSERT INTO publisher_domains
            (domain,publisher_source_id,country_code,status,method,confidence,evidence)
            VALUES(:domain,:id,'AD','verified','catalog',1,'{}'::jsonb)"""),
            {"domain": domain, "id": source_id})
    store.sync_gaps([{"code": "AD", "source_gaps": ["no_successful_direct_fetch"]}], as_of=NOW)
    task = store.claim_discovery(as_of=NOW)
    store.save_leads(task, [_lead(website=f"https://{domain}/")], as_of=NOW)
    lead = store.claim_activation(as_of=NOW)
    result = store.finish_activation(lead, feed_url=f"https://{domain}/new.xml", as_of=NOW)
    assert result == {"status": "repaired", "source_id": source_id, "reason": None}
    with sessions() as session:
        source = session.execute(text("""SELECT id,name,url,country_code,language,active,
            last_status,last_error,consecutive_failures,config FROM sources WHERE id=:id"""),
            {"id": source_id}).one()
        mapping = session.execute(text("""SELECT publisher_source_id,country_code,status,method
            FROM publisher_domains WHERE domain=:domain"""), {"domain": domain}).one()
        lead_status = session.execute(text("""SELECT activation_status,activated_source_id
            FROM source_research_leads WHERE id=:id"""), {"id": lead["id"]}).one()
        assert (source.id, source.name, source.country_code.strip(), source.language) == (
            source_id, "Historical Daily", "AD", "ca")
        assert source.url == f"https://{domain}/new.xml" and source.active
        assert (source.last_status, source.last_error, source.consecutive_failures) == (
            "pending_validation", None, 0)
        assert source.config["custom"] == "keep"
        assert source.config["source_research"]["previous_feed_url"] == f"https://{domain}/old.xml"
        assert (mapping.publisher_source_id, mapping.country_code.strip(), mapping.status,
                mapping.method) == (source_id, "AD", "verified", "wikidata_feed")
        assert (lead_status.activation_status, lead_status.activated_source_id) == ("active", source_id)
        assert session.execute(text("SELECT count(*) FROM sources WHERE url LIKE :pattern"),
                               {"pattern": f"%{domain}%"}).scalar_one() == 1


def test_healthy_same_domain_rss_source_remains_unchanged(monkeypatch):
    sessions = _database(monkeypatch)
    domain = "healthy-publisher.example"
    with sessions() as session:
        session.execute(text("DELETE FROM publisher_domains WHERE domain=:domain"), {"domain": domain})
        session.execute(text("DELETE FROM sources WHERE url LIKE :pattern"), {"pattern": f"%{domain}%"})
        source_id = session.execute(text("""INSERT INTO sources
            (name,url,country_code,source_type,active,last_status)
            VALUES('Healthy',:old,'AD','rss',true,'ok') RETURNING id"""),
            {"old": f"https://{domain}/old.xml"}).scalar_one()
    store.sync_gaps([{"code": "AD", "source_gaps": ["no_qualifying_sample_in_7d"]}], as_of=NOW)
    task = store.claim_discovery(as_of=NOW)
    store.save_leads(task, [_lead(website=f"https://{domain}/")], as_of=NOW)
    lead = store.claim_activation(as_of=NOW)
    result = store.finish_activation(lead, feed_url=f"https://{domain}/new.xml", as_of=NOW)
    assert result["status"] == "blocked" and result["reason"] == "collision"
    with sessions() as session:
        assert session.execute(text("SELECT url FROM sources WHERE id=:id"),
                               {"id": source_id}).scalar_one() == f"https://{domain}/old.xml"


@pytest.mark.parametrize("conflict", ["foreign_mapping", "blocked_mapping", "multiple_sources"])
def test_repair_rejects_ambiguous_or_foreign_publisher_assignment(monkeypatch, conflict):
    sessions = _database(monkeypatch)
    domain = f"repair-{conflict.replace('_', '-')}.example"
    with sessions() as session:
        session.execute(text("DELETE FROM publisher_domains WHERE domain=:domain"), {"domain": domain})
        session.execute(text("DELETE FROM sources WHERE url LIKE :pattern"), {"pattern": f"%{domain}%"})
        source_id = session.execute(text("""INSERT INTO sources
            (name,url,country_code,source_type,active,last_status)
            VALUES('Broken',:old,'AD','rss',true,'timeout') RETURNING id"""),
            {"old": f"https://{domain}/old.xml"}).scalar_one()
        if conflict == "multiple_sources":
            session.execute(text("""INSERT INTO sources
                (name,url,country_code,source_type,active,last_status)
                VALUES('Other',:url,'AD','rss',false,'timeout')"""),
                {"url": f"https://{domain}/other.xml"})
        else:
            session.execute(text("""INSERT INTO publisher_domains
                (domain,publisher_source_id,country_code,status,method,confidence,evidence)
                VALUES(:domain,:id,:country,:status,'catalog',1,'{}'::jsonb)"""),
                {"domain": domain, "id": source_id,
                 "country": "AO" if conflict == "foreign_mapping" else "AD",
                 "status": "blocked" if conflict == "blocked_mapping" else "verified"})
    store.sync_gaps([{"code": "AD", "source_gaps": ["no_successful_direct_fetch"]}], as_of=NOW)
    task = store.claim_discovery(as_of=NOW)
    store.save_leads(task, [_lead(website=f"https://{domain}/")], as_of=NOW)
    lead = store.claim_activation(as_of=NOW)
    result = store.finish_activation(lead, feed_url=f"https://{domain}/new.xml", as_of=NOW)
    assert result["status"] == "blocked" and result["reason"] == "collision"
    with sessions() as session:
        assert session.execute(text("SELECT url FROM sources WHERE id=:id"),
                               {"id": source_id}).scalar_one() == f"https://{domain}/old.xml"
