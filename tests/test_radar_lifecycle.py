from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

import pytest

from src.radar.lifecycle import decide_state
from src.radar.types import Contour, TrendMetrics, TrendState, TrendTimeline


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def _metrics(**overrides) -> TrendMetrics:
    values = {
        "contour": Contour.MEDIA,
        "persistent": False,
        "publisher_family_count": 1,
        "coverage": 0.9,
        "signal_strength": 0.8,
        "authoritative": False,
        "authority": None,
        "authoritative_source_count": 0,
        "analysis_action_level": 0,
        "as_of": NOW,
        "quiet_since": None,
        "missing_collection_cycles": 0,
        "timeline": TrendTimeline(),
    }
    values.update(overrides)
    return TrendMetrics(**values)


def test_media_and_action_confirm_independently():
    media = decide_state(
        _metrics(contour="media", persistent=True, publisher_family_count=2),
        "emerging",
    )
    action = decide_state(
        _metrics(contour="action", authoritative=True, authority="registry"),
        "emerging",
    )

    assert media.state == "confirmed"
    assert action.state == "confirmed"


def test_media_requires_persistence_and_two_independent_families():
    decision = decide_state(
        _metrics(persistent=True, publisher_family_count=1), TrendState.EMERGING
    )

    assert decision.state == TrendState.EMERGING
    assert decision.reason == "insufficient_independent_publishers"


def test_critical_coverage_prevents_media_confirmation():
    decision = decide_state(
        _metrics(persistent=True, publisher_family_count=3, coverage=0.42),
        "emerging",
    )

    assert decision.state == "emerging"
    assert decision.reason == "critical_coverage"
    assert decision.confirmation_allowed is False


def test_critical_coverage_prevents_action_confirmation():
    decision = decide_state(
        _metrics(
            contour="action",
            authoritative=True,
            authority="registry",
            coverage=0.42,
        ),
        "emerging",
    )

    assert decision.state == "emerging"
    assert decision.reason == "critical_coverage"
    assert decision.confirmation_allowed is False


def test_analysis_action_level_never_confirms_action():
    decision = decide_state(
        _metrics(contour="action", analysis_action_level=6), "emerging"
    )

    assert decision.state == "emerging"
    assert decision.reason == "insufficient_authoritative_evidence"


def test_two_independent_authoritative_sources_confirm_action():
    decision = decide_state(
        _metrics(contour="action", authoritative_source_count=2), "emerging"
    )

    assert decision.state == "confirmed"
    assert decision.confirmation_allowed is True


def test_confirmation_sets_only_confirmed_at_on_immutable_timeline():
    timeline = TrendTimeline(
        first_observed_at=NOW - timedelta(days=20),
        detected_at=NOW - timedelta(days=10),
        t0_auto=NOW - timedelta(days=15),
        t0_effective=NOW - timedelta(days=14),
    )

    decision = decide_state(
        _metrics(
            contour="action",
            authoritative=True,
            authority="registry",
            timeline=timeline,
        ),
        "emerging",
    )

    assert decision.timeline == TrendTimeline(
        first_observed_at=timeline.first_observed_at,
        detected_at=timeline.detected_at,
        confirmed_at=NOW,
        t0_auto=timeline.t0_auto,
        t0_effective=timeline.t0_effective,
    )
    assert timeline.confirmed_at is None
    with pytest.raises(FrozenInstanceError):
        decision.timeline.confirmed_at = NOW - timedelta(days=1)


def test_reconfirmation_preserves_original_confirmed_at():
    confirmed_at = NOW - timedelta(days=4)
    timeline = TrendTimeline(
        first_observed_at=NOW - timedelta(days=20),
        detected_at=NOW - timedelta(days=10),
        confirmed_at=confirmed_at,
        t0_auto=NOW - timedelta(days=15),
        t0_effective=NOW - timedelta(days=14),
    )

    decision = decide_state(
        _metrics(
            contour="media",
            persistent=True,
            publisher_family_count=2,
            timeline=timeline,
        ),
        "confirmed",
    )

    assert decision.timeline == timeline
    assert decision.timeline.confirmed_at == confirmed_at


def test_single_missing_collection_cycle_cannot_cool_or_resolve_a_trend():
    decision = decide_state(
        _metrics(
            quiet_since=NOW - timedelta(days=90),
            missing_collection_cycles=1,
        ),
        "confirmed",
    )

    assert decision.state == "confirmed"
    assert decision.reason == "collection_gap"


def test_quiet_windows_drive_cooling_then_resolution():
    cooling = decide_state(
        _metrics(quiet_since=NOW - timedelta(days=8)), "confirmed"
    )
    resolved = decide_state(
        _metrics(quiet_since=NOW - timedelta(days=31)), "cooling"
    )

    assert cooling.state == "cooling"
    assert resolved.state == "resolved"
