import json
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import stories as stories_routes
from src.stories import (
    StoryCandidate,
    StoryArticle,
    build_stories,
    cluster_story_candidates,
    compute_source_hash,
    deterministic_story_copy,
    merge_rejection_reasons,
    persist_story_cluster,
    resolve_story_copy,
    score_story_match,
    should_merge,
    transition_lifecycle,
    _story_slug,
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


def test_identical_event_key_does_not_double_count_title_similarity():
    left = candidate("AZ", title="Одинаковый заголовок")
    same_title = candidate("KZ", title="Одинаковый заголовок")
    different_title = candidate("KZ", title="Совершенно другая формулировка")

    same_score = score_story_match(left, same_title)
    different_score = score_story_match(left, different_title)

    assert same_score.components["event_key"] == 1.0
    assert same_score.total == different_score.total
    assert "title" not in same_score.matched_features


def test_tiny_entity_and_topic_overlap_is_not_independent_evidence():
    shared_entity = {"shared"}
    shared_topic = {"shared-topic"}
    left = candidate(
        "AZ",
        entities=frozenset(shared_entity | {f"left-{index}" for index in range(9)}),
        topics=frozenset(shared_topic | {f"left-topic-{index}" for index in range(9)}),
    )
    right = candidate(
        "KZ",
        entities=frozenset(shared_entity | {f"right-{index}" for index in range(9)}),
        topics=frozenset(shared_topic | {f"right-topic-{index}" for index in range(9)}),
    )

    similarity = score_story_match(left, right)

    assert similarity.components["entities"] == 0
    assert similarity.components["topics"] == 0
    assert "entities" not in similarity.matched_features
    assert "topics" not in similarity.matched_features


def test_gap_over_fourteen_days_needs_explicit_reactivation():
    old = candidate(
        "AZ",
        first_seen=NOW - timedelta(days=18),
        last_seen=NOW - timedelta(days=15, seconds=1),
    )
    new = candidate("KZ", first_seen=NOW, last_seen=NOW)
    similarity = replace(score_story_match(old, new), total=0.65)

    assert similarity.gap_days > 14
    assert not should_merge(similarity)
    assert should_merge(similarity, explicit_reactivation=True)


def test_reactivation_requires_matching_event_and_entity_evidence():
    old = candidate(
        "AZ",
        entities=frozenset(),
        first_seen=NOW - timedelta(days=20),
        last_seen=NOW - timedelta(days=15),
    )
    new = candidate(
        "KZ",
        entities=frozenset(),
        first_seen=NOW,
        last_seen=NOW,
    )

    similarity = replace(score_story_match(old, new), total=0.65)

    assert similarity.gap_days == 15
    assert similarity.total >= 0.65
    assert not should_merge(similarity, explicit_reactivation=True)
    assert "reactivation_requires_event_and_entity" in merge_rejection_reasons(
        similarity, explicit_reactivation=True
    )


def test_article_activity_dates_expose_gap_hidden_by_thread_envelope():
    old = replace(
        candidate(
            "AZ",
            first_seen=NOW - timedelta(days=40),
            last_seen=NOW,
        ),
        articles=(
            StoryArticle(1, "AZ", "old", None, NOW - timedelta(days=40), "az-1"),
            StoryArticle(3, "AZ", "new", None, NOW, "az-2"),
        ),
    )
    middle = replace(
        candidate(
            "KZ",
            first_seen=NOW - timedelta(days=20),
            last_seen=NOW - timedelta(days=20),
        ),
        articles=(
            StoryArticle(2, "KZ", "middle", None, NOW - timedelta(days=20), "kz-1"),
        ),
    )

    similarity = score_story_match(old, middle)

    assert similarity.gap_days == 20
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


def test_single_link_chain_cannot_bridge_a_non_cohesive_cluster():
    left = candidate(
        "AZ",
        event_key="транскаспийский маршрут альфа",
        title="транскаспийский маршрут альфа",
        entities=frozenset({"x"}),
    )
    bridge = replace(
        candidate("KZ"),
        event_key="транскаспийский маршрут альфа бета",
        title="транскаспийский маршрут альфа бета",
        entities=frozenset({"x", "y"}),
    )
    unrelated_same_country = replace(
        candidate("AZ"),
        thread_id=3,
        article_ids=(3,),
        event_key="транскаспийский маршрут бета",
        title="транскаспийский маршрут бета",
        entities=frozenset({"y"}),
    )

    assert should_merge(score_story_match(left, bridge))
    assert should_merge(score_story_match(bridge, unrelated_same_country))
    assert not should_merge(score_story_match(left, unrelated_same_country))
    clusters = cluster_story_candidates([left, bridge, unrelated_same_country])
    assert all(len(cluster) == 2 for cluster in clusters)
    assert not any(
        {item.thread_id for item in cluster} == {1, 2, 3}
        for cluster in clusters
    )


def test_non_cohesive_cluster_is_rejected_before_persistence():
    left = candidate("AZ", event_key="маршрут альфа", entities=frozenset({"x"}))
    bridge = replace(
        candidate("KZ"),
        event_key="маршрут альфа бета",
        entities=frozenset({"x", "y"}),
    )
    unrelated = replace(
        candidate("AZ"), thread_id=3, article_ids=(3,),
        event_key="маршрут бета", entities=frozenset({"y"}),
    )
    session = PersistenceSession()

    with pytest.raises(ValueError, match="cohesion"):
        persist_story_cluster(session, [left, bridge, unrelated], now=NOW)

    assert session.calls == []


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


def test_source_hash_changes_when_copy_action_priority_changes():
    baseline = [candidate("AZ"), candidate("KZ")]
    escalated = [replace(baseline[0], highest_action_level=4), baseline[1]]

    assert compute_source_hash(baseline) != compute_source_hash(escalated)


def test_source_hash_tracks_raw_copy_text_changes():
    baseline = [candidate("AZ", title="Headline."), candidate("KZ")]
    punctuation_changed = [replace(baseline[0], title="headline"), baseline[1]]

    assert deterministic_story_copy(baseline).title_ru != deterministic_story_copy(
        punctuation_changed
    ).title_ru
    assert compute_source_hash(baseline) != compute_source_hash(punctuation_changed)


def test_copy_hash_and_summary_include_final_persisted_membership_union():
    candidates = [candidate("AZ"), candidate("KZ")]
    current_hash = compute_source_hash(candidates)
    union_hash = compute_source_hash(
        candidates,
        member_article_ids=(1, 2, 99),
        member_evidence=({"article_id": 99, "title": "Historical evidence"},),
    )
    copy = deterministic_story_copy(
        candidates,
        member_article_ids=(1, 2, 99),
        member_evidence=({"article_id": 99, "country_code": "UZ"},),
    )

    assert union_hash != current_hash
    assert "3 публикаций" in copy.summary
    assert "UZ" in copy.summary


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
        self.calls = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        self.calls.append((sql, params or {}))
        if "SELECT st.id, st.slug" in sql:
            return FakeResult(row=None)
        if "INSERT INTO stories" in sql:
            return FakeResult(row=(42,))
        return FakeResult()


class DuplicatePersistenceSession(PersistenceSession):
    def __init__(self):
        super().__init__()
        self.matches = [
            SimpleNamespace(
                id=10, slug="story-primary", title_ru="Primary", title_en=None,
                summary="Primary summary", summary_model=None, source_hash="old",
                article_count=2, highest_action_level=2, generated_at=NOW,
                meta={"merge_audit": [{"decision_id": "primary-audit"}]},
                article_overlap=2, thread_overlap=1,
                member_article_ids=[1, 2, 90],
            ),
            SimpleNamespace(
                id=11, slug="story-duplicate", title_ru="Duplicate", title_en=None,
                summary="Duplicate summary", summary_model=None, source_hash="older",
                article_count=3, highest_action_level=2, generated_at=NOW,
                meta={
                    "merge_audit": [{"decision_id": "duplicate-audit"}],
                    "reactivations": [{"decision_id": "reactivation-audit"}],
                },
                article_overlap=1, thread_overlap=1,
                member_article_ids=[2, 91],
            ),
        ]

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        self.calls.append((sql, params or {}))
        if "SELECT st.id, st.slug" in sql:
            return FakeResult(rows=self.matches, row=self.matches[0])
        if "UPDATE stories SET" in sql and "RETURNING id" in sql:
            return FakeResult(row=(10,))
        return FakeResult()


class UnchangedPersistenceSession(PersistenceSession):
    def __init__(self, cluster):
        super().__init__()
        self.generated_at = NOW - timedelta(days=2)
        self.match = SimpleNamespace(
            id=10, slug="story-stable", title_ru="Stable", title_en=None,
            summary="Stable summary", summary_model="model",
            source_hash=compute_source_hash(cluster), article_count=2,
            highest_action_level=3, generated_at=self.generated_at, meta={},
            article_overlap=2, thread_overlap=2,
        )

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        self.calls.append((sql, params or {}))
        if "SELECT st.id, st.slug" in sql:
            return FakeResult(rows=[self.match], row=self.match)
        if "UPDATE stories SET" in sql and "RETURNING id" in sql:
            return FakeResult(row=(10,))
        return FakeResult()


class ResolvedPersistenceSession(PersistenceSession):
    def __init__(self):
        super().__init__()
        self.match = SimpleNamespace(
            id=10,
            slug="story-resolved",
            title_ru="Resolved",
            title_en=None,
            summary="Resolved summary",
            summary_model=None,
            source_hash="old-source-hash",
            article_count=2,
            highest_action_level=3,
            generated_at=NOW - timedelta(days=15),
            lifecycle="resolved",
            first_seen=NOW - timedelta(days=30),
            last_seen=NOW - timedelta(days=15),
            meta={"thread_ids": [1, 2]},
            article_overlap=0,
            thread_overlap=2,
            member_article_ids=[90, 91],
        )

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        self.calls.append((sql, params or {}))
        if "SELECT st.id, st.slug" in sql:
            return FakeResult(rows=[self.match], row=self.match)
        if "UPDATE stories SET" in sql and "RETURNING id" in sql:
            return FakeResult(row=(10,))
        if "INSERT INTO stories" in sql:
            return FakeResult(row=(42,))
        return FakeResult()


class DeniedReactivationPersistenceSession(PersistenceSession):
    def __init__(self, cluster):
        super().__init__()
        old_first_seen = NOW - timedelta(days=30)
        self.old_story = SimpleNamespace(
            id=10,
            slug=_story_slug(cluster, old_first_seen),
            title_ru="Resolved",
            title_en=None,
            summary="Resolved summary",
            summary_model=None,
            source_hash="old-source-hash",
            article_count=2,
            highest_action_level=3,
            generated_at=NOW - timedelta(days=15),
            lifecycle="resolved",
            first_seen=old_first_seen,
            last_seen=NOW - timedelta(days=15),
            meta={"thread_ids": [1, 2]},
            article_overlap=2,
            thread_overlap=2,
            member_article_ids=[90, 91],
        )
        self.active_story = None

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        self.statements.append(sql)
        self.calls.append((sql, params))
        if "SELECT st.id, st.slug" in sql:
            rows = (
                [self.active_story, self.old_story]
                if self.active_story is not None
                else [self.old_story]
            )
            return FakeResult(rows=rows, row=rows[0])
        if "UPDATE stories SET" in sql and "RETURNING id" in sql:
            return FakeResult(row=(params["story_id"],))
        if "INSERT INTO stories" in sql:
            if params["slug"] == self.old_story.slug:
                return FakeResult(row=None)
            self.active_story = SimpleNamespace(
                id=42,
                slug=params["slug"],
                title_ru=params["title_ru"],
                title_en=params["title_en"],
                summary=params["summary"],
                summary_model=params["summary_model"],
                source_hash=params["source_hash"],
                article_count=params["article_count"],
                highest_action_level=params["highest_action_level"],
                generated_at=params["generated_at"],
                lifecycle=params["lifecycle"],
                first_seen=params["first_seen"],
                last_seen=params["last_seen"],
                meta=json.loads(params["meta"]),
                article_overlap=4,
                thread_overlap=2,
                member_article_ids=[1, 2, 90, 91],
            )
            return FakeResult(row=(42,))
        return FakeResult()


class IndependentSlugCollisionSession(PersistenceSession):
    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        self.calls.append((sql, params or {}))
        if "SELECT st.id, st.slug" in sql:
            return FakeResult(row=None)
        if "INSERT INTO stories" in sql:
            return FakeResult(row=None)
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
        relevance_score=0.88,
        meta={"topics": ["energy"], "merge_audit": [{"decision": "merge"}]},
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
                evidence={"article_ids": [1, 2]}, canonical_name="Route",
                kind="infrastructure",
            )])
        if "FROM story_events sve" in sql:
            return FakeResult(rows=[SimpleNamespace(
                entity_id="entity-route", event_key="транскаспийский маршрут",
                event_at=NOW, action_level=3, evidence={"articles": [1, 2]},
                confidence=0.9,
            )])
        if "WITH ranked_articles" in sql:
            return FakeResult(rows=[SimpleNamespace(
                article_id=1, title="Новость", url="https://example.test/az",
                published_at=NOW, source="Источник", country_code="AZ",
                membership_confidence=0.81, evidence={"event_key": 1.0},
                relevance_score=0.79, is_primary=True,
            )])
        if "candidate_links AS" in sql or "WITH story_windows AS" in sql:
            return FakeResult(rows=[])
        return FakeResult(rows=[story_row(7), story_row(6, last_seen=NOW - timedelta(hours=1))])


class LinkedStoryContextSession(FakeStorySession):
    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "candidate_links AS" in sql:
            self.calls.append((sql, params))
            return FakeResult(rows=[SimpleNamespace(
                story_id=7,
                linked_signal_count=2,
                linked_signals=[{
                    "id": 91,
                    "type": "index_shift",
                    "severity": "warning",
                    "title": "Индекс Азербайджана изменился",
                    "created_at": NOW.isoformat(),
                    "confidence": 0.83,
                    "completeness": "complete",
                    "relation": "explicit_story_evidence",
                    "evidence": {
                        "source": "signal_evidence.story_ids",
                        "story_id": 7,
                    },
                }],
            )])
        if "WITH story_windows AS" in sql:
            self.calls.append((sql, params))
            return FakeResult(rows=[
                SimpleNamespace(
                    story_id=7,
                    country_code="AZ",
                    point_time=NOW,
                    score=-12.0,
                    delta_24h=-8.0,
                    version="v1",
                ),
                SimpleNamespace(
                    story_id=7,
                    country_code="KZ",
                    point_time=NOW - timedelta(hours=2),
                    score=7.0,
                    delta_24h=4.5,
                    version="v1",
                ),
            ])
        return super().execute(statement, params)


class SupersededSignalStoryContextSession(FakeStorySession):
    """Signal evidence points only at old story 11; page 7 is canonical."""

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "WITH RECURSIVE story_aliases AS" in sql:
            self.calls.append((sql, params))
            return FakeResult(rows=[SimpleNamespace(
                story_id=7,
                linked_signal_count=1,
                linked_signals=[{
                    "id": 92,
                    "type": "index_shift",
                    "severity": "warning",
                    "title": "Сигнал старого сюжета",
                    "created_at": NOW.isoformat(),
                    "confidence": 0.79,
                    "completeness": "complete",
                    "relation": "explicit_story_evidence",
                    "evidence": {
                        "source": "signal_evidence.story_ids",
                        "story_id": 7,
                        "matched_story_id": 11,
                    },
                }],
            )])
        return super().execute(statement, params)


class CountryStoryContextSession(FakeStorySession):
    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "SELECT sc.story_id," in sql:
            self.calls.append((sql, params))
            return FakeResult(rows=[SimpleNamespace(
                story_id=7,
                country_code="AZ",
                article_count=3,
                source_count=2,
                media_tone=-1.25,
            )])
        return super().execute(statement, params)


class StoryCoverageSession(FakeStorySession):
    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "AS available_from" in sql:
            self.calls.append((sql, params))
            return FakeResult(row=SimpleNamespace(
                available_from=NOW - timedelta(days=120),
                available_to=NOW - timedelta(minutes=5),
            ))
        return super().execute(statement, params)


class MutableStoryAggregateSession(FakeStorySession):
    def __init__(self):
        super().__init__()
        self.first = story_row(7)
        self.second = story_row(6, last_seen=NOW - timedelta(hours=1))

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "ORDER BY lifecycle_rank ASC" in sql:
            self.calls.append((sql, params))
            if "cursor_lifecycle_rank" in params:
                return FakeResult(rows=[self.second])
            return FakeResult(rows=[self.first, self.second])
        return super().execute(statement, params)


class UnsafeUrlStorySession(FakeStorySession):
    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        self.calls.append((sql, params))
        unsafe_story = story_row(7)
        unsafe_story.primary_url = "data:text/html,boom"
        if "WHERE st.id = :story_id" in sql:
            return FakeResult(row=unsafe_story)
        if "FROM story_countries sc" in sql and "json" not in sql.lower():
            return FakeResult(rows=[SimpleNamespace(
                country_code="AZ", article_count=4, source_count=2, media_tone=-0.2,
                first_seen=NOW - timedelta(days=2), last_seen=NOW,
                primary_url="javascript:alert(1)",
            )])
        if "FROM story_entities se" in sql:
            return super().execute(statement, params)
        if "FROM story_events sve" in sql:
            return super().execute(statement, params)
        if "FROM story_articles sa" in sql:
            urls = [
                "javascript:alert(1)",
                "data:text/html,boom",
                "https:///missing-host",
                "https://safe.example/story",
            ]
            return FakeResult(rows=[SimpleNamespace(
                article_id=index, title=f"Article {index}", url=url,
                published_at=NOW - timedelta(minutes=index), source="Source",
                country_code="AZ", membership_confidence=0.8,
                evidence={"matched_features": ["event_key", "entities"]},
            ) for index, url in enumerate(urls, start=1)])
        return FakeResult(rows=[unsafe_story, story_row(6, last_seen=NOW - timedelta(hours=1))])


class PaginatedArticleSession(FakeStorySession):
    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "WITH ranked_articles" in sql:
            self.calls.append((sql, params))
            return FakeResult(rows=[SimpleNamespace(
                article_id=index, title=f"Article {index}",
                url=f"https://example.test/{index}",
                published_at=NOW - timedelta(minutes=index), source="Source",
                country_code="AZ" if index == 1 else "KZ",
                membership_confidence=0.9 - index / 10,
                relevance_score=0.95 - index / 10,
                evidence={"matched_features": ["event_key", "entities"]},
                is_primary=True,
            ) for index in (1, 2)])
        return super().execute(statement, params)


class BoundArticleSession(PaginatedArticleSession):
    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "WHERE st.id = :story_id" in sql and params.get("story_id") in {6, 7}:
            self.calls.append((sql, params))
            return FakeResult(row=story_row(params["story_id"]))
        return super().execute(statement, params)


class SupersededStorySession(FakeStorySession):
    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        self.calls.append((sql, params))
        if "WHERE st.slug = :story_slug" in sql:
            return FakeResult(row=SimpleNamespace(id=11))
        if "WHERE st.id = :story_id" in sql:
            if params["story_id"] == 11:
                old = story_row(11)
                old.meta = {"merged_into_story_id": 10, "canonical_slug": "story-primary"}
                return FakeResult(row=old)
            if params["story_id"] == 10:
                canonical = story_row(10)
                canonical.slug = "story-primary"
                return FakeResult(row=canonical)
            return FakeResult(row=None)
        return super().execute(statement, params)


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc, traceback):
        return False


def story_client(monkeypatch, session=None):
    session = session or FakeStorySession()
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
    client, session = story_client(monkeypatch)

    response = client.get("/api/v2/stories/7")

    assert response.status_code == 200
    payload = response.json()
    assert payload["countries"][0]["primary_url"] == "https://example.test/az"
    assert payload["entities"][0]["evidence"] == {"article_ids": [1, 2]}
    assert payload["events"][0]["evidence"] == {"articles": [1, 2]}
    assert payload["articles"][0]["is_primary"] is True
    detail_sql, detail_params = next(
        call for call in session.calls if "WHERE st.id = :story_id" in call[0]
    )
    assert ":ranking_at" in detail_sql
    assert detail_params["ranking_at"].tzinfo is not None


def test_story_cards_and_detail_expose_bounded_proven_signal_context(monkeypatch):
    client, session = story_client(monkeypatch, LinkedStoryContextSession())

    listing = client.get("/api/v2/stories?limit=1")
    detail = client.get("/api/v2/stories/7")

    assert listing.status_code == 200
    card = listing.json()["stories"][0]
    assert card["linked_signal_count"] == 2
    assert len(card["linked_signals"]) == 1
    assert card["linked_signals"][0]["relation"] == "explicit_story_evidence"
    assert card["linked_signals"][0]["evidence"] == {
        "source": "signal_evidence.story_ids",
        "story_id": 7,
    }
    assert card["latest_rri_shift"] == {
        "country_code": "AZ",
        "country_name": "Азербайджан",
        "at": NOW.isoformat(),
        "score": -12.0,
        "delta_24h": -8.0,
        "version": "v1",
        "relation": "temporal_context",
        "why_included": "rri_point_within_story_window",
        "limitation": "Временное совпадение с сюжетом не доказывает причинность.",
    }
    assert detail.status_code == 200
    assert detail.json()["id"] == card["id"] == 7
    assert detail.json()["linked_signal_count"] == 2
    assert detail.json()["latest_rri_shift"]["relation"] == "temporal_context"
    assert [shift["country_code"] for shift in detail.json()["rri_shifts"]] == [
        "AZ", "KZ",
    ]

    signal_sql, signal_params = next(
        call for call in session.calls if "candidate_links AS" in call[0]
    )
    assert "signal_evidence" in signal_sql
    assert "story_ids" in signal_sql
    assert "story_articles" in signal_sql
    assert "linked_signal_count" in signal_sql
    assert "WITH RECURSIVE story_aliases AS" in signal_sql
    assert "merged_into_story_id' = aliases.alias_story_id::text" in signal_sql
    assert "NOT candidate.id = ANY(aliases.path)" in signal_sql
    assert "se.story_ids &&" in signal_sql
    assert "se.article_ids &&" in signal_sql
    assert signal_params["linked_signal_limit"] == 5
    assert signal_params["max_alias_depth"] == 32
    rri_sql, rri_params = next(
        call for call in session.calls if "WITH story_windows AS" in call[0]
    )
    assert "JOIN ru_index" in rri_sql
    assert "ABS(ri.delta_24h) >= :min_meaningful_delta" in rri_sql
    assert "point_rank <= :rri_shift_limit" in rri_sql
    assert rri_params["min_meaningful_delta"] == 3.0
    assert rri_params["rri_shift_limit"] == 8
    assert "relation" not in rri_sql.lower()


def test_signal_on_superseded_story_id_reverse_resolves_to_canonical_story(monkeypatch):
    client, session = story_client(monkeypatch, SupersededSignalStoryContextSession())

    response = client.get("/api/v2/stories?limit=1")

    assert response.status_code == 200
    story = response.json()["stories"][0]
    assert story["id"] == 7
    assert story["linked_signal_count"] == 1
    assert story["linked_signals"][0]["evidence"] == {
        "source": "signal_evidence.story_ids",
        "story_id": 7,
        "matched_story_id": 11,
    }
    signal_sql, _ = next(
        call for call in session.calls if "WITH RECURSIVE story_aliases AS" in call[0]
    )
    assert "candidate.meta->>'merged_into_story_id'" in signal_sql
    assert "candidate.meta->>'merged_into_story_id')::bigint" not in signal_sql


def test_story_context_is_empty_without_persisted_evidence(monkeypatch):
    client, _ = story_client(monkeypatch)

    payload = client.get("/api/v2/stories?limit=1").json()["stories"][0]

    assert payload["linked_signal_count"] == 0
    assert payload["linked_signals"] == []
    assert payload["latest_rri_shift"] is None


def test_country_story_card_includes_its_country_specific_slice(monkeypatch):
    client, session = story_client(monkeypatch, CountryStoryContextSession())

    response = client.get("/api/v2/countries/AZ/stories?limit=1")

    assert response.status_code == 200
    context = response.json()["stories"][0]["country_context"]
    assert context == {
        "country_code": "AZ",
        "country_name": "Азербайджан",
        "article_count": 3,
        "source_count": 2,
        "media_tone": -1.25,
    }
    sql, params = next(call for call in session.calls if "SELECT sc.story_id," in call[0])
    assert "story_countries" in sql
    assert params == {"story_ids": [7], "country_code": "AZ"}


def test_story_list_reports_bounded_actual_indexed_coverage(monkeypatch):
    client, session = story_client(monkeypatch, StoryCoverageSession())

    response = client.get(
        "/api/v2/stories",
        params={
            "country": "AZ",
            "date_from": "2026-07-01T00:00:00+00:00",
            "date_to": "2026-07-15T23:59:59+00:00",
        },
    )

    assert response.status_code == 200
    assert response.json()["coverage"] == {
        "selected_from": "2026-07-01T00:00:00+00:00",
        "selected_to": "2026-07-15T23:59:59+00:00",
        "available_from": (NOW - timedelta(days=120)).isoformat(),
        "available_to": (NOW - timedelta(minutes=5)).isoformat(),
    }
    sql, params = next(call for call in session.calls if "AS available_from" in call[0])
    assert sql.count("LIMIT 1") == 2
    assert "ORDER BY ar.published_at ASC" in sql
    assert "ORDER BY ar.published_at DESC" in sql
    assert params == {"coverage_country": "AZ"}


def test_story_list_supports_topic_entity_date_filters_and_active_ranking(monkeypatch):
    client, session = story_client(monkeypatch)

    response = client.get(
        "/api/v2/stories",
        params={
            "topic": "energy",
            "entity_id": "entity-route",
            "date_from": "2026-07-01T00:00:00+00:00",
            "date_to": "2026-07-20T00:00:00+00:00",
        },
    )
    invalid_range = client.get(
        "/api/v2/stories",
        params={
            "date_from": "2026-07-20T00:00:00+00:00",
            "date_to": "2026-07-01T00:00:00+00:00",
        },
    )
    mixed_timezone_invalid_range = client.get(
        "/api/v2/stories",
        params={
            "date_from": "2026-07-20T00:00:00",
            "date_to": "2026-07-01T00:00:00+00:00",
        },
    )

    assert response.status_code == 200
    sql, params = session.calls[0]
    assert params["topic"] == "energy"
    assert params["entity_id"] == "entity-route"
    assert params["date_from"].isoformat().startswith("2026-07-01")
    assert params["date_to"].isoformat().startswith("2026-07-20")
    assert "story_entities" in sql
    assert "meta->'topics'" in sql
    assert "sa.added_at <= :ranking_at" in sql
    assert "action_level_snapshot" in sql
    assert "JOIN analysis" not in sql
    assert "EXTRACT(EPOCH FROM (:ranking_at - rf.last_seen))" in sql
    assert "rf.article_count::numeric" in sql
    assert "rf.source_count::numeric" in sql
    assert "rf.country_count" in sql
    assert "rf.highest_action_level" in sql
    assert params["ranking_at"].tzinfo is not None
    assert invalid_range.status_code == 422
    assert mixed_timezone_invalid_range.status_code == 422


def test_story_activity_ranking_favors_recent_broad_active_story_over_stale_confidence():
    active = stories_routes._story_activity_score(
        action_level=4,
        article_count=12,
        source_count=7,
        country_count=4,
        first_seen=NOW - timedelta(days=2),
        last_seen=NOW - timedelta(hours=2),
        ranking_at=NOW,
    )
    stale = stories_routes._story_activity_score(
        action_level=2,
        article_count=30,
        source_count=2,
        country_count=2,
        first_seen=NOW - timedelta(days=90),
        last_seen=NOW - timedelta(days=60),
        ranking_at=NOW,
    )

    assert active < stale  # lifecycle rank is the primary ascending key
    assert active[1] > stale[1]


def test_story_cursor_pins_ranking_clock_for_stable_followup_page(monkeypatch):
    client, session = story_client(monkeypatch)

    first = client.get("/api/v2/stories?limit=1")
    cursor = first.json()["next_cursor"]
    second = client.get("/api/v2/stories", params={"limit": 1, "cursor": cursor})

    assert first.status_code == second.status_code == 200
    list_calls = [
        call for call in session.calls
        if "ORDER BY lifecycle_rank ASC" in call[0]
    ]
    assert len(list_calls) >= 2
    first_ranking_at = list_calls[0][1]["ranking_at"]
    second_ranking_at = list_calls[1][1]["ranking_at"]
    assert second_ranking_at == first_ranking_at
    assert "cursor_lifecycle_rank" in list_calls[1][1]


def test_story_cursor_ignores_mutated_current_aggregates_after_first_page(monkeypatch):
    session = MutableStoryAggregateSession()
    client, _ = story_client(monkeypatch, session)

    first = client.get("/api/v2/stories?limit=1")
    cursor = first.json()["next_cursor"]
    session.second.article_count = 10000
    session.second.source_count = 9000
    session.second.country_count = 99
    session.second.highest_action_level = 5
    second = client.get("/api/v2/stories", params={"limit": 1, "cursor": cursor})

    assert [item["id"] for item in first.json()["stories"]] == [7]
    assert [item["id"] for item in second.json()["stories"]] == [6]
    list_sql = next(
        sql for sql, params in session.calls
        if "cursor_lifecycle_rank" in params and "ORDER BY lifecycle_rank ASC" in sql
    )
    assert "sa.added_at <= :ranking_at" in list_sql
    assert "action_level_snapshot" in list_sql
    assert "JOIN analysis" not in list_sql
    assert "COALESCE(st.article_count" not in list_sql


def test_story_detail_articles_use_bounded_cursor_pagination(monkeypatch):
    client, session = story_client(monkeypatch, PaginatedArticleSession())

    first_page = client.get("/api/v2/stories/7?article_limit=1")
    too_large = client.get("/api/v2/stories/7?article_limit=101")

    assert first_page.status_code == 200
    payload = first_page.json()
    assert len(payload["articles"]) == 1
    assert payload["articles_next_cursor"]
    assert too_large.status_code == 422
    article_call = next(
        call for call in session.calls if "WITH ranked_articles" in call[0]
    )
    assert article_call[1]["article_limit"] == 2


@pytest.mark.parametrize("changed_params", [
    {"country": "AZ"},
    {"lifecycle": "resolved"},
    {"min_confidence": 0.1},
    {"min_action_level": 2},
    {"since": "2026-07-01T00:00:00+00:00"},
    {"topic": "different"},
    {"entity_id": "entity-other"},
    {"date_from": "2026-07-01T00:00:00+00:00"},
    {"date_to": "2026-07-20T00:00:00+00:00"},
])
def test_story_cursor_is_bound_to_all_filters(monkeypatch, changed_params):
    client, _ = story_client(monkeypatch)

    first_page = client.get("/api/v2/stories?limit=1")
    cursor = first_page.json()["next_cursor"]
    mismatched = client.get(
        "/api/v2/stories",
        params={"limit": 1, "cursor": cursor, **changed_params},
    )

    assert first_page.status_code == 200
    assert mismatched.status_code == 400
    assert mismatched.json()["detail"] == "Story cursor does not match query"


def test_story_cursor_is_bound_to_scope_and_sort_version(monkeypatch):
    client, _ = story_client(monkeypatch)

    first_page = client.get("/api/v2/stories?country=AZ&limit=1")
    cursor = first_page.json()["next_cursor"]
    wrong_scope = client.get(
        "/api/v2/countries/AZ/stories",
        params={"limit": 1, "cursor": cursor},
    )
    cursor_payload = json.loads(base64.urlsafe_b64decode(
        cursor + "=" * (-len(cursor) % 4)
    ).decode("utf-8"))
    cursor_payload[0] = "stories-v1-obsolete-sort"
    wrong_version_cursor = base64.urlsafe_b64encode(
        json.dumps(cursor_payload).encode("utf-8")
    ).decode("ascii")
    wrong_version = client.get(
        "/api/v2/stories",
        params={"country": "AZ", "limit": 1, "cursor": wrong_version_cursor},
    )

    assert wrong_scope.status_code == 400
    assert wrong_version.status_code == 400


def test_article_cursor_is_bound_to_canonical_story(monkeypatch):
    client, _ = story_client(monkeypatch, BoundArticleSession())

    first_page = client.get("/api/v2/stories/7?article_limit=1")
    cursor = first_page.json()["articles_next_cursor"]
    mismatched = client.get(
        "/api/v2/stories/6",
        params={"article_limit": 1, "article_cursor": cursor},
    )

    assert first_page.status_code == 200
    assert mismatched.status_code == 400
    assert mismatched.json()["detail"] == "Article cursor does not match story"


def test_ranked_story_and_article_responses_explain_inclusion(monkeypatch):
    client, _ = story_client(monkeypatch)

    listing = client.get("/api/v2/stories?limit=1").json()["stories"][0]
    detail = client.get("/api/v2/stories/7").json()

    assert listing["why_included"]
    assert listing["relevance_score"] == 0.88
    assert listing["confidence"] == 0.81
    assert listing["evidence"]["topics"] == ["energy"]
    assert detail["entities"][0]["canonical_name"] == "Route"
    assert detail["entities"][0]["kind"] == "infrastructure"
    assert detail["events"][0]["confidence"] == 0.9
    article = detail["articles"][0]
    assert article["why_included"]
    assert article["relevance_score"] == 0.79
    assert article["confidence"] == 0.81


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


def test_typed_invalid_story_and_article_cursors_return_400(monkeypatch):
    client, _ = story_client(monkeypatch)
    invalid_story_cursor = base64.urlsafe_b64encode(
        json.dumps([123, 7]).encode("utf-8")
    ).decode("ascii")
    invalid_article_cursor = base64.urlsafe_b64encode(
        json.dumps([0.8, 123, 7]).encode("utf-8")
    ).decode("ascii")

    story_response = client.get(
        "/api/v2/stories", params={"cursor": invalid_story_cursor}
    )
    article_response = client.get(
        "/api/v2/stories/7", params={"article_cursor": invalid_article_cursor}
    )

    assert story_response.status_code == 400
    assert article_response.status_code == 400


def test_story_api_serializes_only_http_urls_with_hostnames(monkeypatch):
    client, _ = story_client(monkeypatch, UnsafeUrlStorySession())

    listing = client.get("/api/v2/stories?limit=1")
    detail = client.get("/api/v2/stories/7")

    assert listing.status_code == 200
    assert listing.json()["stories"][0]["primary_url"] is None
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["primary_url"] is None
    assert payload["countries"][0]["primary_url"] is None
    assert [article["url"] for article in payload["articles"]] == [
        None,
        None,
        None,
        "https://safe.example/story",
    ]


@pytest.mark.parametrize("url", [
    "https://exa mple.com/story",
    "https://-bad.example/story",
    "https://example.com:not-a-port/story",
    "https://user:secret@example.com/story",
    "https://user@example.com/story",
    f"https://{chr(0xD800)}.example/story",
])
def test_malformed_http_hostnames_are_rejected(url):
    assert stories_routes.safe_public_url(url) is None


def test_independent_clusters_cannot_share_or_overwrite_story_identity():
    first_session = PersistenceSession()
    second_session = PersistenceSession()
    first_cluster = [candidate("AZ"), candidate("KZ")]
    second_cluster = [
        replace(candidate("AZ"), thread_id=11, article_ids=(11,)),
        replace(candidate("KZ"), thread_id=12, article_ids=(12,)),
    ]

    persist_story_cluster(first_session, first_cluster, now=NOW)
    persist_story_cluster(second_session, second_cluster, now=NOW)

    first_insert = next(call for call in first_session.calls if "INSERT INTO stories" in call[0])
    second_insert = next(call for call in second_session.calls if "INSERT INTO stories" in call[0])
    lookup_sql = next(sql for sql in first_session.statements if "SELECT st.id, st.slug" in sql)
    assert first_insert[1]["slug"] != second_insert[1]["slug"]
    assert "st.slug = :slug" not in lookup_sql
    assert "thread_ids" in lookup_sql
    assert "ON CONFLICT (slug) DO NOTHING" in first_insert[0]


def test_genuinely_independent_slug_collision_is_not_silently_reused():
    session = IndependentSlugCollisionSession()

    with pytest.raises(
        RuntimeError,
        match="Story slug collision without article or thread identity overlap",
    ):
        persist_story_cluster(session, [candidate("AZ"), candidate("KZ")], now=NOW)


def test_persistence_recomputes_header_counts_from_saved_memberships():
    session = PersistenceSession()

    persist_story_cluster(session, [candidate("AZ"), candidate("KZ")], now=NOW)

    aggregate_sql = "\n".join(session.statements)
    assert "UPDATE stories st SET" in aggregate_sql
    assert "COUNT(DISTINCT ar.source_id)" in aggregate_sql
    assert "COUNT(DISTINCT s.country_code)" in aggregate_sql
    assert "MAX(COALESCE(an.action_level, 1))" in aggregate_sql


def test_persistence_uses_real_entity_confidence_and_one_representative_event_row():
    session = PersistenceSession()

    persist_story_cluster(session, [candidate("AZ"), candidate("KZ")], now=NOW)

    aggregate_sql = "\n".join(session.statements)
    assert "AVG(aem.confidence)" in aggregate_sql
    assert "DISTINCT ON (aem.entity_id)" in aggregate_sql
    assert "representative_article_id" in aggregate_sql


def test_membership_evidence_reproduces_score_and_names_peer():
    session = PersistenceSession()

    persist_story_cluster(session, [candidate("AZ"), candidate("KZ")], now=NOW)

    evidence_rows = [
        json.loads(params["evidence"])
        for sql, params in session.calls
        if "INSERT INTO story_articles" in sql and "VALUES" in sql
    ]
    assert evidence_rows
    for evidence in evidence_rows:
        assert evidence["peer_thread_id"] in {1, 2}
        assert evidence["peer_thread_id"] != evidence["thread_id"]
        assert evidence["effective_components"]
        assert evidence["weights"]
        assert evidence["action_level_snapshot"] == 3
        reproduced = sum(
            evidence["effective_components"][name] * weight
            for name, weight in evidence["weights"].items()
        )
        assert round(reproduced, 6) == evidence["score"]
    membership_sql = next(
        sql for sql, _ in session.calls
        if "INSERT INTO story_articles" in sql and "VALUES" in sql
    )
    assert "story_articles.evidence->'action_level_snapshot'" in membership_sql


def test_persistence_checks_stable_thread_identity_before_summary_generation():
    session = PersistenceSession()

    persist_story_cluster(session, [candidate("AZ"), candidate("KZ")], now=NOW)

    lookup_sql = next(sql for sql in session.statements if "SELECT st.id, st.slug" in sql)
    assert "thread_ids" in lookup_sql


def test_overlapping_story_ids_are_reconciled_into_one_primary():
    session = DuplicatePersistenceSession()

    persist_story_cluster(session, [candidate("AZ"), candidate("KZ")], now=NOW)

    reconcile_call = next(
        call for call in session.calls
        if "SELECT :primary_story_id" in call[0] and "FROM story_articles" in call[0]
    )
    supersede_call = next(
        call for call in session.calls
        if "merged_into_story_id" in call[0] and "UPDATE stories" in call[0]
    )
    assert reconcile_call[1] == {"primary_story_id": 10, "duplicate_story_ids": [11]}
    assert supersede_call[1]["primary_story_id"] == 10
    assert supersede_call[1]["duplicate_story_ids"] == [11]
    assert not any("DELETE FROM stories" in sql for sql in session.statements)
    assert not any("NOT (article_id = ANY" in sql for sql in session.statements)
    lookup_sql = next(sql for sql in session.statements if "SELECT st.id, st.slug" in sql)
    assert "FOR UPDATE OF st" in lookup_sql
    update_call = next(
        call for call in session.calls
        if "UPDATE stories SET" in call[0] and "RETURNING id" in call[0]
    )
    assert update_call[1]["article_count"] == 4
    assert "4 публикаций" in update_call[1]["summary"]
    merged_meta = json.loads(update_call[1]["meta"])
    assert {item["decision_id"] for item in merged_meta["merge_audit"]} >= {
        "primary-audit", "duplicate-audit",
    }
    assert {item["decision_id"] for item in merged_meta["reactivations"]} >= {
        "reactivation-audit",
    }
    assert merged_meta["merged_story_ids"] == [11]


def test_old_story_slug_resolves_to_canonical_story(monkeypatch):
    client, _ = story_client(monkeypatch, SupersededStorySession())

    response = client.get("/api/v2/stories/by-slug/story-duplicate")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == 10
    assert payload["slug"] == "story-primary"
    assert payload["redirected_from_story_id"] == 11


def test_explicit_reactivation_is_persisted_in_audit_history():
    session = PersistenceSession()
    old = candidate(
        "AZ",
        first_seen=NOW - timedelta(days=20),
        last_seen=NOW - timedelta(days=15),
    )
    new = candidate("KZ", first_seen=NOW, last_seen=NOW)

    persist_story_cluster(
        session,
        [old, new],
        now=NOW,
        reactivation_pairs=frozenset({(1, 2)}),
    )

    story_insert = next(call for call in session.calls if "INSERT INTO stories" in call[0])
    meta = json.loads(story_insert[1]["meta"])
    assert meta["reactivations"][0]["thread_ids"] == [1, 2]
    assert meta["reactivations"][0]["gap_days"] == 15
    membership_evidence = [
        json.loads(params["evidence"])
        for sql, params in session.calls
        if "INSERT INTO story_articles" in sql and "VALUES" in sql
    ]
    assert membership_evidence
    assert all(item["explicit_reactivation"] is True for item in membership_evidence)
    assert all(
        "time_window_exceeded" not in item["non_merge_reasons"]
        for item in membership_evidence
    )


def test_resolved_story_reopens_only_with_event_and_entity_gate_and_is_audited():
    session = ResolvedPersistenceSession()

    persist_story_cluster(session, [candidate("AZ"), candidate("KZ")], now=NOW)

    update_call = next(
        call for call in session.calls
        if "UPDATE stories SET" in call[0] and "RETURNING id" in call[0]
    )
    meta = json.loads(update_call[1]["meta"])
    assert meta["reactivations"]
    assert meta["reactivations"][-1]["decision"] == "reactivation"
    assert meta["reactivations"][-1]["story_id"] == 10
    assert "4 публикаций" in update_call[1]["summary"]


def test_resolved_story_without_entity_gate_stays_closed_and_new_story_is_created():
    session = ResolvedPersistenceSession()
    cluster = [
        candidate("AZ", entities=frozenset()),
        candidate("KZ", entities=frozenset()),
    ]

    story_id, _ = persist_story_cluster(session, cluster, now=NOW)

    assert story_id == 42
    assert any("INSERT INTO stories" in sql for sql in session.statements)
    assert not any(
        "UPDATE stories SET" in sql and "RETURNING id" in sql
        for sql in session.statements
    )


def test_denied_reactivation_uses_new_activity_epoch_and_is_idempotent():
    cluster = [
        replace(
            candidate(
                "AZ",
                entities=frozenset(),
                first_seen=NOW - timedelta(days=30),
                last_seen=NOW,
            ),
            article_ids=(1, 90),
            articles=(
                StoryArticle(
                    90, "AZ", "Old AZ", None,
                    NOW - timedelta(days=30), "source-az-old",
                ),
                StoryArticle(1, "AZ", "New AZ", None, NOW, "source-az-new"),
            ),
        ),
        replace(
            candidate(
                "KZ",
                entities=frozenset(),
                first_seen=NOW - timedelta(days=15),
                last_seen=NOW,
            ),
            article_ids=(2, 91),
            articles=(
                StoryArticle(
                    91, "KZ", "Old KZ", None,
                    NOW - timedelta(days=15), "source-kz-old",
                ),
                StoryArticle(2, "KZ", "New KZ", None, NOW, "source-kz-new"),
            ),
        ),
    ]
    session = DeniedReactivationPersistenceSession(cluster)

    first_story_id, _ = persist_story_cluster(session, cluster, now=NOW)
    first_slug = session.active_story.slug
    second_story_id, _ = persist_story_cluster(session, cluster, now=NOW)

    story_inserts = [
        params for sql, params in session.calls if "INSERT INTO stories" in sql
    ]
    updated_story_ids = {
        params.get("story_id")
        for sql, params in session.calls
        if "UPDATE stories SET" in sql and "RETURNING id" in sql
    }
    assert first_story_id == second_story_id == 42
    assert first_slug != session.old_story.slug
    assert first_slug.startswith(f"story-{NOW.date().isoformat()}-")
    assert len(story_inserts) == 1
    assert session.active_story.slug == first_slug
    assert session.old_story.lifecycle == "resolved"
    assert 10 not in updated_story_ids


def test_background_builder_derives_reactivation_pairs_from_resolved_story(monkeypatch):
    import src.stories as stories_module

    old = candidate(
        "AZ",
        first_seen=NOW - timedelta(days=20),
        last_seen=NOW - timedelta(days=15),
    )
    new = candidate("KZ", first_seen=NOW, last_seen=NOW)
    observed = {}

    class ResolvedPairSession:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "lifecycle = 'resolved'" in sql:
                return FakeResult(rows=[SimpleNamespace(
                    id=10,
                    lifecycle="resolved",
                    last_seen=NOW - timedelta(days=15),
                    meta={"thread_ids": [old.thread_id]},
                )])
            return FakeResult()

    monkeypatch.setattr(stories_module, "fetch_story_candidates", lambda session: [old, new])

    def fake_persist(session, items, *, summarizer, now, reactivation_pairs):
        observed["pairs"] = reactivation_pairs
        return 10, 2

    monkeypatch.setattr(stories_module, "persist_story_cluster", fake_persist)
    monkeypatch.setattr(stories_module, "refresh_story_lifecycles", lambda session, now: None)

    result = build_stories(ResolvedPairSession(), now=NOW)

    assert result.stories_upserted == 1
    assert observed["pairs"] == frozenset({(1, 2)})


def test_background_builder_wires_explicit_reactivation_pairs(monkeypatch):
    import src.stories as stories_module

    old = candidate(
        "AZ",
        first_seen=NOW - timedelta(days=20),
        last_seen=NOW - timedelta(days=15),
    )
    new = candidate("KZ", first_seen=NOW, last_seen=NOW)
    explicit_pairs = frozenset({(1, 2)})
    observed = {}

    monkeypatch.setattr(stories_module, "fetch_story_candidates", lambda session: [old, new])

    def fake_cluster(items, *, reactivation_pairs):
        observed["cluster_pairs"] = reactivation_pairs
        return [tuple(items)]

    def fake_persist(session, items, *, summarizer, now, reactivation_pairs):
        observed["persist_pairs"] = reactivation_pairs
        return 7, 2

    monkeypatch.setattr(stories_module, "cluster_story_candidates", fake_cluster)
    monkeypatch.setattr(stories_module, "persist_story_cluster", fake_persist)
    monkeypatch.setattr(stories_module, "refresh_story_lifecycles", lambda session, now: None)

    result = build_stories(object(), now=NOW, reactivation_pairs=explicit_pairs)

    assert result.stories_upserted == 1
    assert observed == {
        "cluster_pairs": explicit_pairs,
        "persist_pairs": explicit_pairs,
    }


def test_unchanged_copy_preserves_generated_at():
    cluster = [candidate("AZ"), candidate("KZ")]
    session = UnchangedPersistenceSession(cluster)

    persist_story_cluster(session, cluster, now=NOW)

    update_call = next(
        call for call in session.calls
        if "UPDATE stories SET" in call[0] and "RETURNING id" in call[0]
    )
    assert "generated_at = :generated_at" in update_call[0]
    assert update_call[1]["generated_at"] == session.generated_at
