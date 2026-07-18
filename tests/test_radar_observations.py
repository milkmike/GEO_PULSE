from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.radar.actions import build_action_observations
from src.radar.media import build_media_observations
from src.radar.repository import upsert_observations
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
        if "sanctions_pressure" in sql:
            return _Result(self.action_rows)
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
