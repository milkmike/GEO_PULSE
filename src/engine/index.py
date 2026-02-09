"""Temperature index calculation."""
import logging
import math
import statistics
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from src.config import COUNTRY_NAMES, EVENT_TYPE_WEIGHTS
from src.db import get_session, Temperature, Alert

logger = logging.getLogger(__name__)

WINDOW_DAYS = 14
TAU = WINDOW_DAYS * 86400  # decay time constant in seconds

ACTION_MULTIPLIERS = {
    1: 1,    # заявление
    2: 3,    # переговоры/визит
    3: 5,    # соглашение
    4: 8,    # санкции/запрет
    5: 12,   # выход из организации/разрыв
    6: 15,   # военные действия
}


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
                  AND ar.published_at > NOW() - INTERVAL ':days days'
            """.replace(":days", str(WINDOW_DAYS))),
            {"cc": country_code},
        ).fetchall()

        if not rows:
            return None

        numerator = 0.0
        denominator = 0.0
        type_sums = {t: 0.0 for t in ("diplomatic", "military", "economic", "cultural", "security")}
        type_counts = {t: 0 for t in type_sums}
        source_ids = set()

        # Phase 1: Group articles by event_key to prevent one story from dominating
        event_clusters = {}  # event_key -> list of rows
        unclustered = []     # articles without event_key
        
        for row in rows:
            ek = getattr(row, 'event_key', None)
            if ek and len(str(ek)) > 3:
                event_clusters.setdefault(str(ek).lower().strip(), []).append(row)
            else:
                unclustered.append(row)
        
        # Phase 2: For each cluster, sort by source weight (best source first)
        # Apply diminishing returns: 1st article full weight, each next 20%
        clustered_rows = []
        for ek, cluster in event_clusters.items():
            cluster.sort(key=lambda r: float(r.weight or 1.0), reverse=True)
            for i, row in enumerate(cluster):
                clustered_rows.append((row, 0.2 ** i))
        
        # Unclustered articles get full weight
        for row in unclustered:
            clustered_rows.append((row, 1.0))
        
        for row, cluster_decay in clustered_rows:
            sentiment = float(row.sentiment)
            published_at = row.published_at
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            
            age = (now - published_at).total_seconds()
            decay = math.exp(-age / TAU)
            
            w_source = float(row.weight or 1.0)
            event_type = row.event_type
            w_type = EVENT_TYPE_WEIGHTS.get(event_type, 1.0)
            
            importance = 1 + math.log1p(row.reprint_count)
            
            # Action level multiplier
            action_mult = ACTION_MULTIPLIERS.get(row.action_level or 1, 1)
            
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
        temperature = round(raw_sentiment * (100 / 3), 1)

        # Component averages
        components = {}
        for t in type_sums:
            if type_counts[t] > 0:
                components[t] = round(type_sums[t] / type_counts[t], 2)
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
            "raw_sentiment": round(raw_sentiment, 2),
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
        text("""
            SELECT temperature FROM temperature
            WHERE country_code = :cc
            ORDER BY time DESC LIMIT 3
        """),
        {"cc": country_code},
    ).fetchall()

    if len(rows) < 2:
        return "stable"

    prev_temps = [float(r.temperature) for r in rows]
    avg_prev = statistics.mean(prev_temps)
    diff = current - avg_prev
    
    if diff > 5:
        return "rising"
    elif diff < -5:
        return "falling"
    return "stable"


def detect_anomaly(session, country_code: str, current: float) -> float | None:
    """Z-score based anomaly detection."""
    rows = session.execute(
        text("""
            SELECT temperature FROM temperature
            WHERE country_code = :cc
            ORDER BY time DESC LIMIT 30
        """),
        {"cc": country_code},
    ).fetchall()

    if len(rows) < 5:
        return None

    temps = [float(r.temperature) for r in rows]
    mean = statistics.mean(temps)
    std = statistics.stdev(temps) if len(temps) > 1 else 1.0
    if std == 0:
        std = 1.0

    z_score = round((current - mean) / std, 2)
    
    # Create alert if anomalous
    if abs(z_score) > 2.0:
        severity = "critical" if abs(z_score) > 3.0 else "warning"
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
