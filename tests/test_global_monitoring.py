"""Bounded country sampling remains honest about empty and thin coverage."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os

import pytest
from sqlalchemy import create_engine, text

from src import global_monitoring as monitoring

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def article(code="ET", publisher=1, article_id=1, minutes=0):
    published = NOW - timedelta(hours=1, minutes=minutes)
    return {"code": code, "id": article_id, "title": f"Training notice {article_id}",
            "excerpt": "A local institution announced a training proposal.",
            "source_id": publisher, "source_name": f"Publisher {publisher}",
            "country_code": code, "url": f"https://example.org/{article_id}",
            "published_at": published, "collected_at": published + timedelta(minutes=1)}


def test_publisher_round_robin_prevents_single_feed_from_filling_country():
    rows = [article(publisher=1, article_id=i, minutes=i) for i in range(1, 201)]
    rows += [article(publisher=2, article_id=201, minutes=201)]
    selected = monitoring._fair_sample(rows, 4)
    assert [row["source_id"] for row in selected] == [1, 2, 1, 1]
    assert len(monitoring._fair_sample(rows, 40)) == 9  # Eight per publisher + one.
    # The SQL must probe publisher 2 independently; the older article would be
    # lost if a top-160 country LIMIT ran before resolved-publisher selection.
    sql = str(monitoring._ARTICLES)
    assert sql.index("JOIN sources publisher") < sql.index("CROSS JOIN LATERAL")
    assert "LIMIT :publisher_cap" in sql
    assert "LIMIT :probe" not in sql
    assert "article.source_id=publisher.id AND article.publisher_source_id IS NULL" in sql
    assert "article.publisher_source_id=publisher.id" in sql and "UNION ALL" in sql


class _Result:
    def __init__(self, rows):
        self.rows = rows
    def mappings(self):
        return self
    def all(self):
        return self.rows


class _Session:
    def __init__(self, sources, articles):
        self.sources, self.articles = sources, articles
        self.queries = []
    def execute(self, query, params=None):
        self.queries.append((str(query), params))
        sql = str(query)
        if "FROM sources WHERE active" in sql:
            return _Result(self.sources)
        if "FROM unnest" in sql:
            return _Result(self.articles)
        return _Result([])


def test_all_areas_including_zero_source_and_bounded_country_query(monkeypatch):
    sources = [{"id": 1, "name": "Local publisher", "country_code": "ET", "type": "rss",
                "url": "https://example.org/feed", "config": {}, "tier": "mainstream",
                "state_affiliated": False, "last_status": "ok", "last_fetch_at": NOW}]
    session = _Session(sources, [article("ET", 1, 1), article("ET", 1, 2),
                                 article("ET", 1, 3), article("RS", 2, 4)])
    @contextmanager
    def fake_session():
        yield session
    monkeypatch.setattr(monitoring, "get_session", fake_session)
    result = monitoring.load_global_monitoring(as_of=NOW, per_country=2)
    assert len(result["countries"]) == 248
    by_code = {row["code"]: row for row in result["countries"]}
    assert by_code["ET"]["sampled_articles_7d"] == 2
    assert by_code["ET"]["working_direct_publishers"] == 1
    assert by_code["ET"]["coverage_state"] == "sampled_articles"
    assert by_code["RS"]["coverage_state"] == "sampled_articles"
    assert by_code["RS"]["source_gaps"] == ["no_active_sources"]
    assert by_code["DJ"]["sampled_articles_7d"] == 0
    assert len([a for a in result["articles"] if a["country_code"] == "ET"]) == 2
    assert result["limits"]["counts_are_bounded"] is True
    query, params = next((sql, params) for sql, params in session.queries if "FROM unnest" in sql)
    assert "CROSS JOIN LATERAL" in query and "article.geo_country_code=target.code" in query
    assert params["publisher_cap"] == 8 and params["per_country"] == 2
    assert len(params["codes"]) == 248


def test_invalid_bounds_and_unsafe_source_excluded(monkeypatch):
    for bound in (0, 41, True):
        with pytest.raises(ValueError):
            monitoring.load_global_monitoring(as_of=NOW, per_country=bound)
    with pytest.raises(ValueError):
        monitoring.load_global_monitoring(as_of=NOW.replace(tzinfo=None))
    session = _Session([], [article("ET", 1, 1) | {"url": "http://127.0.0.1/internal"}])
    @contextmanager
    def fake_session():
        yield session
    monkeypatch.setattr(monitoring, "get_session", fake_session)
    result = monitoring.load_global_monitoring(as_of=NOW)
    assert result["articles"] == []
    assert next(c for c in result["countries"] if c["code"] == "ET")["sampled_articles_7d"] == 0


def test_old_successful_fetch_is_a_source_gap():
    source = {"id": 1, "name": "Once working", "country_code": "ET", "type": "rss",
              "url": "https://example.org/feed", "config": {}, "tier": "mainstream",
              "state_affiliated": False, "last_status": "ok",
              "last_fetch_at": NOW - timedelta(days=4)}
    assert monitoring._source_counts([source], as_of=NOW)["ET"] == {
        "configured_sources": 1, "working_direct_publishers": 0}
    source["last_fetch_at"] = NOW - timedelta(hours=71)
    assert monitoring._source_counts([source], as_of=NOW)["ET"]["working_direct_publishers"] == 1
    source["last_fetch_at"] = NOW + timedelta(minutes=1)
    assert monitoring._source_counts([source], as_of=NOW)["ET"]["working_direct_publishers"] == 0


def test_postgres_quiet_publisher_survives_dominant_feed(monkeypatch):
    """Optional disposable-DB check of the actual per-publisher LATERAL query."""
    dsn = os.environ.get("GEO_PULSE_TEST_DATABASE_URL")
    if not dsn or os.environ.get("GEO_PULSE_TEST_DATABASE_RESET") != "1":
        pytest.skip("requires explicitly disposable PostgreSQL")
    engine = create_engine(dsn)
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(text("""CREATE TEMP TABLE sources (
                id integer PRIMARY KEY,name text,country_code char(2),source_type text,
                url text,config jsonb,tier text,state_affiliated boolean,
                last_status text,last_fetch_at timestamptz,active boolean)"""))
            connection.execute(text("""CREATE TEMP TABLE articles (
                id integer PRIMARY KEY,source_id integer,publisher_source_id integer,
                title text,body text,summary text,url text,resolved_url text,
                published_at timestamptz,collected_at timestamptz,
                geo_country_code char(2),is_duplicate boolean,geo_status text)"""))
            connection.execute(text("""INSERT INTO sources VALUES
                (1,'Large','ET','rss','https://large.example.org/feed','{}','mainstream',false,'ok',:at,true),
                (2,'Quiet','ET','rss','https://quiet.example.org/feed','{}','mainstream',false,'ok',:at,true),
                (3,'Discovery','RU','rss','https://discovery.example.org/feed',
                 '{"feed_mode":"publisher_discovery"}','mainstream',false,'ok',:at,true)"""), {"at": NOW})
            connection.execute(text("""INSERT INTO articles
                SELECT gs,1,NULL,'Local training notice '||gs,'Source report',NULL,
                  'https://large.example.org/'||gs,NULL,
                  :at-(gs * interval '1 minute'),:at-(gs * interval '1 minute')+interval '10 seconds',
                  'ET',false,'source_verified'
                FROM generate_series(1,200) AS gs"""), {"at": NOW})
            connection.execute(text("""INSERT INTO articles VALUES
                (201,2,NULL,'Quiet local notice','Source report',NULL,
                 'https://quiet.example.org/201',NULL,
                 :at-interval '201 minutes',:at-interval '200 minutes',
                 'ET',false,'source_verified')"""), {"at": NOW})
            connection.execute(text("""INSERT INTO articles VALUES
                (202,3,2,'Attributed quiet notice','Source report',NULL,
                 'https://quiet.example.org/202',NULL,
                 :at-interval '202 minutes',:at-interval '201 minutes',
                 'ET',false,'publisher_verified')"""), {"at": NOW})
            @contextmanager
            def fake_session():
                yield connection
            monkeypatch.setattr(monitoring, "get_session", fake_session)
            snapshot = monitoring.load_global_monitoring(as_of=NOW)
            ethiopia = next(row for row in snapshot["countries"] if row["code"] == "ET")
            assert ethiopia["sampled_articles_7d"] == 10
            assert {201, 202} <= {row["id"] for row in snapshot["articles"]}
        finally:
            transaction.rollback()
    engine.dispose()
