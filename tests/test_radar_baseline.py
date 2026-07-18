from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from src.radar import baseline
from src.radar.baseline import calculate_baseline, refine_t0
from src.radar.types import DailyPoint


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


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


def test_baseline_uses_an_exact_rolling_90_times_24_hour_window():
    window_start = NOW - timedelta(days=90)
    points = (
        DailyPoint(at=window_start - timedelta(microseconds=1), volume=100.0),
        DailyPoint(at=window_start, volume=1.0),
        DailyPoint(at=NOW - timedelta(hours=1), volume=2.0),
        DailyPoint(at=NOW, volume=100.0),
    )

    result = calculate_baseline(points, as_of=NOW, coverage=0.9)

    assert tuple(point.at for point in result.baseline_points) == (
        window_start,
        NOW - timedelta(hours=1),
    )
    assert result.dense_points[0].at == window_start
    assert result.dense_points[-1].at == NOW - timedelta(hours=1)


def test_baseline_normalizes_berlin_dst_timestamps_before_window_math():
    berlin = ZoneInfo("Europe/Berlin")
    as_of = datetime(2026, 4, 15, 12, 0, tzinfo=berlin)
    as_of_utc = as_of.astimezone(timezone.utc)
    window_start = as_of_utc - timedelta(days=90)
    points = (
        DailyPoint(at=window_start - timedelta(microseconds=1), volume=100.0),
        DailyPoint(at=window_start, volume=1.0),
        DailyPoint(at=as_of - timedelta(hours=1), volume=2.0),
    )

    result = calculate_baseline(points, as_of=as_of, coverage=0.9)

    assert result.as_of == as_of_utc
    assert result.as_of.tzinfo is timezone.utc
    assert tuple(point.at for point in result.baseline_points) == (
        window_start,
        as_of_utc - timedelta(hours=1),
    )
    assert all(point.at.tzinfo is timezone.utc for point in result.baseline_points)


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


@pytest.mark.parametrize("coverage", (-0.01, 1.01))
def test_baseline_rejects_coverage_outside_the_unit_interval(coverage):
    with pytest.raises(ValueError, match="coverage must be between 0 and 1"):
        calculate_baseline(_daily_points(days=35), as_of=NOW, coverage=coverage)


def test_online_drift_flags_are_accumulated_when_detected_midstream(monkeypatch):
    class TransientDetector:
        def __init__(self):
            self.updates = 0
            self.drift_detected = False

        def update(self, value):
            self.updates += 1
            self.drift_detected = self.updates == 2

    monkeypatch.setattr(baseline, "ADWIN", TransientDetector)
    monkeypatch.setattr(baseline, "PageHinkley", TransientDetector)

    result = calculate_baseline(_daily_points(days=35), as_of=NOW, coverage=0.9)

    assert result.online_candidate_flags == ("adwin", "page_hinkley")


def test_t0_is_first_supported_change_not_detection_time(monkeypatch):
    class DeterministicPelt:
        def __init__(self, *, model, min_size, jump):
            assert (model, min_size, jump) == ("l2", 7, 1)

        def fit(self, signal):
            self.sample_count = len(signal)
            return self

        def predict(self, *, pen):
            assert pen > 0
            return [63, self.sample_count]

    monkeypatch.setattr(baseline, "rpt", SimpleNamespace(Pelt=DeterministicPelt))
    result = refine_t0(_regime_change(day=63), detected_at=NOW)

    expected = NOW - timedelta(days=7)
    assert result.t0_auto == expected
    assert result.t0_auto < result.detected_at
    assert result.status == "refined"


def test_t0_requires_28_valid_daily_points():
    result = refine_t0(_regime_change(day=20)[:27], detected_at=NOW)

    assert result.t0_auto is None
    assert result.status == "insufficient_history"


def test_t0_rejects_history_that_does_not_reach_detection_boundary():
    result = refine_t0(_regime_change(day=63)[:-2], detected_at=NOW)

    assert result.valid_days == 68
    assert result.status == "trailing_gap"
    assert result.t0_auto is None


def test_t0_accepts_history_ending_earlier_on_detection_day(monkeypatch):
    class NoChangePelt:
        def __init__(self, *, model, min_size, jump):
            pass

        def fit(self, signal):
            self.sample_count = len(signal)
            return self

        def predict(self, *, pen):
            return [self.sample_count]

    monkeypatch.setattr(baseline, "rpt", SimpleNamespace(Pelt=NoChangePelt))
    latest = NOW - timedelta(hours=1)
    points = tuple(
        DailyPoint(at=latest - timedelta(days=27 - offset), volume=1.0)
        for offset in range(28)
    )

    result = refine_t0(points, detected_at=NOW)

    assert result.valid_days == 28
    assert result.status == "no_changepoint"
    assert result.t0_auto is None


def test_t0_excludes_a_point_at_the_detection_time():
    points = tuple(
        DailyPoint(at=NOW - timedelta(days=27 - offset), volume=float(offset + 1))
        for offset in range(28)
    )

    result = refine_t0(points, detected_at=NOW)

    assert result.valid_days == 27
    assert result.status == "insufficient_history"
    assert result.t0_auto is None


def test_t0_does_not_collapse_calendar_gaps_into_continuous_history():
    start = NOW - timedelta(days=40)
    points = tuple(
        DailyPoint(at=start + timedelta(days=offset), volume=float(offset + 1))
        for offset in range(40)
        if offset != 20
    )

    result = refine_t0(points, detected_at=NOW)

    assert result.valid_days == 19
    assert result.status == "insufficient_history"
    assert result.t0_auto is None


def test_t0_reports_detector_unavailable_without_heuristic_fallback(monkeypatch):
    monkeypatch.setattr(baseline, "rpt", None)

    result = refine_t0(_regime_change(day=63), detected_at=NOW)

    assert result.valid_days == 70
    assert result.status == "detector_unavailable"
    assert result.t0_auto is None
    assert result.changepoints == ()


def test_temperature_requirements_pin_numpy_compatible_river():
    requirements = (ROOT / "requirements-temperature.txt").read_text().splitlines()

    assert "river==0.23.0" in requirements
    assert "river==0.25.0" not in requirements
