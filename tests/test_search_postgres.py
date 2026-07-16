"""Opt-in PostgreSQL execution-plan regression tests for indexed article search."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text

from src.search import ARTICLE_SEARCH_SQL


DATABASE_URL = os.getenv("GEO_PULSE_SEARCH_PERF_TEST_DATABASE_URL")


def _walk_plan(node):
    yield node
    for child in node.get("Plans", []):
        yield from _walk_plan(child)


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="GEO_PULSE_SEARCH_PERF_TEST_DATABASE_URL is not configured",
)
def test_actual_search_plans_use_selective_indexes_and_bounded_candidates():
    """Exact search SQL must use selective indexes for representative branches."""

    engine = create_engine(DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TEMP TABLE sources (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    country_code CHAR(2) NOT NULL,
                    tier TEXT,
                    weight NUMERIC(3,2)
                );
                CREATE TEMP TABLE articles (
                    id INTEGER PRIMARY KEY,
                    source_id INTEGER NOT NULL,
                    title TEXT,
                    summary TEXT,
                    body TEXT,
                    url TEXT,
                    resolved_url TEXT,
                    published_at TIMESTAMPTZ NOT NULL,
                    collected_at TIMESTAMPTZ,
                    language TEXT,
                    title_normalized TEXT,
                    is_duplicate BOOLEAN NOT NULL,
                    search_vector TSVECTOR
                );
                CREATE TEMP TABLE article_country_facts (
                    article_id INTEGER PRIMARY KEY,
                    id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    country_code CHAR(2) NOT NULL,
                    tier TEXT,
                    weight NUMERIC(3,2)
                );
                CREATE TEMP TABLE analysis (
                    article_id INTEGER PRIMARY KEY,
                    topics TEXT[],
                    sentiment NUMERIC(3,1),
                    action_level INTEGER
                );
                CREATE TEMP TABLE canonical_entities (
                    id UUID PRIMARY KEY,
                    kind TEXT NOT NULL,
                    canonical_name TEXT NOT NULL,
                    normalized_name TEXT NOT NULL
                );
                CREATE TEMP TABLE entity_aliases (
                    entity_id UUID NOT NULL,
                    normalized_alias TEXT NOT NULL,
                    ambiguous BOOLEAN NOT NULL
                );
                CREATE TEMP TABLE article_entity_mentions (
                    article_id INTEGER NOT NULL,
                    entity_id UUID NOT NULL,
                    mention_text TEXT,
                    confidence NUMERIC(4,3)
                );
                CREATE TEMP TABLE stories (
                    id BIGINT PRIMARY KEY,
                    slug TEXT NOT NULL,
                    title_ru TEXT NOT NULL,
                    summary TEXT,
                    last_seen TIMESTAMPTZ
                );
                CREATE TEMP TABLE story_articles (
                    story_id BIGINT NOT NULL,
                    article_id INTEGER NOT NULL,
                    membership_confidence NUMERIC(4,3) NOT NULL
                );

                CREATE INDEX idx_articles_search_vector
                    ON articles USING GIN(search_vector);
                CREATE INDEX idx_articles_search_snapshot_v2
                    ON articles ((COALESCE(collected_at, published_at)) DESC, id DESC);
                CREATE INDEX idx_articles_source_published
                    ON articles(source_id, published_at DESC);
                CREATE INDEX idx_articles_source_candidates
                    ON articles(source_id, published_at DESC, id DESC)
                    WHERE is_duplicate = FALSE;
                CREATE INDEX idx_articles_title_trgm
                    ON articles USING GIN(title_normalized gin_trgm_ops);
                CREATE INDEX idx_articles_language_published_id
                    ON articles(language, published_at DESC, id DESC)
                    WHERE is_duplicate = FALSE;
                CREATE INDEX idx_article_country_facts_country_article
                    ON article_country_facts(country_code, article_id);
                CREATE INDEX idx_analysis_topics
                    ON analysis USING GIN(topics);
                CREATE INDEX idx_entity_aliases_normalized
                    ON entity_aliases(normalized_alias);
                CREATE UNIQUE INDEX idx_canonical_entities_kind_normalized
                    ON canonical_entities(kind, normalized_name);
                CREATE INDEX idx_article_entity_mentions_entity
                    ON article_entity_mentions(entity_id, article_id);
                CREATE INDEX idx_article_entity_mentions_article
                    ON article_entity_mentions(article_id);
                CREATE INDEX idx_story_articles_membership
                    ON story_articles(article_id, story_id);
            """))
            connection.execute(text("""
                INSERT INTO sources(id, name, country_code, tier, weight)
                VALUES (1, 'EL PAÍS', 'ES', 'mainstream', 0.8),
                       (2, 'Reuters', 'GB', 'mainstream', 0.8),
                       (900, 'Google News (ES) — Россия', 'ES', 'mainstream', 0.5);

                INSERT INTO articles(
                    id, source_id, title, summary, body, url, resolved_url,
                    published_at, collected_at, language, title_normalized,
                    is_duplicate, search_vector
                )
                SELECT sequence_id,
                       CASE WHEN sequence_id % 20 = 0 THEN 1 ELSE 2 END,
                       CASE WHEN sequence_id % 20 = 0
                            THEN 'Путин: тестовая новость ' || sequence_id
                            ELSE 'Обычная тестовая новость ' || sequence_id
                       END,
                       'Краткая аннотация',
                       'Текст материала',
                       'https://example.test/' || sequence_id,
                       NULL,
                       TIMESTAMPTZ '2026-07-15 12:00:00+00'
                           - make_interval(secs => sequence_id),
                       TIMESTAMPTZ '2026-07-15 12:00:00+00'
                           - make_interval(secs => sequence_id),
                       CASE WHEN sequence_id % 20 = 0 THEN 'es' ELSE 'ru' END,
                       CASE WHEN sequence_id % 20 = 0
                            THEN 'путин тестовая новость ' || sequence_id
                            ELSE 'обычная тестовая новость ' || sequence_id
                       END,
                       FALSE,
                       setweight(to_tsvector(
                           'simple',
                           CASE WHEN sequence_id % 20 = 0
                                THEN 'Путин тестовая новость ' || sequence_id
                                ELSE 'Обычная тестовая новость ' || sequence_id
                           END
                       ), 'A')
                FROM generate_series(1, 100001) AS generated(sequence_id);

                INSERT INTO article_country_facts(
                    article_id, id, name, country_code, tier, weight
                )
                SELECT ar.id, source.id, source.name, source.country_code,
                       source.tier, source.weight
                FROM articles ar
                JOIN sources source ON source.id = ar.source_id;

                INSERT INTO articles(
                    id, source_id, title, summary, body, url, resolved_url,
                    published_at, collected_at, language, title_normalized,
                    is_duplicate, search_vector
                ) VALUES
                    (300001, 900, 'Publisher attribution fixture EL PAÍS',
                     'fixture', 'fixture', 'https://news.google.com/articles/elpais',
                     'https://elpais.com/resolved-fixture',
                     TIMESTAMPTZ '2026-07-15 13:00:00+00',
                     TIMESTAMPTZ '2026-07-15 13:00:00+00', 'es',
                     'publisher attribution fixture el pais', FALSE,
                     to_tsvector('simple', 'publisher attribution fixture')),
                    (300002, 900, 'Publisher attribution fixture Reuters',
                     'fixture', 'fixture', 'https://news.google.com/articles/reuters',
                     'https://reuters.com/resolved-fixture',
                     TIMESTAMPTZ '2026-07-15 12:59:00+00',
                     TIMESTAMPTZ '2026-07-15 12:59:00+00', 'en',
                     'publisher attribution fixture reuters', FALSE,
                     to_tsvector('simple', 'publisher attribution fixture')),
                    (300003, 900, 'Publisher attribution fixture unknown',
                     'fixture', 'fixture', 'https://news.google.com/articles/unknown',
                     '', TIMESTAMPTZ '2026-07-15 12:58:00+00',
                     TIMESTAMPTZ '2026-07-15 12:58:00+00', 'en',
                     'publisher attribution fixture unknown', FALSE,
                     to_tsvector('simple', 'publisher attribution fixture'));

                INSERT INTO article_country_facts(
                    article_id, id, name, country_code, tier, weight
                ) VALUES
                    (300001, 1, 'EL PAÍS', 'ES', 'mainstream', 0.8),
                    (300002, 2, 'Reuters', 'GB', 'mainstream', 0.8);

                INSERT INTO analysis(article_id, topics)
                SELECT sequence_id,
                       CASE WHEN sequence_id % 1000 = 0
                            THEN ARRAY['diplomacy']::TEXT[]
                            ELSE ARRAY['other-topic']::TEXT[]
                       END
                FROM generate_series(1, 100001) AS generated(sequence_id);

                INSERT INTO canonical_entities(
                    id, kind, canonical_name, normalized_name
                ) VALUES (
                    '00000000-0000-0000-0000-000000000001',
                    'person', 'Vladimir Putin', 'vladimir putin'
                );
                INSERT INTO article_entity_mentions(
                    article_id, entity_id, mention_text, confidence
                )
                SELECT sequence_id,
                       '00000000-0000-0000-0000-000000000001'::UUID,
                       'Putin', 1.0
                FROM generate_series(1, 100001) AS generated(sequence_id);

                ANALYZE sources;
                ANALYZE articles;
                ANALYZE article_country_facts;
                ANALYZE analysis;
                ANALYZE canonical_entities;
                ANALYZE entity_aliases;
                ANALYZE article_entity_mentions;
                ANALYZE stories;
                ANALYZE story_articles;
            """))

            params = {
                "q": "путин",
                "country": None,
                "topic": None,
                "entity_id": None,
                "date_from": None,
                "date_to": None,
                "tier": None,
                "language": None,
                "sort": "relevance",
                "ranking_at": datetime(2026, 7, 15, 12, tzinfo=timezone.utc),
                "snapshot_collected_at": None,
                "snapshot_collected_article_id": None,
                "snapshot_max_article_id": None,
                "candidate_limit": 500,
            }
            connection.execute(text(
                "SET LOCAL pg_trgm.similarity_threshold = 0.1"
            ))
            lexical_plan_document = connection.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + ARTICLE_SEARCH_SQL),
                params,
            ).scalar_one()
            topic_plan_document = connection.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + ARTICLE_SEARCH_SQL),
                {**params, "q": "diplomacy"},
            ).scalar_one()
            language_plan_document = connection.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + ARTICLE_SEARCH_SQL),
                {**params, "q": "", "language": "es"},
            ).scalar_one()
            entity_country_plan_document = connection.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + ARTICLE_SEARCH_SQL),
                {**params, "q": "vladimir putin", "country": "ES"},
            ).scalar_one()
            structured_entity_params = {
                **params,
                "q": "",
                "entity_id": "00000000-0000-0000-0000-000000000001",
                "country": "ES",
            }
            structured_entity_country_plan_document = connection.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + ARTICLE_SEARCH_SQL),
                structured_entity_params,
            ).scalar_one()
            structured_entity_rows = connection.execute(
                text(ARTICLE_SEARCH_SQL), structured_entity_params
            ).mappings().all()
            publisher_rows = connection.execute(
                text(ARTICLE_SEARCH_SQL),
                {
                    **params,
                    "q": "publisher attribution fixture",
                    "country": "ES",
                },
            ).mappings().all()

            connection.execute(text("""
                INSERT INTO articles(
                    id, source_id, title, summary, body, url, resolved_url,
                    published_at, collected_at, language, title_normalized,
                    is_duplicate, search_vector
                )
                SELECT 200000 + sequence_id,
                       1,
                       'Post-snapshot article ' || sequence_id,
                       'Post-snapshot summary',
                       'Post-snapshot body',
                       'https://example.test/post-snapshot/' || sequence_id,
                       NULL,
                       TIMESTAMPTZ '2026-07-16 12:00:00+00'
                           - make_interval(secs => sequence_id),
                       TIMESTAMPTZ '2026-07-16 12:00:00+00'
                           - make_interval(secs => sequence_id),
                       'es',
                       'post snapshot article ' || sequence_id,
                       FALSE,
                       setweight(to_tsvector(
                           'simple', 'Post-snapshot article ' || sequence_id
                       ), 'A')
                FROM generate_series(1, 600) AS generated(sequence_id);
                INSERT INTO article_country_facts(
                    article_id, id, name, country_code, tier, weight
                )
                SELECT ar.id, source.id, source.name, source.country_code,
                       source.tier, source.weight
                FROM articles ar
                JOIN sources source ON source.id = ar.source_id
                WHERE ar.id BETWEEN 200001 AND 200600;
                ANALYZE articles;
                ANALYZE article_country_facts;
            """))
            snapshot_params = {
                **params,
                "q": "",
                "country": "ES",
                "snapshot_collected_at": datetime(
                    2026, 7, 15, 11, tzinfo=timezone.utc
                ),
                "snapshot_collected_article_id": 3600,
                "snapshot_max_article_id": 100001,
            }
            snapshot_rows = connection.execute(
                text(ARTICLE_SEARCH_SQL), snapshot_params
            ).mappings().all()
            snapshot_plan_document = connection.execute(
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + ARTICLE_SEARCH_SQL),
                snapshot_params,
            ).scalar_one()

        assert [row["source_name"] for row in publisher_rows] == ["EL PAÍS"]
        assert [row["country_code"].strip() for row in publisher_rows] == ["ES"]
        assert [row["url"] for row in publisher_rows] == [
            "https://elpais.com/resolved-fixture"
        ]
        assert "Reuters" not in [row["source_name"] for row in publisher_rows]
        assert all(
            "Google News (" not in row["source_name"] for row in publisher_rows
        )

        lexical_root = lexical_plan_document[0]
        lexical_nodes = [
            node for node in _walk_plan(lexical_root["Plan"])
            if node.get("Actual Loops", 0) > 0
        ]
        assert any(
            node.get("Node Type") == "Bitmap Index Scan"
            and node.get("Index Name") == "idx_articles_search_vector"
            for node in lexical_nodes
        ), lexical_plan_document
        assert not any(
            node.get("Node Type") == "Seq Scan"
            and node.get("Relation Name") == "articles"
            for node in lexical_nodes
        ), lexical_plan_document
        assert lexical_root["Execution Time"] < 2000, lexical_plan_document

        topic_root = topic_plan_document[0]
        topic_nodes = [
            node for node in _walk_plan(topic_root["Plan"])
            if node.get("Actual Loops", 0) > 0
        ]
        assert any(
            node.get("Node Type") == "Bitmap Index Scan"
            and node.get("Index Name") == "idx_analysis_topics"
            for node in topic_nodes
        ), topic_plan_document
        assert not any(
            node.get("Node Type") == "Seq Scan"
            and node.get("Relation Name") == "articles"
            for node in topic_nodes
        ), topic_plan_document
        assert topic_root["Execution Time"] < 2000, topic_plan_document

        language_root = language_plan_document[0]
        language_nodes = [
            node for node in _walk_plan(language_root["Plan"])
            if node.get("Actual Loops", 0) > 0
        ]
        assert any(
            node.get("Node Type") in {
                "Bitmap Index Scan", "Index Scan", "Index Only Scan"
            }
            and node.get("Index Name") == "idx_articles_language_published_id"
            for node in language_nodes
        ), language_plan_document
        assert not any(
            node.get("Node Type") == "Seq Scan"
            and node.get("Relation Name") == "articles"
            for node in language_nodes
        ), language_plan_document
        assert not any(
            node.get("Node Type") in {"Aggregate", "Sort", "Unique"}
            and node.get("Actual Rows", 0) > 500
            for node in language_nodes
        ), language_plan_document
        assert language_root["Execution Time"] < 2000, language_plan_document

        entity_country_root = entity_country_plan_document[0]
        entity_country_nodes = [
            node for node in _walk_plan(entity_country_root["Plan"])
            if node.get("Actual Loops", 0) > 0
        ]
        assert not any(
            node.get("Node Type") == "Seq Scan"
            and node.get("Relation Name") == "articles"
            for node in entity_country_nodes
        ), entity_country_plan_document
        assert any(
            node.get("Node Type") in {
                "Bitmap Index Scan", "Index Scan", "Index Only Scan"
            }
            and node.get("Index Name")
            == "idx_article_country_facts_country_article"
            for node in entity_country_nodes
        ), entity_country_plan_document
        assert not any(
            node.get("Node Type") in {"Aggregate", "Sort", "Unique"}
            and node.get("Actual Rows", 0) > 500
            for node in entity_country_nodes
        ), entity_country_plan_document
        assert entity_country_root["Execution Time"] < 2000, entity_country_plan_document

        structured_entity_country_root = structured_entity_country_plan_document[0]
        structured_entity_country_nodes = [
            node for node in _walk_plan(structured_entity_country_root["Plan"])
            if node.get("Actual Loops", 0) > 0
        ]
        assert len(structured_entity_rows) == 500
        assert any(
            node.get("Node Type") in {
                "Bitmap Index Scan", "Index Scan", "Index Only Scan"
            }
            and node.get("Index Name")
            == "idx_article_country_facts_country_article"
            for node in structured_entity_country_nodes
        ), structured_entity_country_plan_document
        assert not any(
            node.get("Node Type") in {"Aggregate", "Sort", "Unique"}
            and node.get("Actual Rows", 0) > 500
            for node in structured_entity_country_nodes
        ), structured_entity_country_plan_document
        assert structured_entity_country_root["Execution Time"] < 2000, (
            structured_entity_country_plan_document
        )

        snapshot_root = snapshot_plan_document[0]
        snapshot_nodes = [
            node for node in _walk_plan(snapshot_root["Plan"])
            if node.get("Actual Loops", 0) > 0
        ]
        assert len(snapshot_rows) == 500
        assert all(row["id"] <= 100001 for row in snapshot_rows)
        assert any(
            node.get("Node Type") in {
                "Bitmap Index Scan", "Index Scan", "Index Only Scan"
            }
            and node.get("Index Name")
            == "idx_article_country_facts_country_article"
            for node in snapshot_nodes
        ), snapshot_plan_document
        assert not any(
            node.get("Node Type") == "Seq Scan"
            and node.get("Relation Name") == "articles"
            for node in snapshot_nodes
        ), snapshot_plan_document
        assert snapshot_root["Execution Time"] < 2000, snapshot_plan_document
    finally:
        engine.dispose()
