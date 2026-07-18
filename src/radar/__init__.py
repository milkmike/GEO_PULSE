"""Pure, replayable building blocks for the Early Warning Radar."""

from .baseline import calculate_baseline, refine_t0
from .lifecycle import decide_state
from .types import (
    BaselineResult,
    Contour,
    CoverageGate,
    DailyPoint,
    T0Result,
    TrendDecision,
    TrendMetrics,
    TrendState,
    TrendTimeline,
)

__all__ = (
    "BaselineResult",
    "Contour",
    "CoverageGate",
    "DailyPoint",
    "T0Result",
    "TrendDecision",
    "TrendMetrics",
    "TrendState",
    "TrendTimeline",
    "calculate_baseline",
    "decide_state",
    "refine_t0",
)
