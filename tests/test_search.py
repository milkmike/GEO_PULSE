import base64
import json
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes.search import get_search_service
from src.search import (
    ARTICLE_SEARCH_SQL,
    SearchQuery,
    SearchTimeoutError,
    combine_scores,
    decode_cursor,
    encode_cursor,
    explain_match,
    normalize_query,
    search_articles,
    search_request_fingerprint,
    validate_search_query,
)


def test_normalize_query_handles_russian_punctuation_and_yo():
    assert normalize_query("  ПУТИН, в Испании!  ") == "путин в испании"
    assert normalize_query("Ёлка") == "елка"


def test_empty_query_requires_a_structured_filter():
    with pytest.raises(ValueError, match="query or filter"):
        validate_search_query("", {})
    validate_search_query("", {"country": "ES"})


@pytest.mark.parametrize("query", ["a", "a" * 201])
def test_query_length_is_between_two_and_two_hundred_characters(query):
    with pytest.raises(ValueError, match="between 2 and 200"):
        validate_search_query(query, {})


def test_exact_entity_match_outranks_body_only_lexical_match():
    entity = combine_scores(
        lexical=.3,
        entity=1,
        topic=0,
        freshness=.5,
        trust=.8,
        story=0,
    )
    body = combine_scores(
        lexical=.8,
        entity=0,
        topic=0,
        freshness=.5,
        trust=.8,
        story=0,
    )
    assert entity.final > body.final


def test_missing_vector_score_does_not_reduce_v1_score():
    score = combine_scores(
        lexical=.8,
        entity=.4,
        topic=.2,
        freshness=.7,
        trust=.8,
        story=.1,
    )
    assert score.vector is None
    assert 0 <= score.final <= 1


def test_v1_score_uses_the_approved_weights():
    score = combine_scores(
        lexical=1,
        entity=1,
        topic=1,
        freshness=1,
        trust=1,
        story=1,
    )
    assert score.final == 1
    assert score.lexical == 1
    assert score.entity == 1
    assert score.topic == 1
    assert score.freshness == 1
    assert score.trust == 1
    assert score.story == 1


@pytest.mark.parametrize(
    ("component", "expected"),
    [
        ("lexical", .35),
        ("entity", .25),
        ("topic", .15),
        ("freshness", .10),
        ("trust", .10),
        ("story", .05),
    ],
)
def test_v1_component_weight_regression(component, expected):
    values = {
        "lexical": 0,
        "entity": 0,
        "topic": 0,
        "freshness": 0,
        "trust": 0,
        "story": 0,
    }
    values[component] = 1
    assert combine_scores(**values).final == expected


def test_explanation_names_strongest_observed_match_without_semantic_claims():
    score = combine_scores(
        lexical=.3,
        entity=1,
        topic=.2,
        freshness=.5,
        trust=.8,
        story=0,
    )
    explanation = explain_match(
        score,
        matched_entity="Владимир Путин",
        lexical_field="body",
    )
    assert explanation == (
        "Точное упоминание сущности «Владимир Путин»"
    )
    assert "семан" not in explanation.casefold()


def test_cursor_serialization_is_stable_and_round_trips():
    published_at = datetime(2026, 7, 14, 12, 30, tzinfo=timezone.utc)
    ranking_at = datetime(2026, 7, 15, 9, 0, tzinfo=timezone.utc)
    snapshot_at = datetime(2026, 7, 15, 8, 55, tzinfo=timezone.utc)
    request_fingerprint = "a" * 64
    first = encode_cursor(
        .712345,
        published_at,
        321,
        ranking_at,
        snapshot_at,
        900,
        900,
        request_fingerprint,
    )
    second = encode_cursor(
        .712345,
        published_at,
        321,
        ranking_at,
        snapshot_at,
        900,
        900,
        request_fingerprint,
    )
    assert first == second
    assert decode_cursor(first) == (
        .712345,
        published_at,
        321,
        ranking_at,
        snapshot_at,
        900,
        900,
        request_fingerprint,
    )

    half_up = encode_cursor(
        .1234565,
        published_at,
        321,
        ranking_at,
        snapshot_at,
        900,
        900,
        request_fingerprint,
    )
    assert decode_cursor(half_up)[0] == .123457


class FakeSearchService:
    def __init__(self, *, url: str = "https://elpais.com/mundo/putin-espana"):
        self.url = url

    def __call__(self, query):
        return {
            "items": [
                {
                    "article_id": 123,
                    "title": "Путин обсудил отношения с Испанией",
                    "summary": (
                        "Переговоры затронули "
                        "двусторонние отношения."
                    ),
                    "url": self.url,
                    "published_at": "2026-07-14T12:30:00+00:00",
                    "language": "es",
                    "source": {
                        "name": "El País",
                        "country": query.country,
                        "tier": "mainstream",
                    },
                    "topics": ["diplomacy"],
                    "matched_entities": [
                        {
                            "id": "93dbeaec-c20b-44ad-aaed-46b18ea86a47",
                            "name": "Владимир Путин",
                        }
                    ],
                    "sentiment": 1.5,
                    "action_level": 3,
                    "story": {
                        "id": 7,
                        "slug": "russia-spain-talks",
                        "title": "Переговоры России и Испании",
                    },
                    "why_included": (
                        "Точное упоминание сущности "
                        "«Владимир Путин»"
                    ),
                    "relevance_score": .712345,
                    "confidence": .91,
                    "evidence": [
                        {
                            "type": "text_span",
                            "article_id": 123,
                            "text": "Путин обсудил отношения",
                        }
                    ],
                    "scores": {
                        "lexical": .75,
                        "entity": 1.0,
                        "topic": 0.0,
                        "freshness": .98,
                        "trust": .9,
                        "story": .8,
                        "vector": None,
                    },
                }
            ],
            "candidate_count": 1,
            "next_cursor": {
                "relevance_score": .712345,
                "published_at": "2026-07-14T12:30:00+00:00",
                "article_id": 123,
                "ranking_at": "2026-07-15T09:00:00+00:00",
                "snapshot_collected_at": "2026-07-15T08:55:00+00:00",
                "snapshot_collected_article_id": 900,
                "snapshot_max_article_id": 900,
                "request_fingerprint": search_request_fingerprint(query),
            },
        }


@pytest.fixture
def search_client():
    app.dependency_overrides[get_search_service] = lambda: FakeSearchService()
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_search_service, None)


def test_search_endpoint_rejects_missing_query_and_filters(search_client):
    response = search_client.get("/api/v2/search/articles")
    assert response.status_code == 422
    assert response.json()["detail"] == "query or filter is required"


def test_search_endpoint_rejects_whitespace_only_structured_filters(search_client):
    response = search_client.get("/api/v2/search/articles", params={"topic": "   "})
    assert response.status_code == 422
    assert response.json()["detail"] == "query or filter is required"


def test_search_endpoint_limits_page_size(search_client):
    response = search_client.get(
        "/api/v2/search/articles",
        params={"q": "Путин", "limit": 101},
    )
    assert response.status_code == 422


def test_search_endpoint_normalizes_country_and_returns_explainable_result(search_client):
    response = search_client.get(
        "/api/v2/search/articles",
        params={"q": "  ПУТИН! ", "country": "es"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "путин"
    assert payload["filters"]["country"] == "ES"
    assert payload["semantic_search"] == "unavailable"
    assert payload["candidate_count"] == 1
    assert payload["next_cursor"]

    item = payload["items"][0]
    assert item["article_id"] == 123
    assert item["source"]["country"] == "ES"
    assert item["url"].startswith("https://")
    assert item["why_included"] == (
        "Точное упоминание сущности «Владимир Путин»"
    )
    assert item["relevance_score"] == .712345
    assert item["confidence"] == .91
    assert item["evidence"][0]["article_id"] == 123
    assert item["scores"]["vector"] is None


def test_search_endpoint_serializes_cursor_stably(search_client):
    params = {"q": "Путин", "country": "ES"}
    first = search_client.get("/api/v2/search/articles", params=params).json()["next_cursor"]
    second = search_client.get("/api/v2/search/articles", params=params).json()["next_cursor"]
    assert first == second
    assert decode_cursor(first)[2] == 123


@pytest.mark.parametrize(
    "changed_params",
    [
        {"q": "Медведев", "country": "ES", "sort": "relevance"},
        {"q": "Путин", "country": "FR", "sort": "relevance"},
        {"q": "Путин", "country": "ES", "sort": "newest"},
    ],
)
def test_search_endpoint_rejects_cursor_from_a_different_request(
    search_client,
    changed_params,
):
    first = search_client.get(
        "/api/v2/search/articles",
        params={"q": "Путин", "country": "ES", "sort": "relevance"},
    )
    cursor = first.json()["next_cursor"]

    response = search_client.get(
        "/api/v2/search/articles",
        params={**changed_params, "cursor": cursor},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "cursor does not match search request"


def test_search_request_fingerprint_canonicalizes_uuid_dates_and_nulls():
    entity_id = "93DBEAEC-C20B-44AD-AAED-46B18EA86A47"
    first = SearchQuery(
        q="  ПУТИН! ",
        country="es",
        entity_id=entity_id,
        date_from=date(2026, 7, 1),
        date_to=None,
        topic=None,
        tier=" mainstream ",
        language=" es ",
        sort="relevance",
    )
    canonical = SearchQuery(
        q="путин",
        country="ES",
        entity_id=entity_id.casefold(),
        date_from=date.fromisoformat("2026-07-01"),
        tier="mainstream",
        language="es",
        sort="relevance",
    )

    assert search_request_fingerprint(first) == search_request_fingerprint(canonical)
    assert search_request_fingerprint(first) != search_request_fingerprint(
        SearchQuery(**{**canonical.__dict__, "topic": "diplomacy"})
    )


def test_search_endpoint_never_returns_non_http_article_urls():
    app.dependency_overrides[get_search_service] = lambda: FakeSearchService(
        url="javascript:alert(1)"
    )
    try:
        response = TestClient(app).get("/api/v2/search/articles", params={"q": "Путин"})
    finally:
        app.dependency_overrides.pop(get_search_service, None)

    assert response.status_code == 200
    assert response.json()["items"][0]["url"] is None


def test_search_endpoint_rejects_malformed_cursor(search_client):
    response = search_client.get(
        "/api/v2/search/articles",
        params={"q": "Путин", "cursor": "A"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "invalid search cursor"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("article_id", 0),
        ("article_id", 2_147_483_648),
        ("article_id", True),
        ("relevance_score", True),
        ("snapshot_collected_article_id", 0),
        ("snapshot_collected_article_id", 2_147_483_648),
        ("snapshot_collected_article_id", True),
        ("snapshot_max_article_id", 0),
        ("snapshot_max_article_id", 2_147_483_648),
        ("snapshot_max_article_id", True),
    ],
)
def test_search_endpoint_rejects_invalid_numeric_cursor_values(
    search_client,
    field,
    value,
):
    params = {"q": "Путин", "country": "ES"}
    cursor = search_client.get(
        "/api/v2/search/articles",
        params=params,
    ).json()["next_cursor"]
    padded = cursor + "=" * (-len(cursor) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    payload[field] = value
    invalid_cursor = base64.urlsafe_b64encode(
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    ).decode("ascii").rstrip("=")

    response = search_client.get(
        "/api/v2/search/articles",
        params={**params, "cursor": invalid_cursor},
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "invalid search cursor"


def test_search_service_rejects_invalid_entity_uuid_stably():
    def database_must_not_run():
        pytest.fail("invalid entity UUID reached the database")

    with pytest.raises(ValueError, match="entity_id must be a valid UUID"):
        search_articles(
            SearchQuery(q="article", entity_id="not-a-uuid"),
            session_factory=database_must_not_run,
        )


def test_search_endpoint_rejects_reverse_date_range(search_client):
    response = search_client.get(
        "/api/v2/search/articles",
        params={"country": "ES", "from": "2026-07-15", "to": "2026-07-01"},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "from must not be after to"


def test_search_endpoint_returns_retryable_503_on_timeout():
    def timed_out_service(query):
        raise SearchTimeoutError("article search timed out")

    app.dependency_overrides[get_search_service] = lambda: timed_out_service
    try:
        response = TestClient(app, raise_server_exceptions=False).get(
            "/api/v2/search/articles",
            params={"q": "Путин"},
        )
    finally:
        app.dependency_overrides.pop(get_search_service, None)

    assert response.status_code == 503
    assert response.json()["detail"] == "article search timed out; retry the request"


def test_full_text_search_bounds_ids_before_expensive_rank_calculation():
    lexical_ids_sql = ARTICLE_SEARCH_SQL[
        ARTICLE_SEARCH_SQL.index("lexical_article_ids AS"):
        ARTICLE_SEARCH_SQL.index("full_text_candidate_ids AS")
    ]
    assert "full_text_candidate_ids AS" in ARTICLE_SEARCH_SQL
    candidate_ids_sql = ARTICLE_SEARCH_SQL[
        ARTICLE_SEARCH_SQL.index("full_text_candidate_ids AS"):
        ARTICLE_SEARCH_SQL.index("full_text_candidates AS")
    ]
    ranked_candidates_sql = ARTICLE_SEARCH_SQL[
        ARTICLE_SEARCH_SQL.index("full_text_candidates AS"):
        ARTICLE_SEARCH_SQL.index("trigram_candidates AS")
    ]

    assert "lexical_article_ids AS MATERIALIZED" in lexical_ids_sql
    assert "a.search_vector @@ sq.tsq" in lexical_ids_sql
    assert "NOT EXISTS (SELECT 1 FROM matching_entity_ids)" in " ".join(
        lexical_ids_sql.split()
    )
    assert "ORDER BY" not in lexical_ids_sql
    assert "FROM lexical_article_ids lexical" in candidate_ids_sql
    assert "JOIN articles a ON a.id = lexical.id" in candidate_ids_sql
    assert "a.search_vector @@ sq.tsq" not in candidate_ids_sql
    assert "ORDER BY a.published_at DESC, a.id DESC" in candidate_ids_sql
    assert "LIMIT :candidate_limit" in candidate_ids_sql
    assert "ts_rank_cd" not in candidate_ids_sql
    assert "FROM full_text_candidate_ids candidate" in ranked_candidates_sql


def test_exact_entity_match_skips_trigram_fallback():
    trigram_sql = ARTICLE_SEARCH_SQL[
        ARTICLE_SEARCH_SQL.index("trigram_candidates AS"):
        ARTICLE_SEARCH_SQL.index("entity_candidates AS")
    ]

    assert "NOT EXISTS (SELECT 1 FROM matching_entity_ids)" in " ".join(
        trigram_sql.split()
    )


def test_entity_candidates_filter_matches_before_the_global_candidate_cap():
    entity_sql = ARTICLE_SEARCH_SQL[
        ARTICLE_SEARCH_SQL.index("entity_candidates AS"):
        ARTICLE_SEARCH_SQL.index("topic_candidates AS")
    ]

    assert "source_filtered_articles" not in entity_sql
    assert "JOIN matching_sources s ON s.article_id = a.id" in entity_sql
    assert "ORDER BY a.published_at DESC, a.id DESC" in entity_sql
    assert "LIMIT :candidate_limit" in entity_sql


def test_full_text_ranking_avoids_loading_stored_body_vectors():
    ranked_candidates_sql = ARTICLE_SEARCH_SQL[
        ARTICLE_SEARCH_SQL.index("full_text_candidates AS"):
        ARTICLE_SEARCH_SQL.index("trigram_candidates AS")
    ]

    assert "ts_rank_cd" not in ranked_candidates_sql
    assert "to_tsvector('simple', COALESCE(a.title, ''))" in ranked_candidates_sql
    assert "to_tsvector('simple', COALESCE(a.summary, ''))" in ranked_candidates_sql
    assert "THEN 1.0" in ranked_candidates_sql
    assert "THEN 0.7" in ranked_candidates_sql
    assert "ELSE 0.35" in ranked_candidates_sql
    assert "a.body" not in ranked_candidates_sql


def test_search_sql_uses_verified_publishers_and_resolved_article_urls():
    compact_sql = " ".join(ARTICLE_SEARCH_SQL.split())

    assert "matching_sources AS NOT MATERIALIZED" in compact_sql
    assert "FROM article_country_facts s" in compact_sql
    assert "JOIN article_country_facts s ON s.article_id = a.id" in compact_sql
    assert "JOIN sources s ON s.id = a.source_id" not in compact_sql
    assert "JOIN matching_sources s ON s.article_id = a.id" in compact_sql
    assert "COALESCE(NULLIF(a.resolved_url, ''), a.url) AS url" in compact_sql


def test_country_candidates_keep_per_publisher_cap_before_structured_filters():
    publisher_candidates_sql = ARTICLE_SEARCH_SQL[
        ARTICLE_SEARCH_SQL.index("publisher_article_candidates AS"):
        ARTICLE_SEARCH_SQL.index("matching_sources AS")
    ]
    ranked_sql = ARTICLE_SEARCH_SQL[
        ARTICLE_SEARCH_SQL.index("source_ranked_articles AS"):
        ARTICLE_SEARCH_SQL.index("source_filtered_articles AS")
    ]
    filtered_sql = ARTICLE_SEARCH_SQL[
        ARTICLE_SEARCH_SQL.index("source_filtered_articles AS"):
        ARTICLE_SEARCH_SQL.index("matching_entity_ids AS")
    ]
    compact_ranked_sql = " ".join(ranked_sql.split())

    assert (
        "ROW_NUMBER() OVER ( PARTITION BY s.id ORDER BY "
        "candidate.published_at DESC, candidate.id DESC ) AS publisher_rank"
        in compact_ranked_sql
    )
    assert "candidate.source_id = publisher_filter.id" in publisher_candidates_sql
    assert (
        "candidate.publisher_source_id = publisher_filter.id"
        in publisher_candidates_sql
    )
    assert "JOIN LATERAL" in publisher_candidates_sql
    assert (
        "JOIN article_country_facts canonical_source "
        "ON canonical_source.article_id = candidate.id"
        in " ".join(publisher_candidates_sql.split())
    )
    assert "AND canonical_source.id = publisher_filter.id" in (
        publisher_candidates_sql
    )
    assert "ORDER BY candidate.published_at DESC, candidate.id DESC" in (
        publisher_candidates_sql
    )
    assert "LIMIT :candidate_limit" in publisher_candidates_sql
    assert "snapshot_state.snapshot_collected_at IS NULL" in (
        publisher_candidates_sql
    )
    assert "candidate.id <= snapshot_state.snapshot_max_article_id" in (
        publisher_candidates_sql
    )
    assert "FROM publisher_article_candidates publisher_candidate" in ranked_sql
    assert "JOIN article_country_facts s ON s.article_id = candidate.id" in ranked_sql
    assert "AND s.id = publisher_candidate.publisher_id" in ranked_sql
    assert "candidate.is_duplicate = FALSE" in publisher_candidates_sql
    assert ":topic" not in ranked_sql
    assert ":entity_id" not in ranked_sql
    assert ":date_from" not in ranked_sql
    assert ":date_to" not in ranked_sql
    assert ":language" not in ranked_sql
    assert "candidate.publisher_rank <= :candidate_limit" in filtered_sql
    assert ":topic" in filtered_sql
    assert ":entity_id" in filtered_sql
    assert ":date_from" in filtered_sql
    assert ":date_to" in filtered_sql
    assert ":language" in filtered_sql


def test_search_service_uses_parameterized_hybrid_candidates_and_deterministic_ranking():
    published_at = datetime.now(timezone.utc)
    rows = [
        SimpleNamespace(
            id=10,
            title="Досье о президенте России",
            summary="Точное каноническое упоминание.",
            url="https://example.es/entity",
            published_at=published_at,
            snapshot_collected_at=published_at,
            snapshot_collected_article_id=11,
            snapshot_max_article_id=11,
            language="es",
            source_name="Entidad",
            country_code="ES",
            tier="mainstream",
            source_weight=.8,
            topics=["diplomacy"],
            sentiment=1.0,
            action_level=2,
            matched_entities=[
                {
                    "id": "f100d4ce-b728-47c6-b109-7ee9f1408870",
                    "name": "Яндекс",
                    "kind": "organization",
                    "mention_text": "Яндекс",
                    "confidence": .8,
                    "exact_match": False,
                },
                {
                    "id": "87e18b65-e3e8-42a7-b38d-273366f6bd89",
                    "name": "Альфа",
                    "kind": "organization",
                    "mention_text": "Альфа",
                    "confidence": .7,
                    "exact_match": False,
                },
                {
                    "id": "93dbeaec-c20b-44ad-aaed-46b18ea86a47",
                    "name": "Владимир Путин",
                    "kind": "person",
                    "mention_text": "Путин",
                    "confidence": 1.0,
                    "exact_match": True,
                },
            ],
            exact_entity_match=True,
            story_id=None,
            story_slug=None,
            story_title=None,
            story_confidence=None,
            lexical_score=.3,
            lexical_field="body",
            match_snippet="...Путин...",
        ),
        SimpleNamespace(
            id=11,
            title="Большой текст о Путине",
            summary="Только текстовое совпадение.",
            url="https://example.es/body",
            published_at=published_at,
            snapshot_collected_at=published_at,
            snapshot_collected_article_id=11,
            snapshot_max_article_id=11,
            language="es",
            source_name="Texto",
            country_code="ES",
            tier="mainstream",
            source_weight=0,
            topics=[],
            sentiment=None,
            action_level=None,
            matched_entities=[],
            exact_entity_match=False,
            story_id=None,
            story_slug=None,
            story_title=None,
            story_confidence=None,
            lexical_score=.8,
            lexical_field="body",
            match_snippet="...Путине...",
        ),
    ]

    class FakeResult:
        def fetchall(self):
            return rows

    class FakeSession:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params=None):
            self.calls.append((str(statement), params or {}))
            return FakeResult()

    fake_session = FakeSession()

    @contextmanager
    def fake_session_factory():
        yield fake_session

    page = search_articles(
        SearchQuery(q="путин", country="ES", limit=25),
        session_factory=fake_session_factory,
        now=published_at,
    )

    assert [item["article_id"] for item in page["items"]] == [10, 11]
    assert page["items"][0]["scores"]["entity"] == 1
    assert page["items"][0]["scores"]["vector"] is None
    assert page["items"][1]["scores"]["trust"] == 0
    assert [
        entity["name"] for entity in page["items"][0]["matched_entities"]
    ] == ["Владимир Путин", "Альфа", "Яндекс"]
    assert page["items"][0]["why_included"] == (
        "Точное упоминание сущности «Владимир Путин»"
    )
    assert page["candidate_count"] == 2
    assert page["next_cursor"] is None

    sql, params = fake_session.calls[-1]
    compact_sql = " ".join(sql.split())
    assert "websearch_to_tsquery('simple', :q)" in sql
    assert "latest_article AS" in sql
    assert "snapshot AS" in sql
    assert "snapshot_state.snapshot_collected_at" in sql
    assert "article_entity_mentions" in sql
    assert "canonical_entities" in sql
    assert "@> ARRAY[CAST(:topic AS TEXT)]" in sql
    assert "full_text_count" not in sql
    assert "SELECT 1 FROM full_text_candidates OFFSET 9 LIMIT 1" in compact_sql
    assert "a.search_vector @@ sq.tsq" in sql
    assert "a.title_normalized % :q" in sql
    assert fake_session.calls[-2][0] == (
        "SET LOCAL pg_trgm.similarity_threshold = 0.1"
    )
    assert fake_session.calls[-3][0] == "SET LOCAL statement_timeout = '30s'"
    for candidate_source in (
        "entity_candidates AS",
        "topic_candidates AS",
        "story_candidates AS",
    ):
        assert candidate_source in sql
        assert sql.index(candidate_source) < sql.index("limited_candidates AS")
    language_candidates_sql = sql[
        sql.index("structured_language_candidates AS"):
        sql.index("candidate_sources AS")
    ]
    assert "ORDER BY a.published_at DESC, a.id DESC" in language_candidates_sql
    assert "LIMIT :candidate_limit" in language_candidates_sql
    publisher_candidates_sql = sql[
        sql.index("publisher_article_candidates AS"):
        sql.index("matching_sources AS")
    ]
    source_filtered_sql = sql[
        sql.index("source_filtered_articles AS"):
        sql.index("matching_entity_ids AS")
    ]
    assert "snapshot_state.snapshot_collected_at IS NULL" in (
        publisher_candidates_sql
    )
    assert "candidate.id <= snapshot_state.snapshot_max_article_id" in (
        publisher_candidates_sql
    )
    assert "PARTITION BY s.id" in sql
    assert "candidate.publisher_rank <= :candidate_limit" in source_filtered_sql
    structured_entity_sql = sql[
        sql.index("structured_entity_candidates AS"):
        sql.index("structured_topic_candidates AS")
    ]
    structured_topic_sql = sql[
        sql.index("structured_topic_candidates AS"):
        sql.index("structured_source_candidates AS")
    ]
    for structured_sql in (structured_entity_sql, structured_topic_sql):
        assert "FROM source_filtered_articles source_article" in structured_sql
        assert "(:country IS NOT NULL OR :tier IS NOT NULL)" in structured_sql
        assert ":country IS NULL" in structured_sql
        assert ":tier IS NULL" in structured_sql
    assert "candidate_hybrid_score" in sql
    assert "THEN candidate_hybrid_score END DESC" in sql
    assert "ROUND((" in sql
    assert ")::NUMERIC, 6)::DOUBLE PRECISION" in sql
    assert sql.index("ROUND((") < sql.index("limited_candidates AS")
    assert (
        "GREATEST(0.0, LEAST(1.0, COALESCE(s.weight, 0.5)"
        in compact_sql
    )
    assert (
        "GREATEST(0.0, LEAST(1.0, COALESCE(story_rank.story_score, 0.0)"
        in compact_sql
    )
    assert "WHEN :sort = 'newest' THEN published_at" in sql
    assert "FROM articles" in sql
    assert params["q"] == "путин"
    assert params["country"] == "ES"
    assert params["date_from"] is None
    assert params["sort"] == "relevance"
    assert params["ranking_at"] == published_at
    assert params["snapshot_collected_at"] is None
    assert params["snapshot_collected_article_id"] is None
    assert params["snapshot_max_article_id"] is None
    assert params["candidate_limit"] == 500

    first_page = search_articles(
        SearchQuery(q="путин", country="ES", limit=1),
        session_factory=fake_session_factory,
        now=published_at,
    )
    next_cursor = first_page["next_cursor"]
    cursor = encode_cursor(
        next_cursor["relevance_score"],
        datetime.fromisoformat(next_cursor["published_at"]),
        next_cursor["article_id"],
        datetime.fromisoformat(next_cursor["ranking_at"]),
        datetime.fromisoformat(next_cursor["snapshot_collected_at"]),
        next_cursor["snapshot_collected_article_id"],
        next_cursor["snapshot_max_article_id"],
        next_cursor["request_fingerprint"],
    )
    later_page = search_articles(
        SearchQuery(q="путин", country="ES", cursor=cursor),
        session_factory=fake_session_factory,
        now=published_at + timedelta(days=30),
    )
    _, later_params = fake_session.calls[-1]
    assert later_params["ranking_at"] == published_at
    assert later_params["snapshot_collected_at"] == published_at
    assert later_params["snapshot_collected_article_id"] == 11
    assert later_params["snapshot_max_article_id"] == 11
    assert [item["article_id"] for item in later_page["items"]] == [11]
    assert all(item["scores"]["freshness"] == 1 for item in later_page["items"])

    topic_page = search_articles(
        SearchQuery(q="diplomacy"),
        session_factory=fake_session_factory,
        now=published_at,
    )
    topic_item = next(
        item for item in topic_page["items"] if item["article_id"] == 10
    )
    assert topic_item["scores"]["topic"] == 1
    assert {
        "type": "topic",
        "article_id": 10,
        "topic": "diplomacy",
    } in topic_item["evidence"]


def test_cursor_snapshot_excludes_articles_ingested_between_pages():
    ranking_at = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)
    snapshot_at = ranking_at - timedelta(minutes=5)

    def make_row(article_id, lexical_score, collected_at):
        return SimpleNamespace(
            id=article_id,
            title=f"Article {article_id}",
            summary=None,
            url=f"https://example.test/{article_id}",
            published_at=ranking_at - timedelta(days=1),
            collected_at=collected_at,
            language="en",
            source_name="Example",
            country_code="ES",
            tier="mainstream",
            source_weight=.5,
            topics=[],
            sentiment=None,
            action_level=None,
            matched_entities=[],
            exact_entity_match=False,
            story_id=None,
            story_slug=None,
            story_title=None,
            story_confidence=None,
            lexical_score=lexical_score,
            lexical_field="title",
            match_snippet=f"Article {article_id}",
        )

    first_pool = [
        make_row(10, .9, snapshot_at - timedelta(seconds=1)),
        make_row(20, .8, snapshot_at),
    ]
    changed_pool = [
        *first_pool,
        make_row(30, .7, snapshot_at - timedelta(seconds=2)),
        make_row(40, .6, None),
    ]

    class FakeResult:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class ChangingPoolSession:
        def __init__(self):
            self.query_count = 0
            self.query_params = []

        def execute(self, statement, params=None):
            if params is None:
                return FakeResult([])
            self.query_params.append(params)
            pool = first_pool if self.query_count == 0 else changed_pool
            self.query_count += 1
            cutoff_at = params.get("snapshot_collected_at")
            cutoff_id = params.get("snapshot_collected_article_id")
            max_article_id = params.get("snapshot_max_article_id")
            if cutoff_at is not None:
                pool = [
                    row for row in pool
                    if (row.collected_at or row.published_at, row.id)
                    <= (cutoff_at, cutoff_id)
                ]
            if max_article_id is not None:
                pool = [row for row in pool if row.id <= max_article_id]
            rows = [
                SimpleNamespace(
                    **vars(row),
                    snapshot_collected_at=snapshot_at,
                    snapshot_collected_article_id=20,
                    snapshot_max_article_id=20,
                )
                for row in pool
            ]
            return FakeResult(rows)

    session = ChangingPoolSession()

    @contextmanager
    def session_factory():
        yield session

    first_page = search_articles(
        SearchQuery(q="article", limit=1),
        session_factory=session_factory,
        now=ranking_at,
    )
    cursor_data = first_page["next_cursor"]
    assert cursor_data["snapshot_collected_at"] == snapshot_at.isoformat()
    assert cursor_data["snapshot_collected_article_id"] == 20
    assert cursor_data["snapshot_max_article_id"] == 20

    cursor = encode_cursor(
        cursor_data["relevance_score"],
        datetime.fromisoformat(cursor_data["published_at"]),
        cursor_data["article_id"],
        datetime.fromisoformat(cursor_data["ranking_at"]),
        datetime.fromisoformat(cursor_data["snapshot_collected_at"]),
        cursor_data["snapshot_collected_article_id"],
        cursor_data["snapshot_max_article_id"],
        cursor_data["request_fingerprint"],
    )
    second_page = search_articles(
        SearchQuery(q="article", cursor=cursor),
        session_factory=session_factory,
        now=ranking_at + timedelta(days=1),
    )

    assert [item["article_id"] for item in second_page["items"]] == [20]
    assert session.query_params[-1]["snapshot_collected_at"] == snapshot_at
    assert session.query_params[-1]["snapshot_collected_article_id"] == 20
    assert session.query_params[-1]["snapshot_max_article_id"] == 20
