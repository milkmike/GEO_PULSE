from datetime import datetime, timedelta, timezone

import pytest

from src.radar.baseline import calculate_baseline, refine_t0
from src.radar.types import DailyPoint


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


def _daily_points(days: int) -> tuple[DailyPoint, ...]:
    return tuple(
        DailyPoint(
            at=NOW - timedelta(days=offset),
            volume=4.0,
            attention=0.1,
            semantic_signal=0.8,
            source_independence=0.9,
            persistence=0.9,
        )
        for offset in range(days)
    )


def _shifted_points() -> tuple[DailyPoint, ...]:
    return tuple(
        DailyPoint(
            at=NOW - timedelta(days=offset),
            volume=2.0 if offset > 7 else 15.0,
            attention=0.1 if offset > 7 else 0.8,
            semantic_signal=0.9,
            source_independence=0.8,
            persistence=0.9,
        )
        for offset in range(35)
    )


def _regime_change(day: int) -> tuple[DailyPoint, ...]:
    start = NOW - timedelta(days=70)
    return tuple(
        DailyPoint(
            at=start + timedelta(days=offset),
            volume=1.0 if offset < day else 25.0,
            attention=0.01 if offset < day else 0.8,
        )
        for offset in range(70)
    )


def test_baseline_uses_only_the_90_days_before_as_of():
    result = calculate_baseline(_daily_points(days=110), as_of=NOW, coverage=0.9)

    assert result.window_days == 90
    assert result.acceleration_days == 7
    assert len(result.baseline_points) == 90
    assert all(point.at < NOW for point in result.baseline_points)


def test_baseline_preserves_missing_days_instead_of_treating_them_as_zero():
    points = tuple(
        point
        for point in _daily_points(days=35)
        if point.at.date() != (NOW - timedelta(days=5)).date()
    )

    result = calculate_baseline(points, as_of=NOW, coverage=0.9)

    assert result.missing_days == 57
    assert result.dense_points[-5] is None
    assert result.baseline_volume == pytest.approx(4.0)


def test_critical_coverage_suppresses_confirmation():
    result = calculate_baseline(_shifted_points(), as_of=NOW, coverage=0.42)

    assert result.coverage_gate == "critical"
    assert result.confirmation_allowed is False


def test_confidence_multiplies_its_four_bounded_factors():
    result = calculate_baseline(_daily_points(days=35), as_of=NOW, coverage=0.5)

    assert result.confidence == pytest.approx(0.8 * 0.9 * 0.5 * 0.9)


def test_t0_is_first_supported_change_not_detection_time():
    result = refine_t0(_regime_change(day=63), detected_at=NOW)

    expected = NOW - timedelta(days=7)
    assert result.t0_auto == expected
    assert result.t0_auto < result.detected_at
    assert result.status == "refined"


def test_t0_requires_28_valid_daily_points():
    result = refine_t0(_regime_change(day=20)[:27], detected_at=NOW)

    assert result.t0_auto is None
    assert result.status == "insufficient_history"
