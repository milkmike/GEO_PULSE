"""Immutable values shared by deterministic Radar calculations."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Optional


class Contour(str, Enum):
    MEDIA = "media"
    ACTION = "action"


class TrendState(str, Enum):
    CANDIDATE = "candidate"
    EMERGING = "emerging"
    CONFIRMED = "confirmed"
    COOLING = "cooling"
    RESOLVED = "resolved"
    REJECTED = "rejected"


class CoverageGate(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"


def _require_aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _bounded(value: float, field: str) -> None:
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{field} must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class TrendTimeline:
    """Independent lifecycle timestamps retained across every decision."""

    first_observed_at: Optional[datetime] = None
    detected_at: Optional[datetime] = None
    confirmed_at: Optional[datetime] = None
    t0_auto: Optional[datetime] = None
    t0_effective: Optional[datetime] = None

    def __post_init__(self) -> None:
        for field_name in (
            "first_observed_at",
            "detected_at",
            "confirmed_at",
            "t0_auto",
            "t0_effective",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _require_aware(value, field_name)

    def confirm(self, at: datetime) -> "TrendTimeline":
        """Return a timeline with first confirmation recorded exactly once."""

        _require_aware(at, "confirmed_at")
        if self.confirmed_at is not None:
            return self
        return replace(self, confirmed_at=at)


@dataclass(frozen=True, slots=True)
class DailyPoint:
    """One observed daily metric bucket; ``None`` is missing, never zero."""

    at: datetime
    volume: Optional[float] = None
    attention: Optional[float] = None
    semantic_signal: Optional[float] = None
    source_independence: Optional[float] = None
    persistence: Optional[float] = None

    def __post_init__(self) -> None:
        _require_aware(self.at, "at")
        for field in ("volume", "attention"):
            value = getattr(self, field)
            if value is not None and value < 0:
                raise ValueError(f"{field} must be non-negative")
        for field in ("semantic_signal", "source_independence", "persistence"):
            value = getattr(self, field)
            if value is not None:
                _bounded(value, field)


@dataclass(frozen=True, slots=True)
class BaselineResult:
    window_days: int
    acceleration_days: int
    as_of: datetime
    baseline_points: tuple[DailyPoint, ...]
    dense_points: tuple[Optional[DailyPoint], ...]
    missing_days: int
    valid_days: int
    baseline_volume: Optional[float]
    baseline_attention: Optional[float]
    acceleration_volume: Optional[float]
    acceleration_attention: Optional[float]
    coverage_confidence: float
    coverage_gate: CoverageGate
    confirmation_allowed: bool
    confidence: float
    online_candidate_flags: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_aware(self.as_of, "as_of")
        _bounded(self.coverage_confidence, "coverage_confidence")
        _bounded(self.confidence, "confidence")


@dataclass(frozen=True, slots=True)
class T0Result:
    detected_at: datetime
    t0_auto: Optional[datetime]
    status: str
    valid_days: int
    changepoints: tuple[datetime, ...] = ()

    def __post_init__(self) -> None:
        _require_aware(self.detected_at, "detected_at")
        if self.t0_auto is not None:
            _require_aware(self.t0_auto, "t0_auto")


@dataclass(frozen=True, slots=True)
class TrendMetrics:
    contour: Contour
    persistent: bool = False
    publisher_family_count: int = 0
    coverage: float = 1.0
    signal_strength: float = 0.0
    authoritative: bool = False
    authority: Optional[str] = None
    authoritative_source_count: int = 0
    analysis_action_level: int = 0
    as_of: Optional[datetime] = None
    quiet_since: Optional[datetime] = None
    missing_collection_cycles: int = 0
    timeline: TrendTimeline = field(default_factory=TrendTimeline)

    def __post_init__(self) -> None:
        object.__setattr__(self, "contour", Contour(self.contour))
        _bounded(self.coverage, "coverage")
        _bounded(self.signal_strength, "signal_strength")
        if self.publisher_family_count < 0 or self.authoritative_source_count < 0:
            raise ValueError("source counts must be non-negative")
        if self.analysis_action_level < 0:
            raise ValueError("analysis_action_level must be non-negative")
        if self.missing_collection_cycles < 0:
            raise ValueError("missing_collection_cycles must be non-negative")
        if self.as_of is not None:
            _require_aware(self.as_of, "as_of")
        if self.quiet_since is not None:
            _require_aware(self.quiet_since, "quiet_since")


@dataclass(frozen=True, slots=True)
class TrendDecision:
    state: TrendState
    reason: str
    confirmation_allowed: bool
    timeline: TrendTimeline = field(default_factory=TrendTimeline)

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", TrendState(self.state))
