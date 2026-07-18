"""Normalization of structured policy and economic changes into Radar actions."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from .repository import make_observation
from .types import Contour, Observation, ObservationWindow


ACTION_SUBJECTS = {
    "sanctions_pressure": "policy:sanctions:russia",
    "un_votes": "diplomacy:un_alignment:russia",
    "trade_data": "economy:trade:russia",
    "ru_fossil_imports": "energy:imports:russia",
}


_SANCTIONS = text("""
    SELECT country_code, delta::double precision AS value,
           COALESCE(last_change::timestamp, updated_at)::timestamptz AS observed_at,
           'sanctions_pressure:' || country_code || ':' ||
             COALESCE(last_change::text, updated_at::text) AS source_id
    FROM sanctions_pressure
    WHERE delta IS NOT NULL AND delta <> 0
      AND COALESCE(last_change::timestamp, updated_at) >= :window_start
      AND COALESCE(last_change::timestamp, updated_at) < :window_end
""")

_UN_VOTES = text("""
    WITH snapshots AS (
      SELECT country_code, year, agreement_pct, updated_at,
             LAG(agreement_pct) OVER (PARTITION BY country_code ORDER BY year) AS prior_value
      FROM un_votes
    )
    SELECT country_code, (agreement_pct - prior_value)::double precision AS value,
           updated_at::timestamptz AS observed_at,
           'un_votes:' || country_code || ':' || year::text AS source_id
    FROM snapshots
    WHERE prior_value IS NOT NULL AND agreement_pct IS DISTINCT FROM prior_value
      AND updated_at >= :window_start AND updated_at < :window_end
""")

_TRADE = text("""
    WITH snapshots AS (
      SELECT country_code, year, total_trade_usd, yoy_change_pct, updated_at,
             LAG(total_trade_usd) OVER (PARTITION BY country_code ORDER BY year) AS prior_value
      FROM trade_data
    )
    SELECT country_code,
           COALESCE(yoy_change_pct, total_trade_usd - prior_value)::double precision AS value,
           updated_at::timestamptz AS observed_at,
           'trade_data:' || country_code || ':' || year::text AS source_id
    FROM snapshots
    WHERE ((yoy_change_pct IS NOT NULL AND yoy_change_pct <> 0)
       OR (prior_value IS NOT NULL AND total_trade_usd IS DISTINCT FROM prior_value))
      AND updated_at >= :window_start AND updated_at < :window_end
""")

_FOSSIL = text("""
    SELECT imports.country_code, imports.total_eur::double precision AS value,
           updated_at::timestamptz AS observed_at,
           'ru_fossil_imports:' || imports.country_code || ':' || imports.total_eur::text AS source_id
    FROM ru_fossil_imports imports
    WHERE imports.updated_at >= :window_start AND imports.updated_at < :window_end
      AND COALESCE((
          SELECT prior.evidence->>'snapshot_value'
          FROM radar_observations prior
          WHERE prior.contour = 'action'
            AND prior.country_code = imports.country_code
            AND prior.subject_key = 'energy:imports:russia'
            AND prior.evidence->>'dataset' = 'ru_fossil_imports'
          ORDER BY prior.observed_at DESC, prior.id DESC
          LIMIT 1
      ), '') IS DISTINCT FROM imports.total_eur::text
""")


def _value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping.get(name, default)
    return getattr(row, name, default)


def _direction(value: float) -> str:
    return "increase" if value > 0 else "decrease"


def _build_rows(session, sql, window: ObservationWindow) -> list[Any]:
    return session.execute(sql, {
        "window_start": window.start,
        "window_end": window.end,
    }).fetchall()


def build_action_observations(session, window: ObservationWindow) -> list[Observation]:
    """Use authoritative structured changes only; media analysis is excluded."""

    datasets = (
        ("sanctions_pressure", _SANCTIONS, "registry", "delta", "event"),
        ("un_votes", _UN_VOTES, "formal", "agreement_pct_delta", "annual"),
        ("trade_data", _TRADE, "registry", "trade_change", "annual"),
        ("ru_fossil_imports", _FOSSIL, "registry", "import_value", "snapshot"),
    )
    observations: list[Observation] = []
    for dataset, query, authority, metric, resolution in datasets:
        for row in _build_rows(session, query, window):
            country = str(_value(row, "country_code", "")).upper()
            observed_at = _value(row, "observed_at")
            raw_value = _value(row, "value")
            if len(country) != 2 or observed_at is None or raw_value is None:
                continue
            value = float(raw_value)
            if value == 0:
                continue
            source_id = str(_value(row, "source_id", f"{dataset}:{country}:{observed_at.isoformat()}"))
            observations.append(make_observation(
                country_code=country,
                contour=Contour.ACTION,
                subject_key=ACTION_SUBJECTS[dataset],
                direction=_direction(value),
                metric=metric,
                observed_at=observed_at,
                window=window,
                value=value,
                source_count=1,
                coverage_confidence=1.0,
                authority=authority,
                baseline={"temporal_resolution": resolution},
                evidence={
                    "dataset": dataset,
                    "source_id": source_id,
                    "temporal_resolution": resolution,
                    "snapshot_value": str(raw_value) if resolution == "snapshot" else None,
                },
                evidence_ids=(source_id,),
            ))
    return observations
