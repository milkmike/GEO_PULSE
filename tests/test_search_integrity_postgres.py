"""Retrieval behavior against real PostgreSQL, without production data or APIs."""
import os
from contextlib import nullcontext
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import create_engine, text

from src.search import SearchQuery, encode_cursor, normalize_query, search_articles

DSN = os.getenv("GEO_PULSE_SEARCH_PERF_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="requires isolated search PostgreSQL")


@pytest.fixture
def search_db():
    engine = create_engine(DSN)
    with engine.connect() as connection, connection.begin():
        connection.execute(text("""
            CREATE TEMP TABLE sources (
                id INT PRIMARY KEY, name TEXT, country_code TEXT, tier TEXT,
                weight REAL
            );
            CREATE TEMP TABLE articles (
                id INT PRIMARY KEY, source_id INT, publisher_source_id INT,
                title TEXT, summary TEXT, body TEXT, url TEXT, resolved_url TEXT,
                published_at TIMESTAMPTZ, collected_at TIMESTAMPTZ,
                language TEXT, title_normalized TEXT,
                is_duplicate BOOLEAN DEFAULT FALSE, search_vector TSVECTOR
            );
            CREATE TEMP VIEW article_country_facts AS
                SELECT a.id AS article_id, s.* FROM articles a
                JOIN sources s ON s.id=COALESCE(a.publisher_source_id,a.source_id);
            CREATE TEMP TABLE analysis (
                article_id INT PRIMARY KEY, topics TEXT[], sentiment REAL, action_level INT
            );
            CREATE TEMP TABLE canonical_entities (
                id UUID PRIMARY KEY, kind TEXT, canonical_name TEXT, normalized_name TEXT
            );
            CREATE TEMP TABLE entity_aliases (
                entity_id UUID, normalized_alias TEXT, ambiguous BOOLEAN
            );
            CREATE TEMP TABLE article_entity_mentions (
                article_id INT, entity_id UUID, mention_text TEXT, confidence REAL
            );
            CREATE TEMP TABLE stories (
                id BIGINT PRIMARY KEY, slug TEXT, title_ru TEXT, summary TEXT, last_seen TIMESTAMPTZ
            );
            CREATE TEMP TABLE story_articles (
                story_id BIGINT, article_id INT, membership_confidence REAL
            );
            INSERT INTO sources VALUES (1,'Publisher','ES','mainstream',0.8);
        """))
        yield connection
    engine.dispose()


def add_article(db, article_id, title, body="", summary="", published="2026-07-01", language="ru"):
    db.execute(text("""
        INSERT INTO articles (id,source_id,title,summary,body,url,published_at,
                              collected_at,language,title_normalized,search_vector)
        VALUES (:id,1,:title,:summary,:body,'https://example.test/' || :id,
                CAST(:published AS TIMESTAMPTZ),CAST(:published AS TIMESTAMPTZ),
                :language,lower(:title),
                to_tsvector('simple',:title || ' ' || :summary || ' ' || :body))
    """), dict(id=article_id,title=title,summary=summary,body=body,published=published,language=language))


def run_search(db, q="", **filters):
    return search_articles(
        SearchQuery(q=normalize_query(q), **filters),
        session_factory=lambda: nullcontext(db),
        now=datetime(2026, 7, 15, tzinfo=timezone.utc),
    )


def ids(page):
    return {item["article_id"] for item in page["items"]}


def test_entity_alias_keeps_unannotated_text_matches(search_db):
    add_article(search_db, 1, "Путин подписал договор")
    add_article(search_db, 2, "Встреча президента")
    search_db.execute(text("""
        INSERT INTO canonical_entities VALUES
            ('00000000-0000-0000-0000-000000000001','person','Владимир Путин','владимир путин');
        INSERT INTO entity_aliases VALUES
            ('00000000-0000-0000-0000-000000000001','путин',false);
        INSERT INTO article_entity_mentions VALUES
            (2,'00000000-0000-0000-0000-000000000001','Путин',0.95);
    """))
    assert ids(run_search(search_db, "Путин")) == {1, 2}


def test_quoted_phrase_is_not_broadened_by_fuzzy_or_story_matches(search_db):
    add_article(search_db, 1, "Северный поток открыт")
    add_article(search_db, 2, "Северный регион получил поток инвестиций")
    search_db.execute(text("""
        INSERT INTO stories VALUES (1,'pipeline','Северный поток','',now());
        INSERT INTO story_articles VALUES (1,2,0.95);
    """))
    assert ids(run_search(search_db, '"Северный поток"')) == {1}


def test_excluded_term_cannot_return_via_story_or_fuzzy_match(search_db):
    add_article(search_db, 1, "Санкции обсуждаются")
    add_article(search_db, 2, "Санкции затронули спорт")
    search_db.execute(text("""
        INSERT INTO stories VALUES (1,'sanctions','Санкции обсуждаются','',now());
        INSERT INTO story_articles VALUES (1,2,0.95);
    """))
    assert ids(run_search(search_db, "санкции -спорт")) == {1}


def test_or_query_keeps_both_literal_branches(search_db):
    add_article(search_db, 1, "Санкции обсуждаются")
    add_article(search_db, 2, "Торговля растёт")
    add_article(search_db, 3, "Спорт сегодня")
    assert ids(run_search(search_db, "санкции OR торговля")) == {1, 2}


@pytest.mark.parametrize("field", ["body", "summary"])
def test_evidence_quotes_the_field_that_matched(search_db, field):
    add_article(search_db, 1, "Итоги заседания", **{field: "Обсудили ИННОПРОМ Беларусь и торговлю."})
    item = run_search(search_db, "ИННОПРОМ Беларусь")["items"][0]
    passages = [e["text"] for e in item["evidence"] if e["type"] == "text_span"]
    assert any("ИННОПРОМ" in passage and "Беларусь" in passage for passage in passages)


def test_query_spanning_title_and_body_has_complete_evidence(search_db):
    add_article(search_db, 1, "ИННОПРОМ", body="Беларусь участвует в выставке")
    item = run_search(search_db, "ИННОПРОМ Беларусь")["items"][0]
    passages = [e["text"] for e in item["evidence"] if e["type"] == "text_span"]
    assert any("ИННОПРОМ" in passage and "Беларусь" in passage for passage in passages)


@pytest.mark.parametrize("filters", [
    {"date_from": date(2025,1,1), "date_to": date(2025,1,31)},
    {"language": "es"},
    {"topic": "energy"},
    {"entity_id": "00000000-0000-0000-0000-000000000001"},
])
def test_structured_filters_precede_publisher_candidate_cap(search_db, filters):
    add_article(search_db, 1, "Archive evidence", published="2025-01-15", language="es")
    search_db.execute(text("""
        INSERT INTO articles (id,source_id,title,published_at,collected_at,language)
        SELECT n,1,'Newer unrelated article',TIMESTAMPTZ '2026-07-01',
               TIMESTAMPTZ '2026-07-01','ru' FROM generate_series(2,602) n;
        INSERT INTO analysis VALUES (1,ARRAY['energy'],0,0);
        INSERT INTO canonical_entities VALUES
            ('00000000-0000-0000-0000-000000000001','person','Test actor','test actor');
        INSERT INTO article_entity_mentions VALUES
            (1,'00000000-0000-0000-0000-000000000001','Test actor',1);
    """))
    assert ids(run_search(search_db, country="ES", **filters)) == {1}


@pytest.mark.parametrize("q", ["energy", "specific story"])
def test_expansion_matches_are_filtered_before_publisher_cap(search_db, q):
    add_article(search_db, 1, "Archive evidence", published="2025-01-15")
    search_db.execute(text("""
        INSERT INTO articles (id,source_id,title,published_at,collected_at,language)
        SELECT n,1,'Newer unrelated article',TIMESTAMPTZ '2026-07-01',
               TIMESTAMPTZ '2026-07-01','ru' FROM generate_series(2,602) n;
        INSERT INTO analysis VALUES (1,ARRAY['energy'],0,0);
        INSERT INTO stories VALUES (1,'specific','specific story','',now());
        INSERT INTO story_articles VALUES (1,1,0.95);
    """))
    assert ids(run_search(search_db, q, country="ES")) == {1}


def test_candidate_limit_is_visible_on_every_page(search_db):
    search_db.execute(text("""
        INSERT INTO articles (id,source_id,title,published_at,collected_at,language,search_vector)
        SELECT n,1,'Sanctions',TIMESTAMPTZ '2026-07-01',
               TIMESTAMPTZ '2026-07-01','en',to_tsvector('simple','sanctions')
        FROM generate_series(1,601) n;
    """))
    first = run_search(search_db, "sanctions", limit=2)
    assert first["candidate_limit_reached"] is True
    values = dict(first["next_cursor"])
    for field in ("published_at", "ranking_at", "snapshot_collected_at"):
        values[field] = datetime.fromisoformat(values[field])
    cursor = encode_cursor(**values)
    second = run_search(search_db, "sanctions", limit=2, cursor=cursor)
    assert second["candidate_limit_reached"] is True
    assert not ids(first) & ids(second)


def test_small_result_set_does_not_claim_candidate_limit(search_db):
    add_article(search_db, 1, "Sanctions")
    assert run_search(search_db, "sanctions")["candidate_limit_reached"] is False
