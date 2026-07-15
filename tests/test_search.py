from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes.search import get_search_service
from src.search import (
    SearchQuery,
    SearchTimeoutError,
    combine_scores,
    decode_cursor,
    encode_cursor,
    explain_match,
    normalize_query,
    search_articles,
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
    first = encode_cursor(.712345, published_at, 321)
    second = encode_cursor(.712345, published_at, 321)
    assert first == second
    assert decode_cursor(first) == (.712345, published_at, 321)


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


def test_search_service_uses_parameterized_hybrid_candidates_and_deterministic_ranking():
    published_at = datetime.now(timezone.utc)
    rows = [
        SimpleNamespace(
            id=10,
            title="Досье о президенте России",
            summary="Точное каноническое упоминание.",
            url="https://example.es/entity",
            published_at=published_at,
            language="es",
            source_name="Entidad",
            country_code="ES",
            tier="mainstream",
            source_weight=.8,
            topics=["diplomacy"],
            sentiment=1.0,
            action_level=2,
            matched_entities=[{
                "id": "93dbeaec-c20b-44ad-aaed-46b18ea86a47",
                "name": "Владимир Путин",
                "kind": "person",
                "mention_text": "Путин",
                "confidence": 1.0,
                "exact_match": True,
            }],
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
    assert page["items"][0]["why_included"] == (
        "Точное упоминание сущности «Владимир Путин»"
    )
    assert page["candidate_count"] == 2
    assert page["next_cursor"] is None

    sql, params = fake_session.calls[-1]
    assert "websearch_to_tsquery('simple', :q)" in sql
    assert "article_entity_mentions" in sql
    assert "canonical_entities" in sql
    assert "@> ARRAY[CAST(:topic AS TEXT)]" in sql
    assert "full_text_count" in sql
    assert "< 10" in sql
    assert "WHEN :sort = 'newest' THEN published_at" in sql
    assert "FROM articles" in sql
    assert params["q"] == "путин"
    assert params["country"] == "ES"
    assert params["sort"] == "relevance"
    assert params["candidate_limit"] == 500
