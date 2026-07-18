from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.radar.actions import _FOSSIL, build_action_observations
from src.radar.media import build_media_observations
from src.radar.repository import observation_input_hash, make_observation, upsert_observations
from src.radar.types import Contour, Observation, ObservationWindow


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
ARTICLE_A, ARTICLE_B, ARTICLE_C = 101, 102, 103


def _window() -> ObservationWindow:
    return ObservationWindow(NOW - timedelta(days=1), NOW)


class _Result:
    def __init__(self, rows=(), rowcount=0, scalar_value=None):
        self._rows = list(rows)
        self.rowcount = rowcount
        self._scalar_value = scalar_value

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._scalar_value


class _Session:
    def __init__(self, media_rows=(), action_rows=(), inserted=1, existing_source_fingerprints=()):
        self.media_rows = media_rows
        self.action_rows = action_rows
        self.inserted = inserted
        self.calls = []
        self.inserted_hashes = set()
        self.existing_source_fingerprints = set(existing_source_fingerprints)

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if "annual_source_fingerprint_gate" in sql:
            return _Result(scalar_value=(params or {})["source_id"] in self.existing_source_fingerprints)
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


class _AnnualWindowSession(_Session):
    """Small executable stand-in for the annual collection-window predicate."""

    def execute(self, statement, params=None):
        sql = str(statement)
        if "FROM un_votes" not in sql:
            return super().execute(statement, params)
        self.calls.append((sql, params or {}))
        assert "updated_at >= :window_start" in sql
        assert "updated_at < :window_end" in sql
        assert "make_date(year, 12, 31)::timestamp AT TIME ZONE 'UTC') >=" not in sql
        rows = self.action_rows["un_votes"]
        return _Result(
            row for row in rows
            if params["window_start"] <= row.updated_at < params["window_end"]
        )


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


def test_annual_actions_expose_period_values_and_real_refresh_time():
    session = _Session(action_rows={
        "un_votes": (SimpleNamespace(
            country_code="ES", year=2025, current_value=Decimal("42.0"),
            previous_value=Decimal("40.0"), updated_at=NOW,
        ),),
    })

    [point] = build_action_observations(session, _window())

    assert point.observed_at == NOW
    assert point.evidence["period_year"] == 2025
    assert point.evidence["temporal_resolution"] == "year"
    assert point.evidence["current_value"] == 42.0
    assert point.evidence["previous_value"] == 40.0
    assert point.evidence["delta"] == 2.0


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


def test_annual_sql_selects_current_refreshes_by_real_updated_time():
    refreshed_at = NOW - timedelta(hours=2)
    session = _AnnualWindowSession(action_rows={
        "un_votes": (
            SimpleNamespace(
                country_code="ES", year=2025, current_value=Decimal("42"),
                previous_value=Decimal("40"), updated_at=refreshed_at,
            ),
            SimpleNamespace(
                country_code="ES", year=2024, current_value=Decimal("42"),
                previous_value=Decimal("40"), updated_at=NOW - timedelta(days=2),
            ),
        ),
    })

    [point] = build_action_observations(session, _window())

    annual_sql, params = next(
        (sql, params) for sql, params in session.calls if "FROM un_votes" in sql
    )
    assert "updated_at >= :window_start" in annual_sql
    assert "updated_at < :window_end" in annual_sql
    assert "make_date(year, 12, 31)::timestamp AT TIME ZONE 'UTC') >=" not in annual_sql
    assert params == {"window_start": _window().start, "window_end": _window().end}
    assert point.observed_at == refreshed_at
    assert point.evidence["updated_at"] == refreshed_at.isoformat()
    assert point.evidence["temporal_resolution"] == "year"
    assert point.evidence["period_year"] == 2025


def test_annual_hash_uses_exact_persisted_observed_at():
    def observation(updated_at):
        session = _Session(action_rows={
            "trade_data": (SimpleNamespace(
                country_code="ES", year=2025, current_value=Decimal("120"),
                previous_value=Decimal("100"), value=Decimal("20"), updated_at=updated_at,
            ),),
        })
        return build_action_observations(session, _window())[0]

    first = observation(NOW - timedelta(hours=2))
    replay = observation(NOW - timedelta(hours=1))

    assert first.observed_at != replay.observed_at
    assert first.input_hash != replay.input_hash
    assert first.input_hash == observation_input_hash(
        contour=first.contour,
        country_code=first.country_code,
        subject_key=first.subject_key,
        direction=first.direction,
        observed_at=first.observed_at,
        metric=first.metric,
        evidence_ids=first.evidence["evidence_ids"],
    )


def test_annual_source_fingerprint_suppresses_unchanged_refresh():
    row = SimpleNamespace(
        country_code="ES", year=2025, current_value=Decimal("120"),
        previous_value=Decimal("100"), value=Decimal("20"), updated_at=NOW - timedelta(hours=2),
    )
    [first] = build_action_observations(_Session(action_rows={"trade_data": (row,)}), _window())
    refreshed = SimpleNamespace(**{**row.__dict__, "updated_at": NOW - timedelta(hours=1)})

    suppressed = build_action_observations(_Session(
        action_rows={"trade_data": (refreshed,)},
        existing_source_fingerprints=(first.evidence["source_id"],),
    ), _window())

    assert suppressed == []


def test_annual_corrected_value_emits_with_real_updated_time_and_new_hash():
    original = SimpleNamespace(
        country_code="ES", year=2025, current_value=Decimal("120"),
        previous_value=Decimal("100"), value=Decimal("20"), updated_at=NOW - timedelta(hours=2),
    )
    [first] = build_action_observations(_Session(action_rows={"trade_data": (original,)}), _window())
    corrected_at = NOW - timedelta(hours=1)
    corrected = SimpleNamespace(**{**original.__dict__, "value": Decimal("21"), "updated_at": corrected_at})

    [point] = build_action_observations(_Session(
        action_rows={"trade_data": (corrected,)},
        existing_source_fingerprints=(first.evidence["source_id"],),
    ), _window())

    assert point.observed_at == corrected_at
    assert point.input_hash != first.input_hash


def test_fossil_prior_query_excludes_reported_or_non_authoritative_rows():
    sql = str(_FOSSIL)

    assert "prior.authority IN ('registry', 'formal')" in sql
    assert "prior.evidence->>'status' = 'verified'" in sql
    assert "event.authority IN ('registry', 'formal')" in sql
    assert "event.status = 'verified'" in sql
    assert "event.details->>'dataset' = 'ru_fossil_imports'" in sql


def test_corrected_sanction_and_trade_magnitudes_change_replay_identity():
    def sanction(value):
        return build_action_observations(_Session(action_rows={
            "sanctions_pressure": (SimpleNamespace(
                country_code="ES", value=Decimal(value), observed_at=NOW - timedelta(hours=2),
            ),),
        }), _window())[0]

    def trade(value):
        return build_action_observations(_Session(action_rows={
            "trade_data": (SimpleNamespace(
                country_code="ES", year=2025, current_value=Decimal("120"),
                previous_value=Decimal("100"), value=Decimal(value), updated_at=NOW - timedelta(hours=2),
            ),),
        }), _window())[0]

    assert sanction("4").input_hash != sanction("5").input_hash
    assert trade("20").input_hash != trade("21").input_hash
