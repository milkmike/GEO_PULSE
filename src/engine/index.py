"""Temperature index calculation."""
import logging
import math
import statistics
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable, Sequence

from sqlalchemy import text

from src.config import COUNTRY_NAMES
from src.db import get_session, Temperature, Alert
from src.methodology import TEMPERATURE_METHODOLOGY

logger = logging.getLogger(__name__)

WINDOW_DAYS = TEMPERATURE_METHODOLOGY.window_days
TAU = TEMPERATURE_METHODOLOGY.time_decay_tau_seconds
EVENT_TYPE_WEIGHTS = TEMPERATURE_METHODOLOGY.event_type_weights
ACTION_MULTIPLIERS = TEMPERATURE_METHODOLOGY.action_level_weights
_TEMPERATURE_ALERTS_ENABLED: ContextVar[bool] = ContextVar(
    "temperature_alerts_enabled",
    default=True,
)
TemperatureHistoryProvider = Callable[[str, datetime, int], Sequence[float]]
_TEMPERATURE_HISTORY_PROVIDER: ContextVar[TemperatureHistoryProvider | None] = (
    ContextVar("temperature_history_provider", default=None)
)


@contextmanager
def suppress_temperature_alerts():
    """Keep historical recomputation read-only outside temperature upserts."""

    token = _TEMPERATURE_ALERTS_ENABLED.set(False)
    try:
        yield
    finally:
        _TEMPERATURE_ALERTS_ENABLED.reset(token)


@contextmanager
def use_temperature_history(provider: TemperatureHistoryProvider):
    """Use caller-owned history for deterministic chronological recomputation."""

    token = _TEMPERATURE_HISTORY_PROVIDER.set(provider)
    try:
        yield
    finally:
        _TEMPERATURE_HISTORY_PROVIDER.reset(token)


def _temperature_history(
    session,
    country_code: str,
    *,
    as_of: datetime,
    limit: int,
) -> list[float]:
    provider = _TEMPERATURE_HISTORY_PROVIDER.get()
    if provider is not None:
        return [float(value) for value in provider(country_code, as_of, limit)]

    rows = session.execute(
        text(f"""
            SELECT temperature FROM temperature
            WHERE country_code = :cc
              AND time < :as_of
            ORDER BY time DESC LIMIT {limit}
        """),
        {"cc": country_code, "as_of": as_of},
    ).fetchall()
    return [float(row.temperature) for row in rows]


def calculate_temperature(country_code: str) -> dict | None:
    """Calculate current temperature for a country based on analyzed articles."""
    return calculate_temperature_at(
        country_code,
        datetime.now(timezone.utc),
        exclude_backfill=True,
    )


def calculate_temperature_at(
    country_code: str,
    as_of: datetime,
    *,
    exclude_backfill: bool = True,
) -> dict | None:
    """Calculate Thermometer v1 at one bounded historical instant."""

    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    else:
        as_of = as_of.astimezone(timezone.utc)
    window_start = as_of - timedelta(days=WINDOW_DAYS)
    backfill_clause = "AND ar.is_backfill = false" if exclude_backfill else ""

    with get_session() as session:
        rows = session.execute(
            text(f"""
                SELECT a.id AS analysis_id, ar.id AS article_id,
                       a.sentiment, a.event_type, a.sentiment_confidence,
                       a.action_level, a.event_key,
                       ar.published_at, s.weight, s.id as source_id,
                       COALESCE(ar.reprint_count, 0) as reprint_count
                FROM analysis a
                JOIN articles ar ON a.article_id = ar.id
                JOIN article_country_facts s ON s.article_id = ar.id
                WHERE s.country_code = :cc
                  AND a.is_relevant = true
                  AND a.sentiment IS NOT NULL
                  {backfill_clause}
                  AND ar.published_at > :window_start
                  AND ar.published_at <= :as_of
                ORDER BY ar.published_at, ar.id, a.id
            """),
            {
                "cc": country_code,
                "window_start": window_start,
                "as_of": as_of,
            },
        ).fetchall()

        if not rows:
            return None
        history = _temperature_history(
            session,
            country_code,
            as_of=as_of,
            limit=max(
                TEMPERATURE_METHODOLOGY.trend_history_points,
                TEMPERATURE_METHODOLOGY.anomaly_history_points,
            ),
        )
        calculation = calculate_temperature_from_rows(
            country_code,
            as_of,
            rows,
            history=history,
        )
        if calculation is None:
            return None
        _add_anomaly_alert(
            session,
            country_code,
            calculation["temperature"],
            _anomaly_statistics(
                calculation["temperature"],
                history[:TEMPERATURE_METHODOLOGY.anomaly_history_points],
            ),
        )
        return calculation


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _article_order(row: object) -> tuple[datetime, str, str, str]:
    """Return a stable total order for equal-weight event-cluster rows."""

    return (
        _as_utc(row.published_at),
        str(getattr(row, "article_id", "")),
        str(getattr(row, "analysis_id", "")),
        str(getattr(row, "source_id", "")),
    )


def calculate_temperature_from_rows(
    country_code: str,
    as_of: datetime,
    rows: Sequence[object],
    *,
    history: Sequence[float] = (),
) -> dict | None:
    """Calculate Thermometer v1 from bounded rows and caller-owned history."""

    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    else:
        as_of = as_of.astimezone(timezone.utc)

    if not rows:
        return None

    numerator = 0.0
    denominator = 0.0
    type_sums = {
        event_type: 0.0
        for event_type in TEMPERATURE_METHODOLOGY.component_event_types
    }
    type_counts = {t: 0 for t in type_sums}
    source_ids = set()

    # Phase 1: Group articles by event_key to prevent one story from dominating
    event_clusters = {}  # event_key -> list of rows
    unclustered = []     # articles without event_key
        
    for row in rows:
        ek = getattr(row, 'event_key', None)
        if ek and len(str(ek)) >= TEMPERATURE_METHODOLOGY.event_key_min_length:
            event_clusters.setdefault(str(ek).lower().strip(), []).append(row)
        else:
            unclustered.append(row)
        
    # Phase 2: For each cluster, sort by source weight (best source first)
    # Apply diminishing returns: 1st article full weight, each next 20%
    clustered_rows = []
    for cluster in event_clusters.values():
        cluster.sort(
            key=lambda row: (
                -float(
                    row.weight or TEMPERATURE_METHODOLOGY.source_weight_default
                ),
                _article_order(row),
            ),
        )
        for index, row in enumerate(cluster):
            clustered_rows.append((
                row,
                TEMPERATURE_METHODOLOGY.cluster_diminishing_base ** index,
            ))
        
    # Unclustered articles get full weight
    for row in unclustered:
        clustered_rows.append((row, TEMPERATURE_METHODOLOGY.unclustered_weight))
        
    for row, cluster_decay in clustered_rows:
        sentiment = float(row.sentiment)
        published_at = row.published_at
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
            
        age = (as_of - published_at).total_seconds()
        decay = math.exp(-age / TAU)
            
        w_source = float(
            row.weight or TEMPERATURE_METHODOLOGY.source_weight_default
        )
        event_type = row.event_type
        w_type = EVENT_TYPE_WEIGHTS.get(event_type, EVENT_TYPE_WEIGHTS[None])
            
        importance = (
            TEMPERATURE_METHODOLOGY.reprint_importance_base
            + math.log1p(row.reprint_count)
        )
            
        # Action level multiplier
        action_mult = ACTION_MULTIPLIERS.get(
            row.action_level or 1,
            ACTION_MULTIPLIERS[1],
        )
            
        # cluster_decay: 1.0 for first/unique article, 0.2^n for same-event dupes
        weight = w_source * w_type * decay * importance * action_mult * cluster_decay
        numerator += sentiment * weight
        denominator += abs(weight)
            
        if event_type in type_sums:
            type_sums[event_type] += sentiment * cluster_decay
            type_counts[event_type] += cluster_decay
            
        source_ids.add(row.source_id)

    if denominator == 0:
        return None

    raw_sentiment = numerator / denominator
    temperature = round(
        raw_sentiment * TEMPERATURE_METHODOLOGY.normalization_factor,
        TEMPERATURE_METHODOLOGY.temperature_round_digits,
    )

    # Component averages
    components = {}
    for event_type in type_sums:
        if type_counts[event_type] > 0:
            components[event_type] = round(
                type_sums[event_type] / type_counts[event_type],
                TEMPERATURE_METHODOLOGY.component_round_digits,
            )
        else:
            components[event_type] = None

    bounded_history = [float(value) for value in history]
    trend = _trend_from_history(
        temperature,
        bounded_history[:TEMPERATURE_METHODOLOGY.trend_history_points],
    )
    anomaly_statistics = _anomaly_statistics(
        temperature,
        bounded_history[:TEMPERATURE_METHODOLOGY.anomaly_history_points],
    )
    anomaly_score = anomaly_statistics[0] if anomaly_statistics is not None else None

    return {
        "time": as_of,
        "country_code": country_code,
        "temperature": temperature,
        "raw_sentiment": round(
            raw_sentiment,
            TEMPERATURE_METHODOLOGY.raw_sentiment_round_digits,
        ),
        "diplomatic": components.get("diplomatic"),
        "military": components.get("military"),
        "economic": components.get("economic"),
        "cultural": components.get("cultural"),
        "security": components.get("security"),
        "article_count": len(rows),
        "source_count": len(source_ids),
        "trend": trend,
        "anomaly_score": anomaly_score,
    }


def _trend_from_history(current: float, history: Sequence[float]) -> str:
    if len(history) < TEMPERATURE_METHODOLOGY.trend_minimum_samples:
        return "stable"

    diff = current - statistics.mean(history)
    if diff > TEMPERATURE_METHODOLOGY.trend_threshold:
        return "rising"
    if diff < -TEMPERATURE_METHODOLOGY.trend_threshold:
        return "falling"
    return "stable"


def _anomaly_statistics(
    current: float,
    history: Sequence[float],
) -> tuple[float, float, float] | None:
    if len(history) < TEMPERATURE_METHODOLOGY.anomaly_minimum_samples:
        return None

    mean = statistics.mean(history)
    std = (
        statistics.stdev(history)
        if len(history) > 1
        else TEMPERATURE_METHODOLOGY.anomaly_zero_std_fallback
    )
    if std == 0:
        std = TEMPERATURE_METHODOLOGY.anomaly_zero_std_fallback
    z_score = round(
        (current - mean) / std,
        TEMPERATURE_METHODOLOGY.anomaly_round_digits,
    )
    return z_score, mean, std


def _add_anomaly_alert(
    session,
    country_code: str,
    current: float,
    anomaly_statistics: tuple[float, float, float] | None,
) -> None:
    if anomaly_statistics is None or not _TEMPERATURE_ALERTS_ENABLED.get():
        return

    z_score, mean, std = anomaly_statistics
    if abs(z_score) <= TEMPERATURE_METHODOLOGY.anomaly_warning_threshold:
        return
    severity = (
        "critical"
        if abs(z_score) > TEMPERATURE_METHODOLOGY.anomaly_critical_threshold
        else "warning"
    )
    session.add(Alert(
        country_code=country_code,
        alert_type="anomaly",
        severity=severity,
        title=f"Anomaly detected for {COUNTRY_NAMES.get(country_code, country_code)}",
        description=f"Z-score: {z_score}, current temp: {current}",
        data={"z_score": z_score, "temperature": current, "mean": mean, "std": std},
    ))


def detect_trend(
    session,
    country_code: str,
    current: float,
    *,
    as_of: datetime,
) -> str:
    """Simple trend detection based on last 3 readings."""
    prev_temps = _temperature_history(
        session,
        country_code,
        as_of=as_of,
        limit=TEMPERATURE_METHODOLOGY.trend_history_points,
    )

    return _trend_from_history(current, prev_temps)


def detect_anomaly(
    session,
    country_code: str,
    current: float,
    *,
    as_of: datetime,
) -> float | None:
    """Z-score based anomaly detection."""
    temps = _temperature_history(
        session,
        country_code,
        as_of=as_of,
        limit=TEMPERATURE_METHODOLOGY.anomaly_history_points,
    )

    anomaly_statistics = _anomaly_statistics(current, temps)
    _add_anomaly_alert(session, country_code, current, anomaly_statistics)
    return anomaly_statistics[0] if anomaly_statistics is not None else None


def save_temperature(data: dict):
    """Save temperature reading to database."""
    with get_session() as session:
        temp = Temperature(**data)
        session.merge(temp)
        logger.info(f"Saved temperature for {data['country_code']}: {data['temperature']}")
