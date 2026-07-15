"""Temperature index calculation."""
import logging
import math
import statistics
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from src.config import COUNTRY_NAMES
from src.db import get_session, Temperature, Alert
from src.methodology import TEMPERATURE_METHODOLOGY

logger = logging.getLogger(__name__)

WINDOW_DAYS = TEMPERATURE_METHODOLOGY.window_days
TAU = TEMPERATURE_METHODOLOGY.time_decay_tau_seconds
EVENT_TYPE_WEIGHTS = TEMPERATURE_METHODOLOGY.event_type_weights
ACTION_MULTIPLIERS = TEMPERATURE_METHODOLOGY.action_level_weights


def calculate_temperature(country_code: str) -> dict | None:
    """Calculate current temperature for a country based on analyzed articles."""
    now = datetime.now(timezone.utc)

    with get_session() as session:
        # Get analyzed articles with sentiment from last WINDOW_DAYS
        rows = session.execute(
            text("""
                SELECT a.sentiment, a.event_type, a.sentiment_confidence,
                       a.action_level, a.event_key,
                       ar.published_at, s.weight, s.id as source_id,
                       COALESCE(ar.reprint_count, 0) as reprint_count
                FROM analysis a
                JOIN articles ar ON a.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE s.country_code = :cc
                  AND a.is_relevant = true
                  AND a.sentiment IS NOT NULL
                  AND ar.is_backfill = false
                  AND ar.published_at > NOW() - INTERVAL ':days days'
            """.replace(":days", str(WINDOW_DAYS))),
            {"cc": country_code},
        ).fetchall()

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
        for ek, cluster in event_clusters.items():
            cluster.sort(
                key=lambda r: float(
                    r.weight or TEMPERATURE_METHODOLOGY.source_weight_default
                ),
                reverse=True,
            )
            for i, row in enumerate(cluster):
                clustered_rows.append((
                    row,
                    TEMPERATURE_METHODOLOGY.cluster_diminishing_base ** i,
                ))
        
        # Unclustered articles get full weight
        for row in unclustered:
            clustered_rows.append((row, TEMPERATURE_METHODOLOGY.unclustered_weight))
        
        for row, cluster_decay in clustered_rows:
            sentiment = float(row.sentiment)
            published_at = row.published_at
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            
            age = (now - published_at).total_seconds()
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
        for t in type_sums:
            if type_counts[t] > 0:
                components[t] = round(
                    type_sums[t] / type_counts[t],
                    TEMPERATURE_METHODOLOGY.component_round_digits,
                )
            else:
                components[t] = None

        # Trend detection
        trend = detect_trend(session, country_code, temperature)
        
        # Anomaly detection
        anomaly_score = detect_anomaly(session, country_code, temperature)

        return {
            "time": now,
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


def detect_trend(session, country_code: str, current: float) -> str:
    """Simple trend detection based on last 3 readings."""
    rows = session.execute(
        text(f"""
            SELECT temperature FROM temperature
            WHERE country_code = :cc
            ORDER BY time DESC LIMIT {TEMPERATURE_METHODOLOGY.trend_history_points}
        """),
        {"cc": country_code},
    ).fetchall()

    if len(rows) < TEMPERATURE_METHODOLOGY.trend_minimum_samples:
        return "stable"

    prev_temps = [float(r.temperature) for r in rows]
    avg_prev = statistics.mean(prev_temps)
    diff = current - avg_prev
    
    if diff > TEMPERATURE_METHODOLOGY.trend_threshold:
        return "rising"
    elif diff < -TEMPERATURE_METHODOLOGY.trend_threshold:
        return "falling"
    return "stable"


def detect_anomaly(session, country_code: str, current: float) -> float | None:
    """Z-score based anomaly detection."""
    rows = session.execute(
        text(f"""
            SELECT temperature FROM temperature
            WHERE country_code = :cc
            ORDER BY time DESC LIMIT {TEMPERATURE_METHODOLOGY.anomaly_history_points}
        """),
        {"cc": country_code},
    ).fetchall()

    if len(rows) < TEMPERATURE_METHODOLOGY.anomaly_minimum_samples:
        return None

    temps = [float(r.temperature) for r in rows]
    mean = statistics.mean(temps)
    std = (
        statistics.stdev(temps)
        if len(temps) > 1
        else TEMPERATURE_METHODOLOGY.anomaly_zero_std_fallback
    )
    if std == 0:
        std = TEMPERATURE_METHODOLOGY.anomaly_zero_std_fallback

    z_score = round(
        (current - mean) / std,
        TEMPERATURE_METHODOLOGY.anomaly_round_digits,
    )
    
    # Create alert if anomalous
    if abs(z_score) > TEMPERATURE_METHODOLOGY.anomaly_warning_threshold:
        severity = (
            "critical"
            if abs(z_score) > TEMPERATURE_METHODOLOGY.anomaly_critical_threshold
            else "warning"
        )
        alert = Alert(
            country_code=country_code,
            alert_type="anomaly",
            severity=severity,
            title=f"Anomaly detected for {COUNTRY_NAMES.get(country_code, country_code)}",
            description=f"Z-score: {z_score}, current temp: {current}",
            data={"z_score": z_score, "temperature": current, "mean": mean, "std": std},
        )
        session.add(alert)
    
    return z_score


def save_temperature(data: dict):
    """Save temperature reading to database."""
    with get_session() as session:
        temp = Temperature(**data)
        session.merge(temp)
        logger.info(f"Saved temperature for {data['country_code']}: {data['temperature']}")
