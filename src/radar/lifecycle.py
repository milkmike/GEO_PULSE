"""Explicit, deterministic Radar trend lifecycle gates."""

from __future__ import annotations

from datetime import timedelta

from .types import Contour, CoverageGate, TrendDecision, TrendMetrics, TrendState


MIN_MEDIA_SIGNAL_STRENGTH = 0.5
COOLING_QUIET_WINDOW = timedelta(days=7)
RESOLVED_QUIET_WINDOW = timedelta(days=30)


def _coverage_gate(coverage: float) -> CoverageGate:
    if coverage <= 0.5:
        return CoverageGate.CRITICAL
    if coverage < 0.75:
        return CoverageGate.DEGRADED
    return CoverageGate.HEALTHY


def _decision(
    state: TrendState,
    reason: str,
    metrics: TrendMetrics,
) -> TrendDecision:
    timeline = metrics.timeline
    if state is TrendState.CONFIRMED:
        if metrics.as_of is None:
            raise ValueError("as_of is required for confirmed state")
        timeline = timeline.confirm(metrics.as_of)
    return TrendDecision(
        state=state,
        reason=reason,
        confirmation_allowed=(
            _coverage_gate(metrics.coverage) is not CoverageGate.CRITICAL
        ),
        timeline=timeline,
    )


def _quiet_transition(
    metrics: TrendMetrics, previous_state: TrendState
) -> TrendDecision | None:
    if (
        metrics.missing_collection_cycles
        and previous_state in (TrendState.CONFIRMED, TrendState.COOLING)
    ):
        return _decision(previous_state, "collection_gap", metrics)
    if metrics.quiet_since is None or metrics.as_of is None:
        return None
    quiet_for = metrics.as_of - metrics.quiet_since
    if (
        not metrics.missing_collection_cycles
        and previous_state in (TrendState.CANDIDATE, TrendState.EMERGING)
        and quiet_for >= RESOLVED_QUIET_WINDOW
    ):
        return _decision(
            TrendState.REJECTED,
            "unconfirmed_quiet_window_elapsed",
            metrics,
        )
    if previous_state is TrendState.COOLING and quiet_for >= RESOLVED_QUIET_WINDOW:
        return _decision(TrendState.RESOLVED, "quiet_window_elapsed", metrics)
    if previous_state is TrendState.CONFIRMED and quiet_for >= COOLING_QUIET_WINDOW:
        return _decision(TrendState.COOLING, "quiet_window_elapsed", metrics)
    return None


def decide_state(metrics: TrendMetrics, previous_state: TrendState | str) -> TrendDecision:
    """Apply lifecycle gates without a database read or mutable global state."""

    state = TrendState(previous_state)
    quiet_transition = _quiet_transition(metrics, state)
    if quiet_transition is not None:
        return quiet_transition
    if state in (TrendState.REJECTED, TrendState.RESOLVED):
        return _decision(state, "terminal_state", metrics)
    if _coverage_gate(metrics.coverage) is CoverageGate.CRITICAL:
        return _decision(state, "critical_coverage", metrics)

    if metrics.contour is Contour.MEDIA:
        if metrics.signal_strength < MIN_MEDIA_SIGNAL_STRENGTH:
            return _decision(state, "insufficient_signal_strength", metrics)
        if not metrics.persistent:
            return _decision(TrendState.EMERGING, "insufficient_persistence", metrics)
        if metrics.publisher_family_count < 2:
            return _decision(
                TrendState.EMERGING,
                "insufficient_independent_publishers",
                metrics,
            )
        return _decision(
            TrendState.CONFIRMED,
            "media_confirmation_gates_passed",
            metrics,
        )

    # ``analysis_action_level`` intentionally does not participate here: it is a
    # media classifier and cannot become independent action evidence.
    if metrics.authoritative or metrics.authority in {"registry", "formal"}:
        return _decision(
            TrendState.CONFIRMED,
            "authoritative_action_evidence",
            metrics,
        )
    if metrics.authoritative_source_count >= 2:
        return _decision(
            TrendState.CONFIRMED,
            "independent_authoritative_sources",
            metrics,
        )
    return _decision(
        TrendState.EMERGING,
        "insufficient_authoritative_evidence",
        metrics,
    )
