import json
from pathlib import Path
import base64
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import stories as stories_routes
from src.pipeline.briefs import gather_country_inputs
from src.stories import (
    StoryCandidate,
    StoryArticle,
    build_stories,
    cluster_story_candidates,
    compute_source_hash,
    deterministic_story_copy,
    derive_reactivation_pairs,
    fetch_story_candidates,
    merge_rejection_reasons,
    persist_story_cluster,
    resolve_story_copy,
    score_story_match,
    should_merge,
    transition_lifecycle,
    _filter_story_cluster_articles,
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


def test_strong_cross_language_semantics_plus_entity_evidence_does_not_admit_story():
    left = replace(
        candidate(
            "AZ",
            event_key="nazirlik enerji danisiqlarini davam etdirir",
            title="Nazirlik yeni danisiqlar barədə məlumat verdi",
            entities=frozenset({"entity-minister"}),
            topics=frozenset(),
        ),
        semantic_matches=((2, 0.82),),
    )
    right = candidate(
        "KZ",
        event_key="ведомство продолжило консультации по энергетике",
        title="Министерство сообщило о новом раунде консультаций",
        entities=frozenset({"entity-minister"}),
        topics=frozenset(),
    )

    similarity = score_story_match(left, right)

    assert similarity.components["semantic"] == pytest.approx(0.82)
    assert {"semantic", "entities"}.issubset(similarity.matched_features)
    assert similarity.total >= 0.65
    assert not should_merge(similarity)
    assert "missing_concrete_event_anchor" in merge_rejection_reasons(similarity)


def test_embedding_similarity_alone_cannot_merge():
    left = replace(
        candidate(
            "AZ",
            event_key="tamamilə fərqli xəbər",
            title="Birinci yerli xəbər",
            entities=frozenset(),
            topics=frozenset(),
        ),
        semantic_matches=((2, 1.0),),
    )
    right = candidate(
        "KZ",
        event_key="совершенно другая новость",
        title="Вторая местная новость",
        entities=frozenset(),
        topics=frozenset(),
    )

    similarity = score_story_match(left, right)

    assert similarity.components["semantic"] == 1.0
    assert similarity.matched_features == frozenset({"semantic"})
    assert not should_merge(similarity)


def test_concrete_event_threshold_is_inclusive_even_below_legacy_weighted_total():
    baseline = score_story_match(candidate("AZ"), candidate("KZ"))
    similarity = replace(
        baseline,
        total=0.20,
        components={**baseline.components, "event_key": 0.65},
        matched_features=frozenset({"event_key"}),
    )

    assert should_merge(similarity)
    assert "score_below_threshold" not in merge_rejection_reasons(similarity)


def test_concrete_event_threshold_rejects_point_649_even_with_high_legacy_score():
    baseline = score_story_match(candidate("AZ"), candidate("KZ"))
    similarity = replace(
        baseline,
        total=1.0,
        components={**baseline.components, "event_key": 0.649},
        matched_features=frozenset({"semantic", "entities", "topics", "title"}),
    )

    assert not should_merge(similarity)
    assert merge_rejection_reasons(similarity) == ("missing_concrete_event_anchor",)


def test_semantic_entities_and_topics_cannot_admit_without_concrete_event():
    left = replace(
        candidate(
            "KZ",
            event_key="сравнение цен на продукты в еаэс",
            title="Суд разрешил взыскать средства Газпрома в пользу Нафтогаза",
            entities=frozenset({f"entity-{index}" for index in range(9)}),
            topics=frozenset({f"topic-{index}" for index in range(13)}),
        ),
        semantic_matches=((2, 0.99),),
    )
    right = candidate(
        "KG",
        event_key="запрет на ввоз продукции из армении",
        title="В Кыргызстан не пропустили пиломатериалы из России",
        entities=left.entities,
        topics=left.topics,
    )

    similarity = score_story_match(left, right)

    assert similarity.total >= 0.65
    assert {"semantic", "entities", "topics"}.issubset(similarity.matched_features)
    assert not should_merge(similarity)
    assert "missing_concrete_event_anchor" in merge_rejection_reasons(similarity)


def test_generic_event_key_cannot_admit_even_with_identical_keys():
    similarity = score_story_match(
        candidate("AZ", event_key="главные новости дня"),
        candidate("KZ", event_key="главные новости дня"),
    )

    assert similarity.components["event_key"] == 1.0
    assert not should_merge(similarity)
    assert "generic_event_key" in merge_rejection_reasons(similarity)


def test_concrete_event_gate_keeps_existing_fourteen_day_time_window():
    old = candidate(
        "AZ",
        event_key="заседание совета консульских служб снг",
        first_seen=NOW - timedelta(days=20),
        last_seen=NOW - timedelta(days=15, seconds=1),
    )
    new = candidate(
        "KZ",
        event_key="заседание совета консульских служб снг",
        first_seen=NOW,
        last_seen=NOW,
    )

    similarity = score_story_match(old, new)

    assert similarity.components["event_key"] == 1.0
    assert similarity.gap_days > 14
    assert not should_merge(similarity)
    assert "time_window_exceeded" in merge_rejection_reasons(similarity)


def test_dirty_cluster_keeps_only_cross_country_supported_articles():
    thread_event = "заседание консульского совета стран снг"
    supported_event = "заседание совета консульских служб снг"

    def article(article_id, country, event_key, source_id, *, hours=0):
        return StoryArticle(
            article_id,
            country,
            f"Article {article_id}",
            None,
            NOW - timedelta(hours=hours),
            f"Source {source_id}",
            action_level=article_id % 6 + 1,
            event_key=event_key,
            entity_ids=frozenset({f"entity-{article_id}"}),
            topics=frozenset({f"topic-{article_id}"}),
            source_id=source_id,
        )

    left_supported = article(201, "TJ", supported_event, 21)
    left_dirty = article(
        202,
        "TJ",
        "форум креативной молодежи центральной азии",
        22,
    )
    right_supported = article(301, "KG", supported_event, 31, hours=2)
    right_supported_2 = article(302, "KG", supported_event, 32, hours=3)
    right_dirty = article(
        303,
        "KG",
        "заседание совета глав правительств шос",
        33,
    )
    left = replace(
        candidate("TJ", event_key=thread_event),
        thread_id=9201,
        article_ids=(201, 202),
        articles=(left_supported, left_dirty),
    )
    right = replace(
        candidate("KG", event_key=thread_event),
        thread_id=9202,
        article_ids=(301, 302, 303),
        articles=(right_supported, right_supported_2, right_dirty),
    )

    filtered = _filter_story_cluster_articles((left, right))

    assert filtered is not None
    assert [item.article_ids for item in filtered] == [(201,), (301, 302)]
    assert [item.source_ids for item in filtered] == [
        frozenset({21}),
        frozenset({31, 32}),
    ]
    assert all(
        article.event_key == supported_event
        for item in filtered
        for article in item.articles
    )


def test_thread_pair_drops_when_own_article_events_are_different():
    thread_event = "заседание консульского совета стран снг"
    left = replace(
        candidate("TJ", event_key=thread_event),
        thread_id=9301,
        articles=(StoryArticle(
            401,
            "TJ",
            "Молодёжный форум",
            None,
            NOW,
            "TJ source",
            event_key="форум креативной молодежи центральной азии",
            source_id=41,
        ),),
    )
    right = replace(
        candidate("KG", event_key=thread_event),
        thread_id=9302,
        articles=(StoryArticle(
            501,
            "KG",
            "Совет ШОС",
            None,
            NOW,
            "KG source",
            event_key="заседание совета глав правительств шос",
            source_id=51,
        ),),
    )

    assert should_merge(score_story_match(left, right))
    assert _filter_story_cluster_articles((left, right)) is None


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


def test_same_thread_candidates_are_rejected_before_scoring(monkeypatch):
    import src.stories as stories_module

    left = candidate("AZ")
    duplicate_partition = replace(
        candidate("KZ"),
        thread_id=left.thread_id,
    )

    def fail_if_scored(left_item, right_item):
        raise AssertionError(
            f"same thread was scored: {left_item.thread_id}/{right_item.thread_id}"
        )

    monkeypatch.setattr(stories_module, "score_story_match", fail_if_scored)

    assert cluster_story_candidates([left, duplicate_partition]) == []


def test_far_pairs_are_prefiltered_but_near_pairs_use_existing_scorer(monkeypatch):
    import src.stories as stories_module

    old = replace(
        candidate(
            "AZ",
            first_seen=NOW - timedelta(days=30),
            last_seen=NOW - timedelta(days=30),
        ),
        thread_id=1,
    )
    near = replace(
        candidate(
            "KZ",
            first_seen=NOW - timedelta(days=29),
            last_seen=NOW - timedelta(days=29),
        ),
        thread_id=2,
    )
    far = replace(candidate("UZ"), thread_id=3)
    real_scorer = score_story_match
    scored_pairs = []

    def recording_scorer(left_item, right_item):
        scored_pairs.append((left_item.thread_id, right_item.thread_id))
        return real_scorer(left_item, right_item)

    monkeypatch.setattr(stories_module, "score_story_match", recording_scorer)

    clusters = cluster_story_candidates([far, near, old])

    assert [[item.thread_id for item in cluster] for cluster in clusters] == [[1, 2]]
    assert scored_pairs == [(2, 1)]


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
        persist_story_cluster(
            session, [left, bridge, unrelated], now=NOW, membership_generation=1
        )

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


class PublisherAttributionFixtureSession:
    """Model the ES discovery triplet on either side of the canonical view."""

    articles = (
        {
            "article_id": 501,
            "publisher_source_id": 11,
            "publisher_name": "EL PAÍS",
            "publisher_country": "ES",
            "title": "El País informa sobre Rusia",
        },
        {
            "article_id": 502,
            "publisher_source_id": 12,
            "publisher_name": "Reuters",
            "publisher_country": "GB",
            "title": "Reuters informa sobre Rusia",
        },
        {
            "article_id": 503,
            "publisher_source_id": None,
            "publisher_name": None,
            "publisher_country": None,
            "title": "Unknown informa sobre Rusia",
        },
    )

    def __init__(self):
        self.statements = []

    def _attributed(self, sql, *, country=None):
        canonical = (
            "JOIN article_country_facts s" in sql
            and "s.article_id = ar.id" in sql
        )
        rows = []
        for item in self.articles:
            if canonical and item["publisher_source_id"] is None:
                continue
            item_country = item["publisher_country"] if canonical else "ES"
            if country and item_country != country:
                continue
            rows.append((
                item,
                item_country,
                item["publisher_name"] if canonical else "Google News (ES) — Россия",
                item["publisher_source_id"] if canonical else 900,
            ))
        return rows

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        self.statements.append(sql)
        if "FROM ru_index" in sql:
            return FakeResult(row=None)
        if "FROM signals" in sql or "FROM gdelt_daily" in sql:
            return FakeResult(rows=[])
        if "ar.published_at::date AS day" in sql:
            return FakeResult(rows=[
                SimpleNamespace(
                    title=item["title"],
                    url=f"https://example.test/{item['article_id']}",
                    source_name=source_name,
                    publisher_source_id=publisher_source_id,
                    sentiment=0.2,
                    action_level=3,
                    day=NOW.date(),
                )
                for item, _, source_name, publisher_source_id
                in self._attributed(sql, country=params["cc"])
            ])
        if "an.embedding IS NOT NULL AS has_embedding" in sql:
            return FakeResult(rows=[
                SimpleNamespace(
                    analysis_id=item["article_id"] + 1000,
                    article_id=item["article_id"],
                    event_key="отношения с россией",
                    sentiment=0.2,
                    action_level=3,
                    event_type="diplomatic",
                    has_embedding=True,
                    title=item["title"],
                    url=f"https://example.test/{item['article_id']}",
                    published_at=NOW,
                    country_code=country,
                    source_name=source_name,
                    publisher_source_id=publisher_source_id,
                    tier="mainstream",
                )
                for item, country, source_name, publisher_source_id
                in self._attributed(sql)
            ])
        if "FROM threads t" in sql:
            return FakeResult(rows=[
                SimpleNamespace(
                    thread_id=item["article_id"] + 2000,
                    country_code=country,
                    thread_key="отношения с россией",
                    thread_title=item["title"],
                    first_seen=NOW,
                    last_seen=NOW,
                    article_id=item["article_id"],
                    article_title=item["title"],
                    url=f"https://example.test/{item['article_id']}",
                    published_at=NOW,
                    source_name=source_name,
                    publisher_source_id=publisher_source_id,
                    sentiment=0.2,
                    action_level=3,
                    article_event_key="отношения с россией",
                    topics=["diplomacy"],
                )
                for item, country, source_name, publisher_source_id
                in self._attributed(sql)
            ])
        if "FROM article_entity_mentions" in sql:
            return FakeResult(rows=[])
        if "semantic_story_pairs" in sql:
            return FakeResult(rows=[])
        raise AssertionError(f"Unexpected fixture query: {sql}")


class MixedLegacyThreadFixtureSession(PublisherAttributionFixtureSession):
    def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM threads t" in sql:
            self.statements.append(sql)
            canonical_country_boundary = (
                "TRIM(s.country_code) = TRIM(t.country_code)" in sql
                and "TRIM(t.country_code) AS country_code" in sql
            )
            attributed = self._attributed(sql)
            if canonical_country_boundary:
                attributed = [row for row in attributed if row[1] == "ES"]
            return FakeResult(rows=[
                SimpleNamespace(
                    thread_id=2501,
                    country_code=country,
                    thread_key="отношения с россией",
                    thread_title="Legacy mixed-country thread",
                    first_seen=NOW,
                    last_seen=NOW,
                    article_id=item["article_id"],
                    article_title=item["title"],
                    url=f"https://example.test/{item['article_id']}",
                    published_at=NOW,
                    source_name=source_name,
                    publisher_source_id=publisher_source_id,
                    sentiment=0.2,
                    action_level=3,
                    article_event_key="отношения с россией",
                    topics=["diplomacy"],
                )
                for item, country, source_name, publisher_source_id
                in attributed
            ])
        if "FROM article_entity_mentions" in sql:
            self.statements.append(sql)
            return FakeResult(rows=[
                SimpleNamespace(article_id=501, entity_id="entity-russia"),
                SimpleNamespace(article_id=502, entity_id="entity-russia"),
            ])
        return super().execute(statement, params)


def test_verified_publishers_drive_briefs_threads_and_story_candidates():
    import scripts.build_threads as build_threads

    brief_session = PublisherAttributionFixtureSession()
    brief = gather_country_inputs(brief_session, "ES")

    assert [item["title"] for item in brief["own_media_headlines"]] == [
        "El País informa sobre Rusia",
    ]
    assert brief["own_media_headlines"][0]["publisher_source_id"] == 11
    assert [(item["source"], item["country"]) for item in brief["citations"]] == [
        ("EL PAÍS", "ES"),
    ]

    thread_articles = build_threads.fetch_articles(PublisherAttributionFixtureSession())
    assert [
        (item["publisher_source_id"], item["source_name"], item["country_code"])
        for item in thread_articles
    ] == [
        (11, "EL PAÍS", "ES"),
        (12, "Reuters", "GB"),
    ]

    candidates = fetch_story_candidates(PublisherAttributionFixtureSession())
    assert [
        (item.country_code, item.sources, item.source_ids)
        for item in candidates
    ] == [
        ("ES", frozenset({"EL PAÍS"}), frozenset({11})),
        ("GB", frozenset({"Reuters"}), frozenset({12})),
    ]
    assert all(
        "Google News (" not in article.source_name
        for candidate_item in candidates
        for article in candidate_item.articles
    )


def test_ready_active_semantic_pairs_are_attached_symmetrically():
    class SemanticPairSession(PublisherAttributionFixtureSession):
        def execute(self, statement, params=None):
            sql = str(statement)
            if "semantic_story_pairs" in sql:
                self.statements.append(sql)
                assert params == {
                    "semantic_thread_ids": [2501, 2502],
                    "semantic_article_ids": ["501", "502"],
                    "semantic_published_ats": [NOW, NOW],
                }
                return FakeResult(rows=[SimpleNamespace(
                    left_thread_id=2501,
                    right_thread_id=2502,
                    semantic_score=0.91,
                )])
            return super().execute(statement, params)

    candidates = fetch_story_candidates(SemanticPairSession())

    assert [item.semantic_matches for item in candidates] == [
        ((2502, 0.91),),
        ((2501, 0.91),),
    ]


def test_article_event_key_uses_only_own_analysis_or_raw_response_key():
    event_key = "заседание совета консульских служб снг"

    class OwnEventKeySession:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "FROM threads t" in sql:
                normalized_sql = " ".join(sql.split())
                assert "NULLIF(an.event_key, '')" in normalized_sql
                assert "NULLIF(an.raw_response->>'event_key', '')" in normalized_sql
                assert ") AS article_event_key" in normalized_sql
                assert "t.thread_key) AS article_event_key" not in normalized_sql
                common = dict(
                    country_code="TJ",
                    thread_key=event_key,
                    thread_title="Консульский совет СНГ",
                    first_seen=NOW,
                    last_seen=NOW,
                    url=None,
                    published_at=NOW,
                    publisher_source_id=11,
                    source_name="Asia Plus",
                    sentiment=0.1,
                    action_level=3,
                    topics=["diplomacy"],
                )
                return FakeResult(rows=[
                    SimpleNamespace(
                        **common,
                        thread_id=9101,
                        article_id=101,
                        article_title="Без собственного ключа",
                        article_event_key=None,
                    ),
                    SimpleNamespace(
                        **common,
                        thread_id=9102,
                        article_id=102,
                        article_title="Ключ восстановлен из raw response",
                        article_event_key=event_key,
                    ),
                    SimpleNamespace(
                        **common,
                        thread_id=9103,
                        article_id=103,
                        article_title="Пустые собственные ключи",
                        article_event_key=None,
                    ),
                ])
            if "FROM article_entity_mentions" in sql:
                return FakeResult(rows=[])
            if "semantic_story_pairs" in sql:
                return FakeResult(rows=[])
            raise AssertionError(f"Unexpected SQL: {sql}")

    candidates = fetch_story_candidates(OwnEventKeySession())
    by_thread = {item.thread_id: item for item in candidates}
    peer = replace(
        candidate("KG", event_key=event_key),
        thread_id=9200,
        articles=(StoryArticle(
            202,
            "KG",
            "Подтверждённый ключ",
            None,
            NOW,
            "KG source",
            event_key=event_key,
            source_id=22,
        ),),
    )

    assert by_thread[9101].articles[0].event_key is None
    assert _filter_story_cluster_articles((by_thread[9101], peer)) is None
    assert by_thread[9102].articles[0].event_key == event_key
    supported = _filter_story_cluster_articles((by_thread[9102], peer))
    assert supported is not None
    assert [item.article_ids for item in supported] == [(102,), (202,)]
    assert by_thread[9103].articles[0].event_key is None
    assert _filter_story_cluster_articles((by_thread[9103], peer)) is None


def test_mixed_legacy_thread_uses_one_canonical_thread_country_candidate():
    session = MixedLegacyThreadFixtureSession()
    candidates = fetch_story_candidates(session)

    assert [
        (item.thread_id, item.country_code, item.article_ids)
        for item in candidates
    ] == [
        (2501, "ES", (501,)),
    ]
    candidate_sql = next(sql for sql in session.statements if "FROM threads t" in sql)
    assert "TRIM(t.country_code) AS country_code" in candidate_sql
    assert "TRIM(s.country_code) = TRIM(t.country_code)" in candidate_sql


def test_candidate_thread_and_date_scope_reaches_sql_before_mention_materialization():
    scope_start = NOW - timedelta(days=30)

    class ScopedCandidateSession:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params=None):
            sql = str(statement)
            params = params or {}
            self.calls.append((sql, params))
            if "FROM threads t" in sql:
                assert "t.id = ANY(:thread_ids)" in sql
                assert "ar.published_at >= :published_after" in sql
                assert params == {
                    "thread_ids": [41, 42],
                    "published_after": scope_start,
                }
                return FakeResult(rows=[SimpleNamespace(
                    thread_id=41,
                    country_code="AZ",
                    thread_key="recent-event",
                    thread_title="Recent event",
                    first_seen=scope_start - timedelta(days=60),
                    last_seen=NOW,
                    article_id=141,
                    article_title="Recent article",
                    url="https://example.test/141",
                    published_at=scope_start,
                    publisher_source_id=11,
                    source_name="AZ source",
                    sentiment=0.2,
                    action_level=3,
                    article_event_key="recent-event",
                    topics=["diplomacy"],
                )])
            if "FROM article_entity_mentions" in sql:
                assert params == {"article_ids": [141]}
                return FakeResult(rows=[SimpleNamespace(
                    article_id=141,
                    entity_id="entity-recent",
                )])
            if "semantic_story_pairs" in sql:
                assert params == {
                    "semantic_thread_ids": [41],
                    "semantic_article_ids": ["141"],
                    "semantic_published_ats": [scope_start],
                }
                assert "embedding_profiles" in sql
                assert "content_embeddings" in sql
                assert "ep.active = TRUE" in sql
                assert "ep.dimensions = 1024" in sql
                assert "ce.status = 'ready'" in sql
                assert "ce.object_type = 'article'" in sql
                assert "ANY(:semantic_article_ids)" in sql
                assert "ANY(:semantic_thread_ids)" in sql
                assert "HAVING COUNT(*) = 1" in sql
                assert "MIN(ca.published_at) AS activity_first_seen" in sql
                assert "MAX(ca.published_at) AS activity_last_seen" in sql
                left_bound = (
                    "left_thread.activity_first_seen <= "
                    "right_thread.activity_last_seen + INTERVAL '14 days'"
                )
                right_bound = (
                    "right_thread.activity_first_seen <= "
                    "left_thread.activity_last_seen + INTERVAL '14 days'"
                )
                assert left_bound in sql
                assert right_bound in sql
                assert sql.index(left_bound) < sql.index("<=>")
                assert sql.index(right_bound) < sql.index("<=>")
                return FakeResult(rows=[])
            raise AssertionError(f"Unexpected scoped candidate query: {sql}")

    session = ScopedCandidateSession()

    candidates = fetch_story_candidates(
        session,
        thread_ids=frozenset({42, 41}),
        published_after=scope_start,
    )

    assert [(item.thread_id, item.article_ids) for item in candidates] == [
        (41, (141,)),
    ]
    assert candidates[0].semantic_matches == ()
    assert len(session.calls) == 3


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


def test_scoped_story_persistence_guards_old_stories_and_executes_no_delete():
    session = PersistenceSession()
    scope_start = NOW - timedelta(days=30)

    persist_story_cluster(
        session,
        [candidate("AZ"), candidate("KZ")],
        now=NOW,
        membership_generation=1,
        minimum_existing_last_seen=scope_start,
        non_destructive=True,
    )

    lookup_sql, lookup_params = next(
        call for call in session.calls if "SELECT st.id, st.slug" in call[0]
    )
    assert "AND st.last_seen >= :minimum_existing_last_seen" in lookup_sql
    assert lookup_params["minimum_existing_last_seen"] == scope_start
    assert not any("DELETE FROM" in sql for sql in session.statements)


def test_scoped_story_build_excludes_pre_window_article_memberships(monkeypatch):
    import src.stories as stories_module

    scope_start = NOW - timedelta(days=30)

    def scoped_candidate(country, thread_id, article_id):
        old_article = StoryArticle(
            article_id=article_id - 100,
            country_code=country,
            title="Old",
            url=None,
            published_at=scope_start - timedelta(seconds=1),
            source_name="Old source",
            event_key="переговоры о транскаспийском маршруте",
            entity_ids=frozenset({"old-entity"}),
            topics=frozenset({"old-topic"}),
            source_id=1,
        )
        recent_article = StoryArticle(
            article_id=article_id,
            country_code=country,
            title="Recent",
            url=None,
            published_at=scope_start,
            source_name="Recent source",
            event_key="переговоры о транскаспийском маршруте",
            entity_ids=frozenset({"recent-entity"}),
            topics=frozenset({"recent-topic"}),
            source_id=2,
        )
        return replace(
            candidate(country),
            thread_id=thread_id,
            article_ids=(old_article.article_id, recent_article.article_id),
            articles=(old_article, recent_article),
            entities=frozenset({"old-entity", "recent-entity"}),
            topics=frozenset({"old-topic", "recent-topic"}),
            sources=frozenset({"Old source", "Recent source"}),
            source_ids=frozenset({1, 2}),
        )

    candidates = [
        scoped_candidate("AZ", 41, 141),
        scoped_candidate("KZ", 42, 142),
        scoped_candidate("UZ", 43, 143),
    ]
    observed = {}

    def fake_fetch(session, **kwargs):
        observed["fetch_kwargs"] = kwargs
        return candidates

    monkeypatch.setattr(stories_module, "fetch_story_candidates", fake_fetch)
    monkeypatch.setattr(
        stories_module,
        "derive_reactivation_pairs",
        lambda session, items: frozenset(),
    )
    monkeypatch.setattr(
        stories_module,
        "cluster_story_candidates",
        lambda items, *, reactivation_pairs: [tuple(items)],
    )
    monkeypatch.setattr(
        stories_module,
        "allocate_story_membership_generation",
        lambda session: 7,
    )

    def fake_persist(session, items, **kwargs):
        observed["candidates"] = list(items)
        observed["kwargs"] = kwargs
        return 9, sum(len(item.article_ids) for item in items)

    monkeypatch.setattr(stories_module, "persist_story_cluster", fake_persist)

    result = build_stories(
        object(),
        now=NOW,
        candidate_thread_ids=frozenset({41, 42, 43}),
        candidate_article_start=scope_start,
        refresh_lifecycles=False,
        minimum_existing_last_seen=scope_start,
        non_destructive=True,
    )

    assert result.article_memberships == 3
    assert [item.article_ids for item in observed["candidates"]] == [
        (141,),
        (142,),
        (143,),
    ]
    assert all(
        item.entities == frozenset({"recent-entity"})
        and item.topics == frozenset({"recent-topic"})
        and item.sources == frozenset({"Recent source"})
        and item.source_ids == frozenset({2})
        for item in observed["candidates"]
    )
    assert observed["kwargs"]["minimum_existing_last_seen"] == scope_start
    assert observed["kwargs"]["non_destructive"] is True
    assert observed["fetch_kwargs"] == {
        "thread_ids": frozenset({41, 42, 43}),
        "published_after": scope_start,
    }


def test_build_threads_recent_days_routes_only_to_bounded_rebuild(monkeypatch):
    import scripts.build_threads as build_threads_script

    calls = []
    monkeypatch.setattr(build_threads_script, "wait_for_db", lambda: calls.append("wait"))
    monkeypatch.setattr(
        build_threads_script,
        "rebuild_recent_threads_and_stories",
        lambda days: calls.append(("recent", days)),
    )
    monkeypatch.setattr(
        build_threads_script,
        "build_threads",
        lambda: calls.append("unbounded"),
    )
    monkeypatch.setattr(
        "sys.argv",
        ["build_threads.py", "--recent-days", "30"],
    )

    build_threads_script.main()

    assert calls == ["wait", ("recent", 30)]


def test_temperature_image_includes_story_audit_command():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile.temperature").read_text(encoding="utf-8")

    assert "scripts/audit_story_pipeline.py" in dockerfile


def test_story_pipeline_audit_is_read_only_and_has_stable_json_keys(monkeypatch):
    import scripts.audit_story_pipeline as audit

    left_article = StoryArticle(
        1,
        "AZ",
        "AZ event",
        None,
        NOW,
        "AZ source",
        event_key="переговоры о транскаспийском маршруте",
        entity_ids=frozenset({"entity-route"}),
    )
    right_article = StoryArticle(
        2,
        "KZ",
        "KZ event",
        None,
        NOW,
        "KZ source",
        event_key="переговоры о транскаспийском маршруте",
        entity_ids=frozenset({"entity-route"}),
    )
    candidates = [
        replace(candidate("AZ"), articles=(left_article,)),
        replace(candidate("KZ"), articles=(right_article,)),
    ]

    class ReadOnlyAuditSession:
        def __init__(self):
            self.statements = []

        def execute(self, statement, params=None):
            sql = str(statement)
            self.statements.append(sql)
            assert sql.lstrip().upper().startswith(("SELECT", "WITH", "/*"))
            assert not any(
                token in sql.upper()
                for token in ("INSERT ", "UPDATE ", "DELETE ", "MERGE ", "CALL ")
            )
            if "audit_embedding_coverage" in sql:
                return FakeResult(row=SimpleNamespace(embedded_articles=1))
            if "audit_country_mismatches" in sql:
                return FakeResult(rows=[SimpleNamespace(
                    thread_id=99,
                    article_id=199,
                    thread_country="AZ",
                    article_country="GB",
                )])
            if "lifecycle = 'resolved'" in sql:
                return FakeResult(rows=[])
            raise AssertionError(f"Unexpected audit query: {sql}")

        def commit(self):
            raise AssertionError("read-only audit must not commit")

    session = ReadOnlyAuditSession()
    fetch_calls = []

    def fake_fetch(active, **kwargs):
        fetch_calls.append(kwargs)
        return candidates

    monkeypatch.setattr(audit, "fetch_story_candidates", fake_fetch)

    report = audit.run_audit(session, recent_days=30, now=NOW)

    assert set(report) == {
        "candidate_totals",
        "canonical_entity_coverage",
        "embedding_coverage",
        "pair_rejection_reasons",
        "proposed_clusters",
        "same_thread_defects",
        "country_mismatches",
    }
    assert report["candidate_totals"] == {
        "scope_days": 30,
        "candidates": 2,
        "threads": 2,
        "countries": 2,
        "articles": 2,
    }
    assert report["canonical_entity_coverage"]["articles_with_entities"] == 2
    assert report["embedding_coverage"]["articles_with_embeddings"] == 1
    assert set(report["pair_rejection_reasons"]) == {
        "pairs_total",
        "pairs_scored",
        "accepted",
        "same_thread",
        "same_country_pair",
        "missing_concrete_event_anchor",
        "generic_event_key",
        "time_window_exceeded",
        "reactivation_requires_event_and_entity",
    }
    assert report["pair_rejection_reasons"]["accepted"] == 1
    assert report["proposed_clusters"] == [{
        "thread_ids": [1, 2],
        "countries": ["AZ", "KZ"],
        "article_ids": [1, 2],
    }]
    assert report["same_thread_defects"] == []
    assert report["country_mismatches"] == [{
        "thread_id": 99,
        "article_id": 199,
        "thread_country": "AZ",
        "article_country": "GB",
    }]
    assert fetch_calls == [{"published_after": NOW - timedelta(days=30)}]
    assert session.statements


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
        if "FROM story_membership_clock" in sql:
            return FakeResult(row=SimpleNamespace(generation=12))
        if "WITH RECURSIVE forward_chain AS" in sql:
            if params["story_id"] not in {6, 7}:
                return FakeResult(rows=[])
            return FakeResult(rows=[SimpleNamespace(
                id=params["story_id"], meta={}, path=[params["story_id"]], depth=0,
            )])
        if "WHERE st.id = :story_id" in sql:
            return FakeResult(row=story_row(7) if params["story_id"] == 7 else None)
        if "country_stats" in sql and "primary_url_candidates" in sql:
            return FakeResult(rows=[SimpleNamespace(
                country_code="AZ", article_count=2, source_count=2, media_tone=-0.2,
                first_seen=NOW - timedelta(days=2), last_seen=NOW,
                primary_url="https://example.test/az",
            )])
        if "WITH entity_aggregates AS" in sql:
            return FakeResult(rows=[SimpleNamespace(
                entity_id="entity-route", mentions=3, confidence=0.9,
                evidence={"article_ids": [1, 2]}, canonical_name="Route",
                kind="infrastructure",
            )])
        if "WITH representative_events AS" in sql:
            return FakeResult(rows=[SimpleNamespace(
                entity_id="entity-route", event_key="транскаспийский маршрут",
                event_at=NOW, action_level=3,
                evidence={"article_ids": [1], "representative_article_id": 1},
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


class UnknownMembershipStorySession(FakeStorySession):
    def execute(self, statement, params=None):
        sql = str(statement)
        normalized_sql = " ".join(sql.split())
        params = params or {}
        if "WITH story_rank_raw AS" in sql and "WHERE st.id = :story_id" in sql:
            self.calls.append((sql, params))
            row = story_row(7)
            verified_url = "https://elpais.com/verified-story"
            unknown_url = "https://news.google.com/rss/articles/unknown-story"
            row.primary_url_candidates = (
                [verified_url]
                if "JOIN article_country_facts primary_source " in normalized_sql
                else [unknown_url, verified_url]
            )
            return FakeResult(row=row)
        if "WITH entity_aggregates AS" in sql:
            self.calls.append((sql, params))
            verified = SimpleNamespace(
                entity_id="entity-verified",
                mentions=1,
                confidence=0.8,
                evidence={"article_ids": [501]},
                canonical_name="Verified entity",
                kind="organization",
            )
            unknown = SimpleNamespace(
                entity_id="entity-unknown",
                mentions=1,
                confidence=0.99,
                evidence={"article_ids": [503]},
                canonical_name="Unknown entity",
                kind="organization",
            )
            rows = (
                [verified]
                if "JOIN article_country_facts entity_source " in normalized_sql
                else [unknown, verified]
            )
            return FakeResult(rows=rows)
        if "WITH representative_events AS" in sql:
            self.calls.append((sql, params))
            verified = SimpleNamespace(
                entity_id="entity-verified",
                event_key="verified event",
                event_at=NOW - timedelta(minutes=1),
                action_level=3,
                evidence={"article_ids": [501], "representative_article_id": 501},
                confidence=0.8,
            )
            unknown = SimpleNamespace(
                entity_id="entity-unknown",
                event_key="unknown event",
                event_at=NOW,
                action_level=6,
                evidence={"article_ids": [503], "representative_article_id": 503},
                confidence=0.99,
            )
            rows = (
                [verified]
                if "JOIN article_country_facts event_source " in normalized_sql
                else [unknown, verified]
            )
            return FakeResult(rows=rows)
        return super().execute(statement, params)


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
        if "COUNT(DISTINCT ar.id)::integer AS article_count" in sql and params.get("country_code"):
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
        if "FROM story_membership_clock" in sql:
            return FakeResult(row=SimpleNamespace(generation=12))
        if "WITH RECURSIVE forward_chain AS" in sql:
            return super().execute(statement, params)
        unsafe_story = story_row(7)
        unsafe_story.primary_url = "data:text/html,boom"
        unsafe_story.primary_url_candidates = [
            "data:text/html,boom",
            "javascript:alert(1)",
            "https://safe.example/story-primary",
        ]
        if "WHERE st.id = :story_id" in sql:
            return FakeResult(row=unsafe_story)
        if "country_stats" in sql and "primary_url_candidates" in sql:
            return FakeResult(rows=[SimpleNamespace(
                country_code="AZ", article_count=4, source_count=2, media_tone=-0.2,
                first_seen=NOW - timedelta(days=2), last_seen=NOW,
                primary_url="javascript:alert(1)",
                primary_url_candidates=[
                    "javascript:alert(1)",
                    "https://safe.example/country-primary",
                ],
            )])
        if "WITH entity_aggregates AS" in sql:
            return super().execute(statement, params)
        if "WITH representative_events AS" in sql:
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
        if "WITH RECURSIVE forward_chain AS" in sql:
            return FakeResult(rows=[
                SimpleNamespace(
                    id=11,
                    meta={"merged_into_story_id": 10},
                    path=[11],
                    depth=0,
                ),
                SimpleNamespace(id=10, meta={}, path=[11, 10], depth=1),
            ])
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


class MergeChainStorySession(FakeStorySession):
    def __init__(self, chain):
        super().__init__()
        self.chain = chain

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "WITH RECURSIVE forward_chain AS" in sql:
            self.calls.append((sql, params))
            rows = []
            path = []
            current = params["story_id"]
            while current in self.chain and current not in path:
                path.append(current)
                meta = self.chain[current] or {}
                rows.append(SimpleNamespace(
                    id=current,
                    meta=meta,
                    path=list(path),
                    depth=len(path) - 1,
                ))
                target = meta.get("merged_into_story_id")
                if not isinstance(target, int) or isinstance(target, bool):
                    break
                if target in path or target not in self.chain:
                    break
                current = target
            return FakeResult(rows=rows)
        if "WHERE st.id = :story_id" in sql:
            self.calls.append((sql, params))
            story_id = params["story_id"]
            meta = self.chain.get(story_id)
            if meta is None and story_id not in self.chain:
                return FakeResult(row=None)
            row = story_row(story_id)
            row.meta = meta or {}
            if not row.meta.get("merged_into_story_id"):
                row.slug = f"story-canonical-{story_id}"
            return FakeResult(row=row)
        return super().execute(statement, params)


class SnapshotArticleSession(FakeStorySession):
    def __init__(self):
        super().__init__()
        self.mutated = False

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "WITH ranked_articles" in sql:
            self.calls.append((sql, params))
            stable_query = (
                "sa.membership_generation <= :membership_generation" in sql
                and "membership_confidence_snapshot" in sql
                and "JOIN analysis" not in sql
            )
            if "article_cursor_relevance" not in params:
                return FakeResult(rows=[
                    SimpleNamespace(
                        article_id=1, title="First", url="https://example.test/1",
                        published_at=NOW, source="Source", country_code="AZ",
                        membership_confidence=0.9,
                        relevance_score=0.9,
                        evidence={"membership_confidence_snapshot": 0.9},
                        is_primary=True,
                    ),
                    SimpleNamespace(
                        article_id=2, title="Second", url="https://example.test/2",
                        published_at=NOW - timedelta(minutes=1), source="Source",
                        country_code="KZ", membership_confidence=0.8,
                        relevance_score=0.8,
                        evidence={"membership_confidence_snapshot": 0.8},
                        is_primary=True,
                    ),
                ])
            if stable_query and self.mutated:
                return FakeResult(rows=[SimpleNamespace(
                    article_id=2, title="Second", url="https://example.test/2",
                    published_at=NOW - timedelta(minutes=1), source="Source",
                    country_code="KZ", membership_confidence=0.05,
                    relevance_score=0.8,
                    evidence={"membership_confidence_snapshot": 0.8},
                    is_primary=True,
                )])
            return FakeResult(rows=[
                SimpleNamespace(
                    article_id=1, title="First", url="https://example.test/1",
                    published_at=NOW, source="Source", country_code="AZ",
                    membership_confidence=0.05, relevance_score=0.05,
                    evidence={"membership_confidence_snapshot": 0.9},
                    is_primary=True,
                ),
                SimpleNamespace(
                    article_id=3, title="Added later", url="https://example.test/3",
                    published_at=NOW + timedelta(minutes=1), source="Source",
                    country_code="ES", membership_confidence=1.0,
                    relevance_score=1.0,
                    evidence={"membership_confidence_snapshot": 1.0},
                    is_primary=True,
                ),
            ])
        return super().execute(statement, params)


class SnapshotDetailEvidenceSession(SnapshotArticleSession):
    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        old_entity = SimpleNamespace(
            entity_id="entity-old", mentions=2, confidence=0.75,
            evidence={"article_ids": [1, 2]}, canonical_name="Old entity",
            kind="person",
        )
        new_entity = SimpleNamespace(
            entity_id="entity-new", mentions=1, confidence=0.99,
            evidence={"article_ids": [3]}, canonical_name="New entity",
            kind="person",
        )
        old_event = SimpleNamespace(
            entity_id="entity-old", event_key="old event", event_at=NOW,
            action_level=3,
            evidence={"article_ids": [1], "representative_article_id": 1},
            confidence=0.75,
        )
        new_event = SimpleNamespace(
            entity_id="entity-new", event_key="new event",
            event_at=NOW + timedelta(minutes=1), action_level=6,
            evidence={"article_ids": [3], "representative_article_id": 3},
            confidence=0.99,
        )
        if "WITH entity_aggregates AS" in sql:
            self.calls.append((sql, params))
            return FakeResult(rows=[old_entity])
        if "WITH representative_events AS" in sql:
            self.calls.append((sql, params))
            return FakeResult(rows=[old_event])
        if "FROM story_entities se" in sql:
            self.calls.append((sql, params))
            return FakeResult(rows=[new_entity if self.mutated else old_entity])
        if "FROM story_events sve" in sql:
            self.calls.append((sql, params))
            return FakeResult(rows=[new_event if self.mutated else old_event])
        return super().execute(statement, params)


class FractionalRankSession(FakeStorySession):
    def __init__(self):
        super().__init__()
        self.rows = []
        for story_id, relevance in (
            (9, Decimal("0.833333")),
            (8, Decimal("0.833333")),
            (7, Decimal("0.666667")),
        ):
            row = story_row(story_id)
            row.relevance_score = relevance
            self.rows.append(row)

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "ORDER BY lifecycle_rank ASC" in sql:
            self.calls.append((sql, params))
            rows = self.rows
            if "cursor_relevance" in params:
                cursor_relevance = Decimal(str(params["cursor_relevance"]))
                cursor_id = params["cursor_id"]
                rows = [
                    row for row in rows
                    if row.relevance_score < cursor_relevance
                    or (
                        row.relevance_score == cursor_relevance
                        and row.id < cursor_id
                    )
                ]
            return FakeResult(rows=rows[:params["limit"]])
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
    assert payload["consistency"]["ranking_at"]
    assert payload["consistency"]["membership_generation"] == 12
    assert payload["consistency"]["mode"] == "membership_generation_live_filters"
    assert "membership" in payload["consistency"]["frozen_features"]
    assert "lifecycle" in payload["consistency"]["live_filters"]
    assert "not a full point-in-time snapshot" in payload["consistency"]["limitation"]
    list_sql, params = next(
        call for call in session.calls if "ORDER BY lifecycle_rank ASC" in call[0]
    )
    assert params["country"] == "AZ"
    assert params["lifecycle"] == "developing"
    assert params["min_confidence"] == 0.7
    assert params["membership_generation"] == 12
    assert "sa.membership_generation <= :membership_generation" in list_sql
    assert "primary_membership.membership_generation <= :membership_generation" in list_sql
    assert "rf.article_count AS article_count" in list_sql
    assert "rf.country_codes" in list_sql
    assert "JOIN article_country_facts src ON src.article_id = ar.id" in list_sql
    assert "COUNT(DISTINCT src.id)::integer AS source_count" in list_sql


def test_story_detail_includes_evidence_and_country_primary_urls(monkeypatch):
    client, session = story_client(monkeypatch)

    response = client.get("/api/v2/stories/7")

    assert response.status_code == 200
    payload = response.json()
    assert payload["countries"][0]["primary_url"] == "https://example.test/az"
    assert payload["entities"][0]["evidence"] == {"article_ids": [1, 2]}
    assert payload["events"][0]["evidence"] == {
        "article_ids": [1],
        "representative_article_id": 1,
    }
    assert payload["articles"][0]["is_primary"] is True
    detail_sql, detail_params = next(
        call for call in session.calls if "WHERE st.id = :story_id" in call[0]
    )
    assert ":ranking_at" in detail_sql
    assert detail_params["ranking_at"].tzinfo is not None
    assert detail_params["membership_generation"] == 12
    article_sql, article_params = next(
        call for call in session.calls if "WITH ranked_articles" in call[0]
    )
    assert "sa.membership_generation <= :membership_generation" in article_sql
    assert article_params["membership_generation"] == 12
    assert "JOIN article_country_facts s ON s.article_id = ar.id" in article_sql
    assert "JOIN sources" not in article_sql
    country_sql, country_params = next(
        call for call in session.calls
        if "country_stats" in call[0] and "primary_url_candidates" in call[0]
    )
    assert "sa.membership_generation <= :membership_generation" in country_sql
    assert country_params["membership_generation"] == 12
    assert country_sql.count(
        "JOIN article_country_facts s ON s.article_id = ar.id"
    ) == 2
    assert "COUNT(DISTINCT s.id)::integer AS source_count" in country_sql
    assert "JOIN sources" not in country_sql


def test_story_detail_excludes_unknown_primary_url_entity_and_event_evidence(
    monkeypatch,
):
    client, _ = story_client(monkeypatch, UnknownMembershipStorySession())

    response = client.get("/api/v2/stories/7")

    assert response.status_code == 200
    payload = response.json()
    assert payload["primary_url"] == "https://elpais.com/verified-story"
    assert [item["entity_id"] for item in payload["entities"]] == [
        "entity-verified",
    ]
    assert [item["event_key"] for item in payload["events"]] == [
        "verified event",
    ]


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
    sql, params = next(
        call for call in session.calls
        if "COUNT(DISTINCT ar.id)::integer AS article_count" in call[0]
        and call[1].get("country_code") == "AZ"
    )
    assert "story_articles" in sql
    assert "membership_generation <= :membership_generation" in sql
    assert params == {
        "story_ids": [7],
        "country_code": "AZ",
        "membership_generation": 12,
    }


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
    sql, params = next(
        call for call in session.calls if "ORDER BY lifecycle_rank ASC" in call[0]
    )
    assert params["topic"] == "energy"
    assert params["entity_id"] == "entity-route"
    assert params["date_from"].isoformat().startswith("2026-07-01")
    assert params["date_to"].isoformat().startswith("2026-07-20")
    assert "FROM story_articles filter_membership" in sql
    assert "JOIN articles filter_article" in sql
    assert (
        "JOIN article_country_facts filter_source "
        "ON filter_source.article_id = filter_article.id"
    ) in " ".join(sql.split())
    assert "JOIN article_entity_mentions filter_entity" in sql
    assert "meta->'topics'" in sql
    assert "sa.membership_generation <= :membership_generation" in sql
    assert "action_level_snapshot" in sql
    assert "JOIN analysis" not in sql
    assert "EXTRACT(EPOCH FROM (:ranking_at - raw.last_seen))" in sql
    assert "raw.article_count::numeric" in sql
    assert "raw.source_count::numeric" in sql
    assert "raw.country_count" in sql
    assert "raw.highest_action_level" in sql
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


def test_story_activity_ranking_preserves_action_level_six():
    level_five = stories_routes._story_activity_score(
        action_level=5,
        article_count=4,
        source_count=2,
        country_count=2,
        first_seen=NOW - timedelta(days=1),
        last_seen=NOW,
        ranking_at=NOW,
    )
    level_six = stories_routes._story_activity_score(
        action_level=6,
        article_count=4,
        source_count=2,
        country_count=2,
        first_seen=NOW - timedelta(days=1),
        last_seen=NOW,
        ranking_at=NOW,
    )

    assert level_six[1] > level_five[1]
    assert "^[1-6]$" in stories_routes.STORY_RANK_FEATURES_CTE
    assert "LEAST(raw.highest_action_level, 6)" in (
        stories_routes.STORY_RELEVANCE_EXPRESSION_SQL
    )
    assert "/ 6" in stories_routes.STORY_RELEVANCE_EXPRESSION_SQL


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


def test_story_cursor_uses_one_fixed_decimal_rank_key_without_gaps(monkeypatch):
    session = FractionalRankSession()
    client, _ = story_client(monkeypatch, session)
    cursor = None
    observed_ids = []

    while True:
        params = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/api/v2/stories", params=params)
        assert response.status_code == 200
        payload = response.json()
        observed_ids.extend(story["id"] for story in payload["stories"])
        cursor = payload["next_cursor"]
        if not cursor:
            break

    assert observed_ids == [9, 8, 7]
    assert len(observed_ids) == len(set(observed_ids))
    first_cursor = client.get("/api/v2/stories", params={"limit": 1}).json()[
        "next_cursor"
    ]
    cursor_payload = json.loads(base64.urlsafe_b64decode(
        first_cursor + "=" * (-len(first_cursor) % 4)
    ).decode("utf-8"))
    assert cursor_payload[5] == "0.833333"
    followup_params = next(
        params for _, params in session.calls if "cursor_relevance" in params
    )
    assert followup_params["cursor_relevance"] == Decimal("0.833333")
    assert isinstance(followup_params["cursor_relevance"], Decimal)
    list_sql = next(
        sql for sql, _ in session.calls if "ORDER BY lifecycle_rank ASC" in sql
    )
    assert "ROUND(" in list_sql
    assert "6)::numeric(8,6)" in list_sql
    assert "AS relevance_score" in list_sql
    assert stories_routes.STORY_RELEVANCE_SQL == "rf.relevance_score"


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
    assert "sa.membership_generation <= :membership_generation" in list_sql
    assert "action_level_snapshot" in list_sql
    assert "JOIN analysis" not in list_sql
    assert "COALESCE(st.article_count" not in list_sql


def test_merge_after_first_page_is_excluded_by_membership_generation(monkeypatch):
    client, list_session = story_client(monkeypatch)
    first = client.get("/api/v2/stories?limit=1")
    cursor = first.json()["next_cursor"]
    membership_generation = first.json()["consistency"]["membership_generation"]
    merge_session = DuplicatePersistenceSession()

    persist_story_cluster(
        merge_session,
        [candidate("AZ"), candidate("KZ")],
        now=NOW - timedelta(hours=1),
        membership_generation=membership_generation + 1,
    )
    second = client.get("/api/v2/stories", params={"limit": 1, "cursor": cursor})

    assert second.status_code == 200
    reconcile_sql, reconcile_params = next(
        call for call in merge_session.calls
        if "SELECT :primary_story_id" in call[0] and "FROM story_articles" in call[0]
    )
    assert reconcile_params["membership_generation"] == membership_generation + 1
    assert ":membership_generation AS membership_generation" in reconcile_sql
    second_list_sql, second_list_params = next(
        call for call in list_session.calls
        if "cursor_lifecycle_rank" in call[1]
    )
    assert second_list_params["membership_generation"] == membership_generation
    assert "sa.membership_generation <= :membership_generation" in second_list_sql


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
    cursor_payload = json.loads(base64.urlsafe_b64decode(
        payload["articles_next_cursor"]
        + "=" * (-len(payload["articles_next_cursor"]) % 4)
    ).decode("utf-8"))
    assert cursor_payload[0] == stories_routes.ARTICLE_CURSOR_VERSION
    assert len(cursor_payload) == 7
    assert datetime.fromisoformat(cursor_payload[2].replace("Z", "+00:00")).tzinfo
    assert cursor_payload[3] == 12


def test_article_cursor_freezes_membership_set_and_rank_across_mutations(monkeypatch):
    session = SnapshotArticleSession()
    client, _ = story_client(monkeypatch, session)

    first = client.get("/api/v2/stories/7?article_limit=1")
    session.mutated = True
    second = client.get(
        "/api/v2/stories/7",
        params={
            "article_limit": 1,
            "article_cursor": first.json()["articles_next_cursor"],
        },
    )

    assert first.status_code == second.status_code == 200
    assert [item["article_id"] for item in first.json()["articles"]] == [1]
    assert [item["article_id"] for item in second.json()["articles"]] == [2]
    article_calls = [call for call in session.calls if "WITH ranked_articles" in call[0]]
    assert article_calls[1][1]["ranking_at"] == article_calls[0][1]["ranking_at"]
    assert article_calls[1][1]["membership_generation"] == 12
    assert "sa.membership_generation <= :membership_generation" in article_calls[1][0]
    assert "membership_confidence_snapshot" in article_calls[1][0]
    assert "JOIN analysis" not in article_calls[1][0]


def test_article_cursor_freezes_entities_and_events_at_membership_generation(
    monkeypatch,
):
    session = SnapshotDetailEvidenceSession()
    client, _ = story_client(monkeypatch, session)

    first = client.get("/api/v2/stories/7?article_limit=1")
    session.mutated = True
    second = client.get(
        "/api/v2/stories/7",
        params={
            "article_limit": 1,
            "article_cursor": first.json()["articles_next_cursor"],
        },
    )

    assert first.status_code == second.status_code == 200
    assert [item["entity_id"] for item in first.json()["entities"]] == ["entity-old"]
    assert [item["entity_id"] for item in second.json()["entities"]] == ["entity-old"]
    assert [item["event_key"] for item in first.json()["events"]] == ["old event"]
    assert [item["event_key"] for item in second.json()["events"]] == ["old event"]
    entity_sql, entity_params = next(
        call for call in session.calls if "WITH entity_aggregates AS" in call[0]
    )
    event_sql, event_params = next(
        call for call in session.calls if "WITH representative_events AS" in call[0]
    )
    assert "story_entities" not in entity_sql
    assert "story_events" not in event_sql
    assert "sa.membership_generation <= :membership_generation" in entity_sql
    assert "sa.membership_generation <= :membership_generation" in event_sql
    assert entity_params["membership_generation"] == 12
    assert event_params["membership_generation"] == 12


def test_article_cursor_rejects_invalid_ranking_context(monkeypatch):
    client, _ = story_client(monkeypatch, PaginatedArticleSession())
    first = client.get("/api/v2/stories/7?article_limit=1")
    cursor = first.json()["articles_next_cursor"]
    payload = json.loads(base64.urlsafe_b64decode(
        cursor + "=" * (-len(cursor) % 4)
    ).decode("utf-8"))
    payload[2] = 123
    invalid = base64.urlsafe_b64encode(
        json.dumps(payload).encode("utf-8")
    ).decode("ascii")

    response = client.get(
        "/api/v2/stories/7",
        params={"article_limit": 1, "article_cursor": invalid},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid article cursor"


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


def test_story_cursor_rejects_noncanonical_or_unsafe_values(monkeypatch):
    client, _ = story_client(monkeypatch)
    valid = client.get("/api/v2/stories?limit=1").json()["next_cursor"]
    payload = json.loads(base64.urlsafe_b64decode(
        valid + "=" * (-len(valid) % 4)
    ).decode("utf-8"))

    tampered = ["$$$$", valid + "="]
    for index, value in (
        (2, "2026-07-15T12:00:00"),
        (5, 2.0),
        (6, "2026-07-15T12:00:00"),
        (7, 0),
    ):
        changed = list(payload)
        changed[index] = value
        tampered.append(base64.urlsafe_b64encode(
            json.dumps(changed, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("="))

    responses = [
        client.get("/api/v2/stories", params={"limit": 1, "cursor": cursor})
        for cursor in tampered
    ]
    assert all(response.status_code == 400 for response in responses)


def test_article_cursor_rejects_noncanonical_or_unsafe_values(monkeypatch):
    client, _ = story_client(monkeypatch, PaginatedArticleSession())
    valid = client.get("/api/v2/stories/7?article_limit=1").json()[
        "articles_next_cursor"
    ]
    payload = json.loads(base64.urlsafe_b64decode(
        valid + "=" * (-len(valid) % 4)
    ).decode("utf-8"))

    tampered = ["$$$$", valid + "="]
    for index, value in (
        (2, "2026-07-15T12:00:00"),
        (4, 2.0),
        (5, "2026-07-15T12:00:00"),
        (6, 0),
    ):
        changed = list(payload)
        changed[index] = value
        tampered.append(base64.urlsafe_b64encode(
            json.dumps(changed, separators=(",", ":")).encode("utf-8")
        ).decode("ascii").rstrip("="))

    responses = [
        client.get(
            "/api/v2/stories/7",
            params={"article_limit": 1, "article_cursor": cursor},
        )
        for cursor in tampered
    ]
    assert all(response.status_code == 400 for response in responses)


def test_story_detail_requires_a_positive_story_id(monkeypatch):
    client, _ = story_client(monkeypatch)

    response = client.get("/api/v2/stories/0")

    assert response.status_code == 422


def test_story_api_serializes_only_http_urls_with_hostnames(monkeypatch):
    client, session = story_client(monkeypatch, UnsafeUrlStorySession())

    listing = client.get("/api/v2/stories?limit=1")
    detail = client.get("/api/v2/stories/7")

    assert listing.status_code == 200
    assert listing.json()["stories"][0]["primary_url"] == (
        "https://safe.example/story-primary"
    )
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["primary_url"] == "https://safe.example/story-primary"
    assert payload["countries"][0]["primary_url"] == (
        "https://safe.example/country-primary"
    )
    assert [article["url"] for article in payload["articles"]] == [
        None,
        None,
        None,
        "https://safe.example/story",
    ]
    candidate_queries = [
        sql for sql, _ in session.calls if "primary_url_candidates" in sql
    ]
    assert candidate_queries
    assert all("LIMIT 5" in sql for sql in candidate_queries)


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

    persist_story_cluster(first_session, first_cluster, now=NOW, membership_generation=1)
    persist_story_cluster(second_session, second_cluster, now=NOW, membership_generation=1)

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
        persist_story_cluster(
            session, [candidate("AZ"), candidate("KZ")], now=NOW,
            membership_generation=1,
        )


def test_persistence_recomputes_header_counts_from_saved_memberships():
    session = PersistenceSession()

    persist_story_cluster(
        session, [candidate("AZ"), candidate("KZ")], now=NOW,
        membership_generation=1,
    )

    aggregate_sql = "\n".join(session.statements)
    assert "UPDATE stories st SET" in aggregate_sql
    assert "COUNT(DISTINCT s.id) AS source_count" in aggregate_sql
    assert aggregate_sql.count(
        "JOIN article_country_facts s ON s.article_id = ar.id"
    ) >= 2
    assert "COUNT(DISTINCT s.country_code)" in aggregate_sql
    assert "MAX(LEAST(6, GREATEST(1, COALESCE(an.action_level, 1))))" in aggregate_sql


def test_persisted_story_entity_and_event_evidence_requires_verified_memberships():
    session = PersistenceSession()

    persist_story_cluster(
        session, [candidate("AZ"), candidate("KZ")], now=NOW,
        membership_generation=1,
    )

    entity_sql = next(
        sql for sql in session.statements if "INSERT INTO story_entities" in sql
    )
    event_sql = next(
        sql for sql in session.statements if "INSERT INTO story_events" in sql
    )
    normalized_entity_sql = " ".join(entity_sql.split())
    normalized_event_sql = " ".join(event_sql.split())
    assert "JOIN articles ar ON ar.id = sa.article_id" in normalized_entity_sql
    assert (
        "JOIN article_country_facts entity_source "
        "ON entity_source.article_id = ar.id"
    ) in normalized_entity_sql
    assert (
        "JOIN article_country_facts event_source "
        "ON event_source.article_id = ar.id"
    ) in normalized_event_sql


@pytest.mark.parametrize("invalid_action_level", [0, 7])
def test_persistence_rejects_action_levels_outside_story_scale(invalid_action_level):
    session = PersistenceSession()
    invalid_candidate = replace(
        candidate("AZ"),
        highest_action_level=invalid_action_level,
    )

    with pytest.raises(ValueError, match="action_level must be between 1 and 6"):
        persist_story_cluster(
            session,
            [invalid_candidate, candidate("KZ")],
            now=NOW,
            membership_generation=1,
        )

    assert not session.calls


def test_persistence_uses_real_entity_confidence_and_one_representative_event_row():
    session = PersistenceSession()

    persist_story_cluster(
        session, [candidate("AZ"), candidate("KZ")], now=NOW,
        membership_generation=1,
    )

    aggregate_sql = "\n".join(session.statements)
    assert "AVG(aem.confidence)" in aggregate_sql
    assert "DISTINCT ON (aem.entity_id)" in aggregate_sql
    assert "representative_article_id" in aggregate_sql


def test_membership_evidence_reproduces_score_and_names_peer():
    session = PersistenceSession()

    persist_story_cluster(
        session, [candidate("AZ"), candidate("KZ")], now=NOW,
        membership_generation=1,
    )

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
        assert evidence["membership_confidence_snapshot"] == pytest.approx(
            next(
                params["confidence"]
                for sql, params in session.calls
                if "INSERT INTO story_articles" in sql
                and "VALUES" in sql
                and json.loads(params["evidence"]) == evidence
            )
        )
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
    assert "membership_confidence_snapshot" in membership_sql
    assert "jsonb_typeof" in membership_sql


def test_persistence_assigns_generation_only_to_new_memberships():
    session = DuplicatePersistenceSession()

    persist_story_cluster(
        session,
        [candidate("AZ"), candidate("KZ")],
        now=NOW,
        membership_generation=17,
    )

    reconciliation_sql, reconciliation_params = next(
        call for call in session.calls
        if "SELECT :primary_story_id" in call[0] and "FROM story_articles" in call[0]
    )
    membership_sql, membership_params = next(
        call for call in session.calls
        if "INSERT INTO story_articles" in call[0] and "VALUES" in call[0]
    )
    assert reconciliation_params["membership_generation"] == 17
    assert ":membership_generation AS membership_generation" in reconciliation_sql
    assert membership_params["membership_generation"] == 17
    assert "membership_generation" in membership_sql.split("VALUES", 1)[0]
    assert ":membership_generation" in membership_sql.split("VALUES", 1)[1]
    assert "membership_generation =" not in membership_sql.split(
        "ON CONFLICT (story_id, article_id) DO UPDATE SET", 1
    )[1]


def test_persistence_checks_stable_thread_identity_before_summary_generation():
    session = PersistenceSession()

    persist_story_cluster(
        session, [candidate("AZ"), candidate("KZ")], now=NOW,
        membership_generation=1,
    )

    lookup_sql = next(sql for sql in session.statements if "SELECT st.id, st.slug" in sql)
    assert "thread_ids" in lookup_sql


def test_overlapping_story_ids_are_reconciled_into_one_primary():
    session = DuplicatePersistenceSession()

    persist_story_cluster(
        session, [candidate("AZ"), candidate("KZ")], now=NOW,
        membership_generation=1,
    )

    reconcile_call = next(
        call for call in session.calls
        if "SELECT :primary_story_id" in call[0] and "FROM story_articles" in call[0]
    )
    supersede_call = next(
        call for call in session.calls
        if "merged_into_story_id" in call[0] and "UPDATE stories" in call[0]
    )
    assert reconcile_call[1] == {
        "primary_story_id": 10,
        "duplicate_story_ids": [11],
        "now": NOW,
        "membership_generation": 1,
    }
    assert ":now AS added_at" in reconcile_call[0]
    conflict_clause = reconcile_call[0].split(
        "ON CONFLICT (story_id, article_id) DO UPDATE SET", 1
    )[1]
    assert "added_at" not in conflict_clause
    assert "membership_confidence_snapshot" in reconcile_call[0]
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


def test_story_merge_chain_resolves_only_final_canonical_story(monkeypatch):
    session = MergeChainStorySession({
        7: {"merged_into_story_id": 8},
        8: {"merged_into_story_id": 9},
        9: {},
    })
    client, _ = story_client(monkeypatch, session)

    response = client.get("/api/v2/stories/7")

    assert response.status_code == 200
    assert response.json()["id"] == 9
    assert response.json()["redirected_from_story_id"] == 7
    resolver_sql = next(
        sql for sql, _ in session.calls if "forward_chain" in sql
    )
    assert "WITH RECURSIVE" in resolver_sql
    assert "~ '^[1-9][0-9]*$'" in resolver_sql
    assert "ANY(chain.path)" in resolver_sql
    assert "max_merge_depth" in resolver_sql


def test_story_merge_chain_missing_target_returns_404(monkeypatch):
    client, _ = story_client(
        monkeypatch,
        MergeChainStorySession({7: {"merged_into_story_id": 999}}),
    )

    response = client.get("/api/v2/stories/7")

    assert response.status_code == 404
    assert response.json()["detail"] == "Canonical story not found"


@pytest.mark.parametrize("chain", [
    {7: {"merged_into_story_id": "not-an-id"}},
    {
        7: {"merged_into_story_id": 8},
        8: {"merged_into_story_id": 7},
    },
])
def test_story_merge_chain_rejects_invalid_target_and_cycles(monkeypatch, chain):
    client, _ = story_client(monkeypatch, MergeChainStorySession(chain))

    response = client.get("/api/v2/stories/7")

    assert response.status_code == 409
    assert response.json()["detail"] == "Invalid story merge chain"


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
        membership_generation=1,
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

    persist_story_cluster(
        session, [candidate("AZ"), candidate("KZ")], now=NOW,
        membership_generation=1,
    )

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

    story_id, _ = persist_story_cluster(
        session, cluster, now=NOW, membership_generation=1
    )

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

    first_story_id, _ = persist_story_cluster(
        session, cluster, now=NOW, membership_generation=1
    )
    first_slug = session.active_story.slug
    second_story_id, _ = persist_story_cluster(
        session, cluster, now=NOW, membership_generation=2
    )

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

    old = replace(
        candidate(
            "AZ",
            first_seen=NOW - timedelta(days=20),
            last_seen=NOW - timedelta(days=15),
        ),
        articles=(StoryArticle(
            1,
            "AZ",
            "Переговоры о транскаспийском маршруте",
            None,
            NOW - timedelta(days=15),
            "AZ source",
            event_key="переговоры о транскаспийском маршруте",
            entity_ids=frozenset({"entity-route"}),
        ),),
    )
    new = replace(
        candidate("KZ", first_seen=NOW, last_seen=NOW),
        articles=(StoryArticle(
            2,
            "KZ",
            "Переговоры о транскаспийском маршруте",
            None,
            NOW,
            "KZ source",
            event_key="переговоры о транскаспийском маршруте",
            entity_ids=frozenset({"entity-route"}),
        ),),
    )
    observed = {}

    class ResolvedPairSession:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "UPDATE story_membership_clock" in sql:
                return FakeResult(row=(5,))
            if "lifecycle = 'resolved'" in sql:
                return FakeResult(rows=[SimpleNamespace(
                    id=10,
                    lifecycle="resolved",
                    last_seen=NOW - timedelta(days=15),
                    meta={"thread_ids": [old.thread_id]},
                )])
            return FakeResult()

    monkeypatch.setattr(stories_module, "fetch_story_candidates", lambda session: [old, new])

    def fake_persist(
        session, items, *, summarizer, now, reactivation_pairs,
        membership_generation,
    ):
        observed["pairs"] = reactivation_pairs
        observed["membership_generation"] = membership_generation
        return 10, 2

    monkeypatch.setattr(stories_module, "persist_story_cluster", fake_persist)
    monkeypatch.setattr(stories_module, "refresh_story_lifecycles", lambda session, now: None)
    monkeypatch.setattr(
        stories_module,
        "_filter_story_cluster_articles",
        lambda cluster: tuple(cluster),
    )

    result = build_stories(ResolvedPairSession(), now=NOW)

    assert result.stories_upserted == 1
    assert observed["pairs"] == frozenset({(1, 2)})
    assert observed["membership_generation"] == 5


def test_reactivation_pairs_evaluate_all_country_partitions_deterministically():
    resolved_thread_id = 2501
    later_thread_id = 3001
    matching_event = "подписано соглашение о зеленом коридоре"
    matching_entities = frozenset({"entity-green-corridor", "entity-ministry"})
    old_first_seen = NOW - timedelta(days=20)
    old_last_seen = NOW - timedelta(days=15)
    es_partition = replace(
        candidate(
            "ES",
            event_key=matching_event,
            title="Испания подписала соглашение о зеленом коридоре",
            entities=matching_entities,
            first_seen=old_first_seen,
            last_seen=old_last_seen,
        ),
        thread_id=resolved_thread_id,
        article_ids=(501,),
    )
    gb_partition = replace(
        candidate(
            "GB",
            event_key="обсуждение налоговой реформы в парламенте",
            title="Парламент обсудил налоговую реформу",
            entities=frozenset({"entity-parliament", "entity-tax"}),
            first_seen=old_first_seen,
            last_seen=old_last_seen,
        ),
        thread_id=resolved_thread_id,
        article_ids=(502,),
    )
    kz_later = replace(
        candidate(
            "KZ",
            event_key=matching_event,
            title="Казахстан присоединился к зеленому коридору",
            entities=matching_entities,
            first_seen=NOW,
            last_seen=NOW,
        ),
        thread_id=later_thread_id,
        article_ids=(503,),
    )

    class ResolvedPartitionSession:
        def execute(self, statement, params=None):
            return FakeResult(rows=[SimpleNamespace(
                id=10,
                lifecycle="resolved",
                last_seen=old_last_seen,
                meta={"thread_ids": [resolved_thread_id]},
            )])

    expected = frozenset({(resolved_thread_id, later_thread_id)})

    assert (
        derive_reactivation_pairs(
            ResolvedPartitionSession(),
            [es_partition, gb_partition, kz_later],
        ),
        derive_reactivation_pairs(
            ResolvedPartitionSession(),
            [gb_partition, es_partition, kz_later],
        ),
    ) == (expected, expected)


def test_background_builder_allocates_one_generation_for_the_whole_transaction(monkeypatch):
    import src.stories as stories_module

    observed = {"allocation_calls": 0, "persist_generations": []}

    class GenerationSession:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "UPDATE story_membership_clock" in sql:
                observed["allocation_calls"] += 1
                return FakeResult(row=(23,))
            return FakeResult(rows=[])

    candidates = [candidate("AZ"), candidate("KZ")]
    monkeypatch.setattr(stories_module, "fetch_story_candidates", lambda session: candidates)
    monkeypatch.setattr(
        stories_module,
        "cluster_story_candidates",
        lambda items, *, reactivation_pairs: [tuple(items), tuple(items)],
    )
    monkeypatch.setattr(stories_module, "derive_reactivation_pairs", lambda session, items: frozenset())

    def fake_persist(session, items, **kwargs):
        observed["persist_generations"].append(kwargs.get("membership_generation"))
        return 7, 2

    monkeypatch.setattr(stories_module, "persist_story_cluster", fake_persist)
    monkeypatch.setattr(stories_module, "refresh_story_lifecycles", lambda session, now: None)
    monkeypatch.setattr(
        stories_module,
        "_filter_story_cluster_articles",
        lambda cluster: tuple(cluster),
    )

    result = build_stories(GenerationSession(), now=NOW)

    assert result.stories_upserted == 2
    assert observed == {
        "allocation_calls": 1,
        "persist_generations": [23, 23],
    }


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

    def fake_persist(
        session, items, *, summarizer, now, reactivation_pairs,
        membership_generation,
    ):
        observed["persist_pairs"] = reactivation_pairs
        observed["membership_generation"] = membership_generation
        return 7, 2

    monkeypatch.setattr(stories_module, "cluster_story_candidates", fake_cluster)
    monkeypatch.setattr(stories_module, "persist_story_cluster", fake_persist)
    monkeypatch.setattr(stories_module, "refresh_story_lifecycles", lambda session, now: None)
    monkeypatch.setattr(
        stories_module,
        "_filter_story_cluster_articles",
        lambda cluster: tuple(cluster),
    )
    monkeypatch.setattr(
        stories_module,
        "allocate_story_membership_generation",
        lambda session: 8,
    )

    result = build_stories(object(), now=NOW, reactivation_pairs=explicit_pairs)

    assert result.stories_upserted == 1
    assert observed == {
        "cluster_pairs": explicit_pairs,
        "persist_pairs": explicit_pairs,
        "membership_generation": 8,
    }


def test_unchanged_copy_preserves_generated_at():
    cluster = [candidate("AZ"), candidate("KZ")]
    session = UnchangedPersistenceSession(cluster)

    persist_story_cluster(session, cluster, now=NOW, membership_generation=1)

    update_call = next(
        call for call in session.calls
        if "UPDATE stories SET" in call[0] and "RETURNING id" in call[0]
    )
    assert "generated_at = :generated_at" in update_call[0]
    assert update_call[1]["generated_at"] == session.generated_at
