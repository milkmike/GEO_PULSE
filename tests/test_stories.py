from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import stories as stories_routes
from src.stories import (
    StoryCandidate,
    cluster_story_candidates,
    compute_source_hash,
    deterministic_story_copy,
    persist_story_cluster,
    resolve_story_copy,
    score_story_match,
    should_merge,
    transition_lifecycle,
)


NOW = datetime(2026, 7, 15, 12, tzinfo=timezone.utc)


def candidate(
    country: str,
    *,
    event_key: str = "переговоры о транскаспийском маршруте",
    title: str = "Страны обсудили транскаспийский маршрут",
    entities: frozenset[str] = frozenset({"entity-route", "entity-minister"}),
    topics: frozenset[str] = frozenset({"energy", "diplomacy"}),
    first_seen: datetime = NOW - timedelta(days=1),
    last_seen: datetime = NOW,
) -> StoryCandidate:
    article_id = 1 if country == "AZ" else 2
    return StoryCandidate(
        thread_id=article_id,
        country_code=country,
        event_key=event_key,
        title=title,
        article_ids=(article_id,),
        entities=entities,
        topics=topics,
        sources=frozenset({f"source-{country.lower()}"}),
        first_seen=first_seen,
        last_seen=last_seen,
        highest_action_level=3,
    )


def test_matching_event_and_entity_across_countries_merge():
    similarity = score_story_match(candidate("AZ"), candidate("KZ"))

    assert similarity.components["event_key"] == 1.0
    assert similarity.components["entities"] > 0
    assert {"event_key", "entities"}.issubset(similarity.matched_features)
    assert should_merge(similarity)


def test_title_only_similarity_does_not_merge():
    left = candidate(
        "AZ",
        event_key="визит министра в баку",
        entities=frozenset(),
        topics=frozenset(),
    )
    right = candidate(
        "KZ",
        event_key="торговая статистика казахстана",
        entities=frozenset(),
        topics=frozenset(),
    )

    similarity = score_story_match(left, right)

    assert similarity.components["title"] == 1.0
    assert not should_merge(similarity)


def test_merge_threshold_is_inclusive_and_requires_two_features():
    baseline = score_story_match(candidate("AZ"), candidate("KZ"))

    assert not should_merge(replace(baseline, total=0.64))
    assert should_merge(
        replace(baseline, total=0.65, matched_features=frozenset({"event_key", "entities"}))
    )
    assert not should_merge(
        replace(baseline, total=0.90, matched_features=frozenset({"event_key"}))
    )


def test_gap_over_fourteen_days_needs_explicit_reactivation():
    old = candidate(
        "AZ",
        first_seen=NOW - timedelta(days=18),
        last_seen=NOW - timedelta(days=15, seconds=1),
    )
    new = candidate("KZ", first_seen=NOW, last_seen=NOW)
    similarity = score_story_match(old, new)

    assert similarity.gap_days > 14
    assert not should_merge(similarity)
    assert should_merge(similarity, explicit_reactivation=True)


def test_lifecycle_transitions_are_deterministic():
    common = {
        "now": NOW,
        "first_seen": NOW - timedelta(days=3),
        "article_count": 8,
        "recent_article_count": 2,
        "previous_article_count": 4,
        "highest_action_level": 3,
        "previous_action_level": 3,
    }

    assert transition_lifecycle(last_seen=NOW - timedelta(days=15), **common) == "resolved"
    assert transition_lifecycle(last_seen=NOW - timedelta(days=4), **common) == "cooling"
    assert transition_lifecycle(last_seen=NOW, **common) == "developing"
    assert transition_lifecycle(
        last_seen=NOW,
        **{**common, "recent_article_count": 5, "previous_article_count": 2},
    ) == "escalating"
    assert transition_lifecycle(
        now=NOW,
        first_seen=NOW - timedelta(hours=4),
        last_seen=NOW,
        article_count=2,
        recent_article_count=2,
        previous_article_count=0,
        highest_action_level=1,
        previous_action_level=1,
    ) == "emerging"


def test_cluster_builder_emits_only_cross_country_stories():
    cross_country = cluster_story_candidates([candidate("AZ"), candidate("KZ")])
    same_country = cluster_story_candidates([candidate("AZ"), replace(candidate("AZ"), thread_id=9)])

    assert len(cross_country) == 1
    assert {item.country_code for item in cross_country[0]} == {"AZ", "KZ"}
    assert same_country == []


def test_story_copy_is_deterministic_and_llm_runs_only_for_changed_hash():
    candidates = [candidate("AZ"), candidate("KZ")]
    fallback = deterministic_story_copy(candidates)
    source_hash = compute_source_hash(candidates)
    calls = []

    def summarize(payload):
        calls.append(payload)
        return {"title_ru": "Новый заголовок", "summary": "Новое резюме"}

    unchanged = resolve_story_copy(
        candidates,
        source_hash=source_hash,
        previous_source_hash=source_hash,
        previous_copy=fallback,
        summarizer=summarize,
    )
    changed = resolve_story_copy(
        candidates,
        source_hash=source_hash,
        previous_source_hash="old-hash",
        previous_copy=fallback,
        summarizer=summarize,
    )

    assert unchanged == fallback
    assert calls and len(calls) == 1
    assert changed.title_ru == "Новый заголовок"
    assert changed.summary == "Новое резюме"
    assert deterministic_story_copy(list(reversed(candidates))) == fallback


class FakeResult:
    def __init__(self, *, rows=None, row=None):
        self._rows = rows or []
        self._row = row

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._row


class PersistenceSession:
    def __init__(self):
        self.statements = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        if "SELECT st.id, st.slug" in sql:
            return FakeResult(row=None)
        if "INSERT INTO stories" in sql:
            return FakeResult(row=(42,))
        return FakeResult()


def story_row(story_id: int, *, last_seen: datetime = NOW):
    return SimpleNamespace(
        id=story_id,
        slug=f"story-{story_id}",
        title_ru="Транскаспийский маршрут",
        title_en=None,
        summary="Сюжет объединяет сообщения двух стран.",
        lifecycle="developing",
        first_seen=NOW - timedelta(days=2),
        last_seen=last_seen,
        article_count=4,
        source_count=3,
        country_count=2,
        highest_action_level=3,
        clustering_confidence=0.81,
        generated_at=NOW,
        countries=["AZ", "KZ"],
        primary_url="https://example.test/latest",
    )


class FakeStorySession:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        self.calls.append((sql, params))
        if "WHERE st.id = :story_id" in sql:
            return FakeResult(row=story_row(7) if params["story_id"] == 7 else None)
        if "FROM story_countries sc" in sql and "json" not in sql.lower():
            return FakeResult(rows=[SimpleNamespace(
                country_code="AZ", article_count=2, source_count=2, media_tone=-0.2,
                first_seen=NOW - timedelta(days=2), last_seen=NOW,
                primary_url="https://example.test/az",
            )])
        if "FROM story_entities se" in sql:
            return FakeResult(rows=[SimpleNamespace(
                entity_id="entity-route", mentions=3, confidence=0.9,
                evidence={"article_ids": [1, 2]},
            )])
        if "FROM story_events sve" in sql:
            return FakeResult(rows=[SimpleNamespace(
                entity_id="entity-route", event_key="транскаспийский маршрут",
                event_at=NOW, action_level=3, evidence={"articles": [1, 2]},
            )])
        if "FROM story_articles sa" in sql:
            return FakeResult(rows=[SimpleNamespace(
                article_id=1, title="Новость", url="https://example.test/az",
                published_at=NOW, source="Источник", country_code="AZ",
                membership_confidence=0.81, evidence={"event_key": 1.0},
            )])
        return FakeResult(rows=[story_row(7), story_row(6, last_seen=NOW - timedelta(hours=1))])


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc, traceback):
        return False


def story_client(monkeypatch):
    session = FakeStorySession()
    monkeypatch.setattr(stories_routes, "get_session", lambda: FakeSessionContext(session))
    app = FastAPI()
    app.include_router(stories_routes.router)
    return TestClient(app), session


def test_story_list_filters_cursor_and_primary_url(monkeypatch):
    client, session = story_client(monkeypatch)

    response = client.get(
        "/api/v2/stories?country=AZ&lifecycle=developing&min_confidence=0.7&limit=1"
    )

    assert response.status_code == 200
    payload = response.json()
    assert [story["id"] for story in payload["stories"]] == [7]
    assert payload["stories"][0]["primary_url"] == "https://example.test/latest"
    assert payload["next_cursor"]
    _, params = session.calls[0]
    assert params["country"] == "AZ"
    assert params["lifecycle"] == "developing"
    assert params["min_confidence"] == 0.7


def test_story_detail_includes_evidence_and_country_primary_urls(monkeypatch):
    client, _ = story_client(monkeypatch)

    response = client.get("/api/v2/stories/7")

    assert response.status_code == 200
    payload = response.json()
    assert payload["countries"][0]["primary_url"] == "https://example.test/az"
    assert payload["entities"][0]["evidence"] == {"article_ids": [1, 2]}
    assert payload["events"][0]["evidence"] == {"articles": [1, 2]}
    assert payload["articles"][0]["is_primary"] is True


def test_country_story_slice_and_missing_story(monkeypatch):
    client, _ = story_client(monkeypatch)

    country = client.get("/api/v2/countries/AZ/stories?limit=1")
    missing = client.get("/api/v2/stories/999")

    assert country.status_code == 200
    assert country.json()["country"] == "AZ"
    assert country.json()["stories"][0]["countries"] == ["AZ", "KZ"]
    assert missing.status_code == 404


def test_invalid_story_cursor_is_rejected(monkeypatch):
    client, _ = story_client(monkeypatch)

    response = client.get("/api/v2/stories", params={"cursor": "☃"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid story cursor"


def test_slug_conflict_refreshes_story_header_before_memberships_change():
    session = PersistenceSession()

    persist_story_cluster(session, [candidate("AZ"), candidate("KZ")], now=NOW)

    insert_sql = next(sql for sql in session.statements if "INSERT INTO stories" in sql)
    conflict_sql = insert_sql.split("ON CONFLICT (slug) DO UPDATE SET", 1)[1]
    assert "title_ru = EXCLUDED.title_ru" in conflict_sql
    assert "source_hash = EXCLUDED.source_hash" in conflict_sql
    assert "article_count = EXCLUDED.article_count" in conflict_sql


def test_persistence_recomputes_header_counts_from_saved_memberships():
    session = PersistenceSession()

    persist_story_cluster(session, [candidate("AZ"), candidate("KZ")], now=NOW)

    aggregate_sql = "\n".join(session.statements)
    assert "UPDATE stories st SET" in aggregate_sql
    assert "COUNT(DISTINCT ar.source_id)" in aggregate_sql
    assert "COUNT(DISTINCT s.country_code)" in aggregate_sql
    assert "MAX(COALESCE(an.action_level, 1))" in aggregate_sql


def test_persistence_checks_deterministic_slug_before_summary_generation():
    session = PersistenceSession()

    persist_story_cluster(session, [candidate("AZ"), candidate("KZ")], now=NOW)

    lookup_sql = next(sql for sql in session.statements if "SELECT st.id, st.slug" in sql)
    assert "st.slug = :slug" in lookup_sql
