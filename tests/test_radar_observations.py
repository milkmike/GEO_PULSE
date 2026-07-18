from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.radar.actions import build_action_observations
from src.radar.media import build_media_observations
from src.radar.repository import make_observation, upsert_observations
from src.radar.types import Contour, Observation, ObservationWindow


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
ARTICLE_A, ARTICLE_B, ARTICLE_C = 101, 102, 103


def _window() -> ObservationWindow:
    return ObservationWindow(NOW - timedelta(days=1), NOW)


class _Result:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows


class _Session:
    def __init__(self, media_rows=(), action_rows=(), inserted=1):
        self.media_rows = media_rows
        self.action_rows = action_rows
        self.inserted = inserted
        self.calls = []
        self.inserted_hashes = set()

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if "radar_observations" in sql and "INSERT" in sql:
            input_hash = (params or {})["input_hash"]
            if input_hash in self.inserted_hashes:
                return _Result(rowcount=0)
            self.inserted_hashes.add(input_hash)
            return _Result(rowcount=self.inserted)
        if "FROM articles" in sql:
            return _Result(self.media_rows)
        for dataset in ("sanctions_pressure", "un_votes", "trade_data", "ru_fossil_imports"):
            if dataset in sql:
                rows = (
                    self.action_rows.get(dataset, ())
                    if isinstance(self.action_rows, dict)
                    else self.action_rows if dataset == "sanctions_pressure" else ()
                )
                return _Result(rows)
        return _Result()


def _media_row(article_id, publisher_id, publisher_domain, **overrides):
    values = {
        "article_id": article_id,
        "published_at": NOW - timedelta(hours=2),
        "country_code": "ES",
        "publisher_id": publisher_id,
        "publisher_name": f"Publisher {publisher_id}",
        "publisher_url": f"https://{publisher_domain}",
        "publisher_config": {"publisher_domain": publisher_domain},
        "story_id": 77,
        "event_key": "policy shift",
        "sentiment": -0.5,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _only(observations, **attributes):
    matched = [
        point for point in observations
        if all(getattr(point, key) == value for key, value in attributes.items())
    ]
    assert len(matched) == 1
    return matched[0]


def test_observation_types_are_immutable_and_validate_time_boundaries():
    with pytest.raises(ValueError, match="start must not be after end"):
        ObservationWindow(NOW, NOW - timedelta(seconds=1))

    window = _window()
    point = Observation(
        public_id=UUID("00000000-0000-0000-0000-000000000001"),
        input_hash="a" * 64,
        country_code="ES",
        contour=Contour.MEDIA,
        subject_key="story:77",
        direction="negative",
        metric="attention_share",
        observed_at=NOW,
        window=window,
        value=0.5,
        baseline={},
        evidence={},
    )

    assert point.window == window
    assert point.contour is Contour.MEDIA


def test_media_observation_counts_publisher_families_not_feed_rows():
    session = _Session(media_rows=(
        _media_row(ARTICLE_A, 10, "example.es"),
        _media_row(ARTICLE_B, 11, "example.es"),
        _media_row(ARTICLE_C, 12, "independent.es"),
        _media_row(104, 13, "duplicate.es", is_duplicate=True),
        _media_row(105, 14, "future.es", published_at=NOW + timedelta(seconds=1)),
    ))

    observations = build_media_observations(session, _window())

    point = _only(observations, metric="attention_share")
    assert point.publisher_family_count == 2
    assert point.article_ids == (ARTICLE_A, ARTICLE_B, ARTICLE_C)
    assert point.value == pytest.approx(1.0)


def test_media_action_level_never_confirms_action_observation():
    session = _Session(media_rows=(
        _media_row(ARTICLE_A, 10, "example.es", action_level=6),
    ))

    assert build_action_observations(session, _window()) == []


def test_sanction_delta_creates_authoritative_action_observation():
    session = _Session(action_rows=(SimpleNamespace(
        country_code="ES", value=4, observed_at=NOW - timedelta(hours=1),
        source_id="sanctions_pressure:ES:2026-07-18T11:00:00+00:00",
    ),))

    [point] = build_action_observations(session, _window())

    assert point.contour == Contour.ACTION
    assert point.authority == "registry"
    assert point.subject_key == "policy:sanctions:russia"


def test_observation_upsert_uses_input_hash_conflict_and_returns_inserted_count():
    observation = Observation(
        public_id=UUID("00000000-0000-0000-0000-000000000002"),
        input_hash="b" * 64,
        country_code="ES",
        contour=Contour.ACTION,
        subject_key="policy:sanctions:russia",
        direction="increase",
        metric="delta",
        observed_at=NOW,
        window=_window(),
        value=4.0,
        authority="registry",
        baseline={},
        evidence={"evidence_ids": ("sanctions_pressure:ES",)},
    )
    session = _Session(inserted=1)

    assert upsert_observations(session, [observation, observation]) == 1
    sql, params = session.calls[0]
    assert "ON CONFLICT (input_hash) DO NOTHING" in sql
    assert params["input_hash"] == "b" * 64


def test_overlapping_publisher_aliases_merge_transitively():
    session = _Session(media_rows=(
        _media_row(ARTICLE_A, 10, "a.es", publisher_config={
            "publisher_domain": "a.es", "publisher_domain_aliases": ["b.es"],
        }),
        _media_row(ARTICLE_B, 11, "b.es", publisher_config={
            "publisher_domain": "b.es", "publisher_domain_aliases": ["c.es"],
        }),
        _media_row(ARTICLE_C, 12, "c.es"),
        _media_row(106, 13, "independent.es"),
    ))

    point = _only(build_media_observations(session, _window()), metric="attention_share")

    assert point.publisher_family_count == 2


def test_multiple_story_events_produce_sorted_stable_subject_observations():
    session = _Session(media_rows=(
        _media_row(ARTICLE_A, 10, "example.es", story_event_keys=("zeta", "alpha", "zeta")),
    ))

    points = build_media_observations(session, _window())

    assert [point.subject_key for point in points] == ["event:alpha", "event:zeta"]


def test_attention_denominator_includes_verified_irrelevant_national_coverage():
    session = _Session(media_rows=(
        _media_row(ARTICLE_A, 10, "example.es"),
        _media_row(ARTICLE_B, 11, "background.es", is_relevant=False),
    ))

    point = _only(build_media_observations(session, _window(),), metric="attention_share")

    assert point.article_ids == (ARTICLE_A,)
    assert point.baseline["national_indexed_article_count"] == 2
    assert point.value == pytest.approx(0.5)


def test_annual_actions_use_period_and_values_not_mutable_refresh_time():
    session = _Session(action_rows={
        "un_votes": (SimpleNamespace(
            country_code="ES", year=2025, current_value=Decimal("42.0"),
            previous_value=Decimal("40.0"), updated_at=NOW,
        ),),
    })

    [point] = build_action_observations(session, _window())

    assert point.observed_at == datetime(2025, 12, 31, tzinfo=timezone.utc)
    assert point.evidence["current_value"] == 42.0
    assert point.evidence["previous_value"] == 40.0
    assert point.evidence["delta"] == 2.0
    replay = _Session(action_rows={
        "un_votes": (SimpleNamespace(
            country_code="ES", year=2025, current_value=Decimal("42.00"),
            previous_value=Decimal("40.00"), updated_at=NOW + timedelta(days=10),
        ),),
    })
    [replayed] = build_action_observations(replay, _window())
    assert replayed.input_hash == point.input_hash


def test_fossil_snapshot_requires_comparable_prior_and_canonicalizes_numbers():
    initial = _Session(action_rows={
        "ru_fossil_imports": (SimpleNamespace(
            country_code="ES", current_value=Decimal("100.00"), previous_value=None,
            observed_at=NOW - timedelta(hours=2),
        ),),
    })
    unchanged = _Session(action_rows={
        "ru_fossil_imports": (SimpleNamespace(
            country_code="ES", current_value=Decimal("100.0"), previous_value=Decimal("100.00"),
            observed_at=NOW - timedelta(hours=2),
        ),),
    })

    assert build_action_observations(initial, _window()) == []
    assert build_action_observations(unchanged, _window()) == []


def test_fossil_decrease_uses_delta_and_retains_comparison_values():
    session = _Session(action_rows={
        "ru_fossil_imports": (SimpleNamespace(
            country_code="ES", current_value=Decimal("90.0"), previous_value=Decimal("100.00"),
            observed_at=NOW - timedelta(hours=2),
        ),),
    })

    [point] = build_action_observations(session, _window())

    assert point.direction == "decrease"
    assert point.value == -10.0
    assert point.evidence["previous_value"] == 100.0
    assert point.evidence["current_value"] == 90.0
    assert point.evidence["delta"] == -10.0


def test_input_hash_includes_all_evidence_roots():
    common = dict(
        country_code="ES", contour="media", subject_key="event:policy",
        direction="negative", metric="attention_share", observed_at=NOW,
        evidence_ids=("article:1",),
    )
    baseline = make_observation(**common, story_id=7, evidence={"entity_ids": ("entity-a",)})
    changed_story = make_observation(**common, story_id=8, evidence={"entity_ids": ("entity-a",)})
    changed_entity = make_observation(**common, story_id=7, evidence={"entity_ids": ("entity-b",)})
    changed_action = make_observation(**common, story_id=7, evidence={
        "entity_ids": ("entity-a",), "action_event_ids": (99,),
    })

    assert len({baseline.input_hash, changed_story.input_hash, changed_entity.input_hash, changed_action.input_hash}) == 4
