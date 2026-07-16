from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

import scripts.collect as collect
from src.db import Article, ArticleDiscovery, PublisherDomain, Source


PUBLISHED_AT = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)


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
        connection.execute(text("""
            CREATE TABLE articles (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              source_id INTEGER NOT NULL,
              external_id TEXT,
              title TEXT,
              body TEXT,
              summary TEXT,
              url TEXT,
              published_at DATETIME NOT NULL,
              collected_at DATETIME,
              language VARCHAR(5),
              title_normalized TEXT,
              is_duplicate BOOLEAN DEFAULT FALSE,
              duplicate_of INTEGER,
              reprint_count INTEGER DEFAULT 0,
              is_backfill BOOLEAN DEFAULT FALSE,
              publisher_source_id INTEGER,
              publisher_name TEXT,
              publisher_url TEXT,
              publisher_domain TEXT,
              geo_country_code VARCHAR(2),
              geo_status VARCHAR(24) NOT NULL DEFAULT 'source_verified',
              geo_method VARCHAR(40),
              geo_confidence NUMERIC(4,3),
              geo_verified_at DATETIME,
              resolved_url TEXT,
              UNIQUE(source_id, external_id)
            )
        """))
        connection.execute(text("""
            CREATE TABLE article_discoveries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              discovery_source_id INTEGER NOT NULL,
              external_id TEXT NOT NULL,
              title TEXT,
              body TEXT,
              google_url TEXT,
              published_at DATETIME NOT NULL,
              feed_country_code VARCHAR(2) NOT NULL,
              publisher_name TEXT,
              publisher_url TEXT,
              publisher_domain TEXT,
              geo_status VARCHAR(24) NOT NULL DEFAULT 'unverified',
              reason TEXT,
              raw_metadata JSON NOT NULL,
              discovered_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
              promoted_article_id INTEGER,
              UNIQUE(discovery_source_id, external_id)
            )
        """))
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _article(external_id, publisher_name, publisher_url, publisher_domain):
    return {
        "external_id": external_id,
        "title": f"Material suficientemente largo de {publisher_name}",
        "body": "Rusia y las relaciones internacionales.",
        "url": f"https://news.google.com/rss/articles/{external_id}",
        "published_at": PUBLISHED_AT,
        "publisher_name": publisher_name,
        "publisher_url": publisher_url,
        "publisher_domain": publisher_domain,
        "raw_source": {"title": publisher_name, "href": publisher_url},
    }


def _seed(SessionLocal):
    with SessionLocal.begin() as session:
        session.add_all([
            Source(
                id=1,
                name="Google News (ES) — Россия",
                url="https://news.google.com/rss/search?q=Rusia&gl=ES&ceid=ES:es",
                country_code="ES",
                source_type="rss",
                weight=1.0,
                config={"feed_mode": "publisher_discovery"},
            ),
            Source(
                id=2,
                name="EL PAÍS",
                url="https://elpais.com/rss",
                country_code="ES",
                source_type="rss",
                weight=0.9,
                config={},
            ),
            Source(
                id=3,
                name="Reuters",
                url="https://reuters.com/rss",
                country_code="GB",
                source_type="rss",
                weight=0.95,
                config={},
            ),
        ])
        session.add_all([
            PublisherDomain(
                domain="elpais.com",
                publisher_source_id=2,
                country_code="ES",
                status="verified",
                method="catalog",
                confidence=1.0,
                evidence={},
            ),
            PublisherDomain(
                domain="reuters.com",
                publisher_source_id=3,
                country_code="GB",
                status="verified",
                method="catalog",
                confidence=1.0,
                evidence={},
            ),
        ])
    with SessionLocal() as session:
        source = session.get(Source, 1)
        session.expunge(source)
        return source


def test_broad_feed_routes_verified_reassigned_and_unknown_before_dedup(monkeypatch):
    engine, SessionLocal = _database()
    source = _seed(SessionLocal)
    queued = []

    @contextmanager
    def session_context():
        with SessionLocal.begin() as session:
            yield session

    monkeypatch.setattr(collect, "get_session", session_context)
    monkeypatch.setattr(collect, "_enqueue_article", lambda article_id, country: queued.append((article_id, country)))
    monkeypatch.setattr(collect, "find_duplicate", lambda *args, **kwargs: None)

    articles = [
        _article("elpais", "EL PAÍS", "https://elpais.com", "elpais.com"),
        _article("reuters", "Reuters", "https://reuters.com", "reuters.com"),
        _article("unknown", "Unknown", "https://unknown.example", "unknown.example"),
    ]
    new_count, dupe_count, skipped = collect._save_source(source, articles)

    with SessionLocal() as session:
        saved = session.scalars(select(Article).order_by(Article.id)).all()
        discoveries = session.scalars(select(ArticleDiscovery)).all()

    assert (new_count, dupe_count, skipped) == (2, 0, 1)
    assert [(row.source_id, row.publisher_source_id) for row in saved] == [(1, 2), (1, 3)]
    assert [(row.geo_status, row.geo_country_code) for row in saved] == [
        ("publisher_verified", "ES"),
        ("publisher_reassigned", "GB"),
    ]
    assert [(row.publisher_name, row.publisher_domain) for row in saved] == [
        ("EL PAÍS", "elpais.com"),
        ("Reuters", "reuters.com"),
    ]
    assert [(row.external_id, row.geo_status) for row in discoveries] == [
        ("unknown", "unverified"),
    ]
    assert queued == [(saved[0].id, "ES"), (saved[1].id, "GB")]
    engine.dispose()


def test_unknown_discovery_is_recorded_even_when_discovery_source_has_exact_article(monkeypatch):
    engine, SessionLocal = _database()
    source = _seed(SessionLocal)
    with SessionLocal.begin() as session:
        session.add(Article(
            source_id=1,
            external_id="unknown",
            title="Legacy contaminated article",
            body="",
            url="https://news.google.com/rss/articles/unknown",
            published_at=PUBLISHED_AT,
        ))

    @contextmanager
    def session_context():
        with SessionLocal.begin() as session:
            yield session

    monkeypatch.setattr(collect, "get_session", session_context)
    monkeypatch.setattr(collect, "_enqueue_article", lambda *args: None)

    collect._save_source(
        source,
        [_article("unknown", "Unknown", "https://unknown.example", "unknown.example")],
    )

    with SessionLocal() as session:
        discovery = session.scalars(select(ArticleDiscovery)).one()
        assert discovery.external_id == "unknown"
        assert discovery.geo_status == "unverified"
    engine.dispose()


def test_direct_source_behavior_remains_source_verified(monkeypatch):
    engine, SessionLocal = _database()
    _seed(SessionLocal)
    with SessionLocal() as session:
        source = session.get(Source, 2)
        session.expunge(source)

    queued = []

    @contextmanager
    def session_context():
        with SessionLocal.begin() as session:
            yield session

    monkeypatch.setattr(collect, "get_session", session_context)
    monkeypatch.setattr(collect, "_enqueue_article", lambda article_id, country: queued.append(country))
    monkeypatch.setattr(collect, "find_duplicate", lambda *args, **kwargs: None)

    new_count, dupe_count, skipped = collect._save_source(
        source,
        [{
            "external_id": "direct",
            "title": "Direct source article with a long title",
            "body": "Russia",
            "url": "https://elpais.com/direct",
            "published_at": PUBLISHED_AT,
        }],
    )

    with SessionLocal() as session:
        saved = session.scalars(select(Article)).one()
        assert saved.source_id == 2
        assert saved.publisher_source_id is None
        assert saved.geo_status == "source_verified"
        assert saved.geo_country_code == "ES"
    assert (new_count, dupe_count, skipped) == (1, 0, 0)
    assert queued == ["ES"]
    engine.dispose()


def test_site_wrapper_publisher_mismatch_is_quarantined(monkeypatch):
    engine, SessionLocal = _database()
    _seed(SessionLocal)
    source = SimpleNamespace(
        id=4,
        name="EL PAÍS wrapper",
        url="https://news.google.com/rss/search?q=site:elpais.com+Rusia&gl=ES&ceid=ES:es",
        country_code="ES",
        source_type="rss",
        weight=0.9,
        config={"feed_mode": "site_wrapper"},
    )
    with SessionLocal.begin() as session:
        session.add(Source(
            id=4,
            name=source.name,
            url=source.url,
            country_code="ES",
            source_type="rss",
            weight=0.9,
            config=source.config,
        ))

    @contextmanager
    def session_context():
        with SessionLocal.begin() as session:
            yield session

    queued = []
    monkeypatch.setattr(collect, "get_session", session_context)
    monkeypatch.setattr(collect, "_enqueue_article", lambda *args: queued.append(args))

    result = collect._save_source(
        source,
        [_article("wrong", "Reuters", "https://reuters.com", "reuters.com")],
    )

    with SessionLocal() as session:
        assert session.scalars(select(Article)).all() == []
        discovery = session.scalars(select(ArticleDiscovery)).one()
        assert discovery.geo_status == "blocked"
        assert "site wrapper" in discovery.reason.lower()
    assert result == (0, 0, 1)
    assert queued == []
    engine.dispose()


def test_collect_all_selects_source_config():
    statements = []

    class EmptyRows:
        def fetchall(self):
            return []

    class Session:
        def execute(self, statement, params=None):
            statements.append(" ".join(str(statement).split()))
            return EmptyRows()

    @contextmanager
    def session_context():
        yield Session()

    original = collect.get_session
    original_stats = collect._update_collector_stats
    try:
        collect.get_session = session_context
        collect._update_collector_stats = lambda: None
        assert collect.collect_all() == 0
    finally:
        collect.get_session = original
        collect._update_collector_stats = original_stats

    assert statements == [
        "SELECT id, name, url, country_code, source_type, weight, config "
        "FROM sources WHERE active = true"
    ]
