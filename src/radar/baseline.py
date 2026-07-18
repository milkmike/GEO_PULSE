"""Time-correct, database-free Radar baseline and changepoint calculations."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from math import log1p
from statistics import fmean
from typing import Iterable, Optional, Sequence

import numpy as np

from .types import BaselineResult, CoverageGate, DailyPoint, T0Result

try:  # Keep read-only consumers usable until the optional detector extra is installed.
    from river.drift import ADWIN, PageHinkley
except ImportError:  # pragma: no cover - exercised in minimal runtime images
    ADWIN = PageHinkley = None

try:  # The deterministic fallback keeps replay available in minimal runtime images.
    import ruptures as rpt
except ImportError:  # pragma: no cover - exercised in minimal runtime images
    rpt = None


WINDOW_DAYS = 90
ACCELERATION_DAYS = 7
MIN_T0_VALID_DAYS = 28
MIN_SEGMENT_DAYS = 7


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _utc_day(value: datetime) -> date:
    return value.astimezone(timezone.utc).date()


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    known = [value for value in values if value is not None]
    return fmean(known) if known else None


def _coverage_gate(coverage: float) -> CoverageGate:
    if coverage <= 0.5:
        return CoverageGate.CRITICAL
    if coverage < 0.75:
        return CoverageGate.DEGRADED
    return CoverageGate.HEALTHY


def _combine_points(points: Sequence[DailyPoint]) -> DailyPoint:
    """Aggregate same-day observations without turning absent metrics into zero."""

    def mean_attr(name: str) -> Optional[float]:
        return _mean(getattr(point, name) for point in points)

    volume_values = [point.volume for point in points if point.volume is not None]
    return DailyPoint(
        at=min(point.at for point in points),
        volume=sum(volume_values) if volume_values else None,
        attention=mean_attr("attention"),
        semantic_signal=mean_attr("semantic_signal"),
        source_independence=mean_attr("source_independence"),
        persistence=mean_attr("persistence"),
    )


def _online_flags(points: Sequence[DailyPoint]) -> tuple[str, ...]:
    values = [_metric_value(point) for point in points]
    values = [value for value in values if value is not None]
    if not values:
        return ()

    flags: list[str] = []
    if ADWIN is not None and PageHinkley is not None:
        adwin = ADWIN()
        page_hinkley = PageHinkley()
        for value in values:
            adwin.update(value)
            page_hinkley.update(value)
            if getattr(adwin, "drift_detected", False) and "adwin" not in flags:
                flags.append("adwin")
            if (
                getattr(page_hinkley, "drift_detected", False)
                and "page_hinkley" not in flags
            ):
                flags.append("page_hinkley")
        return tuple(flags)

    # A minimal deterministic fallback is intentionally only a candidate flag.
    if len(values) >= ACCELERATION_DAYS * 2:
        recent = fmean(values[-ACCELERATION_DAYS:])
        prior = fmean(values[-2 * ACCELERATION_DAYS:-ACCELERATION_DAYS])
        if recent > prior:
            flags.append("acceleration_fallback")
    return tuple(flags)


def calculate_baseline(
    points: Iterable[DailyPoint], as_of: datetime, coverage: float
) -> BaselineResult:
    """Calculate only from evidence available strictly before ``as_of``.

    The returned dense series deliberately represents unavailable collection days
    as ``None``.  Missing collection cannot manufacture an apparent quiet period.
    """

    _require_aware(as_of, "as_of")
    if not 0.0 <= coverage <= 1.0:
        raise ValueError("coverage must be between 0 and 1")
    window_start = as_of - timedelta(days=WINDOW_DAYS)
    buckets: list[list[DailyPoint]] = [[] for _ in range(WINDOW_DAYS)]
    for point in points:
        if not window_start <= point.at < as_of:
            continue
        elapsed = point.at - window_start
        bucket_index = elapsed // timedelta(days=1)
        buckets[bucket_index].append(point)

    dense: list[Optional[DailyPoint]] = []
    for bucket_points in buckets:
        dense.append(
            _combine_points(bucket_points) if bucket_points else None
        )

    baseline_points = tuple(point for point in dense if point is not None)
    acceleration_points = dense[-ACCELERATION_DAYS:]
    semantic_signal = _mean(point.semantic_signal for point in baseline_points) or 0.0
    source_independence = (
        _mean(point.source_independence for point in baseline_points) or 0.0
    )
    persistence = _mean(point.persistence for point in baseline_points) or 0.0
    gate = _coverage_gate(coverage)

    return BaselineResult(
        window_days=WINDOW_DAYS,
        acceleration_days=ACCELERATION_DAYS,
        as_of=as_of,
        baseline_points=baseline_points,
        dense_points=tuple(dense),
        missing_days=sum(point is None for point in dense),
        valid_days=len(baseline_points),
        baseline_volume=_mean(point.volume for point in baseline_points),
        baseline_attention=_mean(point.attention for point in baseline_points),
        acceleration_volume=_mean(
            point.volume for point in acceleration_points if point is not None
        ),
        acceleration_attention=_mean(
            point.attention for point in acceleration_points if point is not None
        ),
        coverage_confidence=coverage,
        coverage_gate=gate,
        confirmation_allowed=gate is not CoverageGate.CRITICAL,
        confidence=clamp01(
            semantic_signal * source_independence * coverage * persistence
        ),
        online_candidate_flags=_online_flags(baseline_points),
    )


def _metric_value(point: DailyPoint) -> Optional[float]:
    parts = [
        log1p(value)
        for value in (point.volume, point.attention)
        if value is not None
    ]
    return fmean(parts) if parts else None


def _fallback_changepoints(values: Sequence[float]) -> list[int]:
    """Find the strongest supported split when ruptures is unavailable."""

    candidates: list[tuple[float, int]] = []
    for split in range(MIN_SEGMENT_DAYS, len(values) - MIN_SEGMENT_DAYS + 1):
        before = fmean(values[split - MIN_SEGMENT_DAYS:split])
        after = fmean(values[split:split + MIN_SEGMENT_DAYS])
        candidates.append((abs(after - before), split))
    if not candidates:
        return []
    strength, split = max(candidates)
    return [split] if strength > 0.0 else []


def _pelt_changepoints(values: Sequence[float]) -> list[int]:
    if rpt is None:
        return _fallback_changepoints(values)
    # BIC-style penalty keeps a flat metric flat while retaining sustained shifts.
    variance = fmean((value - fmean(values)) ** 2 for value in values)
    penalty = max(1.0, log1p(len(values)) * variance)
    breakpoints = rpt.Pelt(
        model="l2", min_size=MIN_SEGMENT_DAYS, jump=1
    ).fit(
        np.asarray(values, dtype=float)
    ).predict(pen=penalty)
    return [point for point in breakpoints if point < len(values)]


def refine_t0(points: Iterable[DailyPoint], detected_at: datetime) -> T0Result:
    """Refine automatic T0 from the earliest changepoint before detection."""

    _require_aware(detected_at, "detected_at")
    daily: dict[date, list[DailyPoint]] = defaultdict(list)
    for point in points:
        if point.at < detected_at:
            daily[_utc_day(point.at)].append(point)
    eligible: list[tuple[date, DailyPoint]] = []
    for day in sorted(daily):
        combined = _combine_points(daily[day])
        if _metric_value(combined) is not None:
            eligible.append((day, combined))
    contiguous_runs: list[list[DailyPoint]] = []
    previous_day: Optional[date] = None
    for day, point in eligible:
        if previous_day is None or day != previous_day + timedelta(days=1):
            contiguous_runs.append([])
        contiguous_runs[-1].append(point)
        previous_day = day
    valid = contiguous_runs[-1] if contiguous_runs else []
    if len(valid) < MIN_T0_VALID_DAYS:
        return T0Result(
            detected_at=detected_at,
            t0_auto=None,
            status="insufficient_history",
            valid_days=len(valid),
        )

    values = [_metric_value(point) for point in valid]
    splits = _pelt_changepoints(values)  # values are guaranteed non-null above.
    if not splits:
        return T0Result(
            detected_at=detected_at,
            t0_auto=None,
            status="no_changepoint",
            valid_days=len(valid),
        )

    changepoints = tuple(valid[split].at for split in splits if split < len(valid))
    if not changepoints:
        return T0Result(
            detected_at=detected_at,
            t0_auto=None,
            status="no_changepoint",
            valid_days=len(valid),
        )
    return T0Result(
        detected_at=detected_at,
        t0_auto=changepoints[0],
        status="refined",
        valid_days=len(valid),
        changepoints=changepoints,
    )
