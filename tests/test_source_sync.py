from contextlib import contextmanager

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

import scripts.collect as collect
from src.collectors.gnews import native_feed_url, site_wrapper_url
from src.collectors.publisher_attribution import sync_publisher_domains
from src.db import PublisherDomain, Source


def _database():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE sources (
              id INTEGER PRIMARY KEY,
              name VARCHAR(100) NOT NULL,
              url VARCHAR(500) NOT NULL,
              country_code VARCHAR(2) NOT NULL,
              source_type VARCHAR(20) NOT NULL,
              weight NUMERIC(3,2),
              language VARCHAR(5),
              config JSON,
              active BOOLEAN,
              tier VARCHAR(20),
              state_affiliated BOOLEAN,
              propaganda_risk VARCHAR(10),
              created_at DATETIME
            )
        """))
        connection.execute(text("""
            CREATE TABLE publisher_domains (
              domain TEXT PRIMARY KEY,
              publisher_source_id INTEGER NOT NULL,
              country_code VARCHAR(2) NOT NULL,
              status VARCHAR(16) NOT NULL,
              method VARCHAR(32) NOT NULL,
              confidence NUMERIC(4,3) NOT NULL,
              evidence JSON NOT NULL,
              created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """))
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_registry_sync_seeds_direct_and_site_wrapper_but_not_discovery():
    engine, SessionLocal = _database()
    with SessionLocal.begin() as session:
        session.add_all(
            [
                Source(
                    id=1,
                    name="El País",
                    url="https://www.elpais.com/rss",
                    country_code="ES",
                    source_type="rss",
                    config={},
                ),
                Source(
                    id=2,
                    name="CivilNet",
                    url=site_wrapper_url("https://civilnet.am/rss", "en"),
                    country_code="AM",
                    source_type="rss",
                    config={},
                ),
                Source(
                    id=3,
                    name="Google News (ES) — Россия",
                    url=native_feed_url("ES", "es"),
                    country_code="ES",
                    source_type="rss",
                    config={"feed_mode": "publisher_discovery"},
                ),
            ]
        )

    with SessionLocal.begin() as session:
        report = sync_publisher_domains(session)

    with SessionLocal() as session:
        rows = session.scalars(select(PublisherDomain).order_by(PublisherDomain.domain)).all()

        assert [(row.domain, row.publisher_source_id, row.country_code, row.method) for row in rows] == [
            ("civilnet.am", 2, "AM", "site_wrapper"),
            ("elpais.com", 1, "ES", "catalog"),
        ]
        assert all(row.status == "verified" and float(row.confidence) == 1.0 for row in rows)
        assert report.verified == 2
        assert report.blocked == 0
        assert report.skipped == 1
    engine.dispose()


def test_registry_sync_skips_invalid_direct_and_site_domains():
    engine, SessionLocal = _database()
    invalid_direct_urls = [
        "http://localhost/feed",
        "https://com/feed",
        "http://127.0.0.1/feed",
        "http://[::1]/feed",
        "https://bad_domain.example/feed",
        "https://-bad.example/feed",
    ]
    invalid_site_domains = ["localhost", "com", "127.0.0.1", "bad_domain.example"]
    with SessionLocal.begin() as session:
        session.add_all(
            [
                Source(
                    id=index,
                    name=f"Invalid direct {index}",
                    url=url,
                    country_code="ES",
                    source_type="rss",
                    config={},
                )
                for index, url in enumerate(invalid_direct_urls, start=1)
            ]
            + [
                Source(
                    id=index,
                    name=f"Invalid site {index}",
                    url=(
                        "https://news.google.com/rss/search?"
                        f"q=site:{domain}+russia&hl=en-US&gl=US&ceid=US:en"
                    ),
                    country_code="ES",
                    source_type="rss",
                    config={},
                )
                for index, domain in enumerate(invalid_site_domains, start=101)
            ]
        )

    with SessionLocal.begin() as session:
        report = sync_publisher_domains(session)

    with SessionLocal() as session:
        assert session.scalars(select(PublisherDomain)).all() == []
        assert report.verified == 0
        assert report.blocked == 0
        assert report.skipped == len(invalid_direct_urls) + len(invalid_site_domains)
    engine.dispose()


def test_registry_sync_blocks_cross_country_domain_conflict_idempotently():
    engine, SessionLocal = _database()
    with SessionLocal.begin() as session:
        session.add_all(
            [
                Source(
                    id=10,
                    name="Shared ES",
                    url="https://www.shared.example/rss",
                    country_code="ES",
                    source_type="rss",
                    config={},
                ),
                Source(
                    id=20,
                    name="Shared ME",
                    url="https://rss.shared.example/feed",
                    country_code="ME",
                    source_type="rss",
                    config={},
                ),
            ]
        )

    with SessionLocal.begin() as session:
        first = sync_publisher_domains(session)
    with SessionLocal.begin() as session:
        second = sync_publisher_domains(session)

    with SessionLocal() as session:
        rows = session.scalars(select(PublisherDomain)).all()

        assert len(rows) == 1
        assert rows[0].domain == "shared.example"
        assert rows[0].status == "blocked"
        assert rows[0].publisher_source_id == 10
        assert [candidate["source_id"] for candidate in rows[0].evidence["candidates"]] == [10, 20]
        assert first.blocked == second.blocked == 1
    engine.dispose()


def test_registry_sync_never_repromotes_blocked_existing_mapping():
    engine, SessionLocal = _database()
    with SessionLocal.begin() as session:
        session.add(
            Source(
                id=20,
                name="Current ME",
                url="https://shared.example/feed",
                country_code="ME",
                source_type="rss",
                config={},
            )
        )
        session.add(
            PublisherDomain(
                domain="shared.example",
                publisher_source_id=10,
                country_code="ES",
                status="verified",
                method="catalog",
                confidence=1.0,
                evidence={
                    "candidate": {
                        "domain": "shared.example",
                        "source_id": 10,
                        "source_name": "Historical ES",
                        "source_url": "https://shared.example/old-feed",
                        "country_code": "ES",
                        "method": "catalog",
                        "explicit_alias": False,
                    }
                },
            )
        )

    snapshots = []
    reports = []
    for _ in range(3):
        with SessionLocal.begin() as session:
            reports.append(sync_publisher_domains(session))
        with SessionLocal() as session:
            row = session.get(PublisherDomain, "shared.example")
            snapshots.append(
                (
                    row.status,
                    row.publisher_source_id,
                    row.country_code,
                    [
                        (candidate["source_id"], candidate["country_code"])
                        for candidate in row.evidence["candidates"]
                    ],
                )
            )

    assert [report.blocked for report in reports] == [1, 1, 1]
    assert snapshots == [
        ("blocked", 10, "ES", [(10, "ES"), (20, "ME")]),
        ("blocked", 10, "ES", [(10, "ES"), (20, "ME")]),
        ("blocked", 10, "ES", [(10, "ES"), (20, "ME")]),
    ]
    engine.dispose()


def test_source_sync_refreshes_metadata_for_url_and_name_matches(monkeypatch):
    engine, SessionLocal = _database()
    old_wrapper = "https://dead.example/civilnet.xml"
    new_wrapper = site_wrapper_url("https://civilnet.am/rss", "en")
    discovery_url = native_feed_url("ES", "es")
    with SessionLocal.begin() as session:
        session.add_all(
            [
                Source(
                    id=1,
                    name="El País",
                    url="https://www.elpais.com/rss",
                    country_code="ES",
                    source_type="web",
                    weight=0.5,
                    language="en",
                    config={"stale": True},
                    active=False,
                    tier="analytics",
                    state_affiliated=True,
                    propaganda_risk="high",
                ),
                Source(
                    id=2,
                    name="CivilNet",
                    url=old_wrapper,
                    country_code="AM",
                    source_type="rss",
                    language="ru",
                    config={"stale": True},
                    active=False,
                    tier="mainstream",
                    state_affiliated=True,
                    propaganda_risk="high",
                ),
                Source(
                    id=3,
                    name="Google News (ES) — Россия",
                    url=discovery_url,
                    country_code="ES",
                    source_type="rss",
                    language="en",
                    config={},
                    active=True,
                    tier="analytics",
                    state_affiliated=True,
                    propaganda_risk="high",
                ),
            ]
        )

    config = {
        "countries": {
            "ES": {
                "sources": [
                    {
                        "name": "El País",
                        "url": "https://www.elpais.com/rss",
                        "type": "rss",
                        "weight": 1.4,
                        "language": "es",
                        "config": {"publisher_domain": "elpais.com"},
                        "tier": "independent",
                        "state_affiliated": False,
                        "propaganda_risk": "low",
                    },
                    {
                        "name": "Google News (ES) — Россия",
                        "url": discovery_url,
                        "type": "rss",
                        "language": "es",
                        "config": {
                            "feed_mode": "publisher_discovery",
                            "discovery_country": "ES",
                            "provider": "google_news",
                        },
                        "tier": "mainstream",
                        "state_affiliated": False,
                        "propaganda_risk": "low",
                    },
                ]
            },
            "AM": {
                "sources": [
                    {
                        "name": "CivilNet",
                        "url": new_wrapper,
                        "type": "rss",
                        "weight": 1.3,
                        "language": "hy",
                        "config": {"feed_mode": "site_wrapper"},
                        "tier": "independent",
                        "state_affiliated": False,
                        "propaganda_risk": "medium",
                    }
                ]
            },
        }
    }

    @contextmanager
    def session_scope():
        with SessionLocal() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    monkeypatch.setattr(collect, "get_session", session_scope)
    monkeypatch.setattr(collect, "load_sources", lambda: config)
    monkeypatch.setattr(collect, "SKIP_YAML_SYNC", False)

    collect.ensure_sources_in_db()

    with SessionLocal() as session:
        elpais = session.get(Source, 1)
        civilnet = session.get(Source, 2)
        discovery = session.get(Source, 3)
        domains = session.scalars(select(PublisherDomain).order_by(PublisherDomain.domain)).all()

        assert (
            elpais.source_type,
            float(elpais.weight),
            elpais.language,
            elpais.config,
            elpais.active,
            elpais.tier,
            elpais.state_affiliated,
            elpais.propaganda_risk,
        ) == (
            "rss",
            1.4,
            "es",
            {"publisher_domain": "elpais.com"},
            True,
            "independent",
            False,
            "low",
        )
        assert civilnet.url == new_wrapper
        assert (civilnet.language, civilnet.config, civilnet.active, civilnet.tier) == (
            "hy",
            {"feed_mode": "site_wrapper"},
            True,
            "independent",
        )
        assert (civilnet.state_affiliated, civilnet.propaganda_risk) == (False, "medium")
        assert discovery.config["feed_mode"] == "publisher_discovery"
        assert discovery.language == "es"
        assert [(row.domain, row.status) for row in domains] == [
            ("civilnet.am", "verified"),
            ("elpais.com", "verified"),
        ]
    engine.dispose()
