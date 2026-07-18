"""Normalization of structured policy and economic changes into Radar actions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text

from .repository import make_observation
from .types import Contour, Observation, ObservationWindow


ACTION_SUBJECTS = {
    "un_votes": "diplomacy:un_alignment:russia",
    "trade_data": "economy:trade:russia",
    "ru_fossil_imports": "energy:imports:russia",
}

_REGISTERED_COUNTRIES = text("""
    /* radar_registered_country_codes */
    SELECT code
    FROM countries
""")

# A refreshed annual row has the same effective date and data fingerprint, so
# it resolves to the same input hash rather than becoming a new action.
_UN_VOTES = text("""
    WITH periods AS (
      SELECT votes.country_code, votes.year, votes.agreement_pct, votes.updated_at,
             LAG(votes.agreement_pct) OVER (
               PARTITION BY votes.country_code ORDER BY votes.year
             ) AS previous_value
      FROM un_votes votes
      JOIN countries tracked ON tracked.code = votes.country_code
    )
    SELECT country_code, year, agreement_pct::numeric AS current_value,
           previous_value::numeric AS previous_value,
           (agreement_pct - previous_value)::numeric AS value,
           updated_at::timestamptz AS updated_at
    FROM periods
    WHERE previous_value IS NOT NULL AND agreement_pct IS DISTINCT FROM previous_value
      AND updated_at >= :window_start
      AND updated_at < :window_end
""")

_TRADE = text("""
    WITH periods AS (
      SELECT trade.country_code, trade.year, trade.total_trade_usd,
             trade.yoy_change_pct, trade.updated_at,
             LAG(trade.total_trade_usd) OVER (
               PARTITION BY trade.country_code ORDER BY trade.year
             ) AS previous_value
      FROM trade_data trade
      JOIN countries tracked ON tracked.code = trade.country_code
    )
    SELECT country_code, year, total_trade_usd::numeric AS current_value,
           previous_value::numeric AS previous_value,
           COALESCE(yoy_change_pct, total_trade_usd - previous_value)::numeric AS value,
           yoy_change_pct::numeric AS yoy_change_pct,
           updated_at::timestamptz AS updated_at
    FROM periods
    WHERE ((yoy_change_pct IS NOT NULL AND yoy_change_pct <> 0)
       OR (previous_value IS NOT NULL AND total_trade_usd IS DISTINCT FROM previous_value))
      AND updated_at >= :window_start
      AND updated_at < :window_end
""")

# ``radar_observations`` and ``action_events`` retain prior comparable
# snapshots. The numeric casts keep database comparison semantic, not textual.
_FOSSIL = text("""
    SELECT imports.country_code, imports.total_eur::numeric AS current_value,
           previous.value AS previous_value,
           (imports.total_eur::numeric - previous.value) AS value,
           imports.updated_at::timestamptz AS observed_at
    FROM ru_fossil_imports imports
    JOIN countries tracked ON tracked.code = imports.country_code
    JOIN LATERAL (
      SELECT candidates.value
      FROM (
        SELECT prior.observed_at AS at, prior.id AS record_id,
               COALESCE((prior.evidence->>'current_value')::numeric,
                        (prior.evidence->>'snapshot_value')::numeric,
                        prior.value::numeric) AS value
        FROM radar_observations prior
        WHERE prior.contour = 'action'
          AND prior.country_code = imports.country_code
          AND prior.subject_key = 'energy:imports:russia'
          AND prior.authority IN ('registry', 'formal')
          AND prior.evidence->>'dataset' = 'ru_fossil_imports'
          AND prior.evidence->>'status' = 'verified'
        UNION ALL
        SELECT event.effective_at AS at, event.id AS record_id,
               COALESCE((event.details->>'current_value')::numeric,
                        (event.details->>'snapshot_value')::numeric) AS value
        FROM action_events event
        WHERE event.country_code = imports.country_code
          AND event.subject_key = 'energy:imports:russia'
          AND event.authority IN ('registry', 'formal')
          AND event.status = 'verified'
          AND event.details->>'dataset' = 'ru_fossil_imports'
      ) candidates
      WHERE candidates.value IS NOT NULL
      ORDER BY candidates.at DESC, candidates.record_id DESC
      LIMIT 1
    ) previous ON TRUE
    WHERE imports.updated_at >= :window_start AND imports.updated_at < :window_end
      AND imports.total_eur::numeric IS DISTINCT FROM previous.value
""")


_ANNUAL_FINGERPRINT_GATE = text("""
    /* annual_source_fingerprint_gate */
    SELECT EXISTS (
        SELECT 1
        FROM radar_observations prior
        WHERE prior.contour = 'action'
          AND prior.country_code = :country_code
          AND prior.evidence->>'dataset' = :dataset
          AND prior.evidence->>'source_id' = :source_id
        UNION ALL
        SELECT 1
        FROM action_events prior
        WHERE prior.country_code = :country_code
          AND prior.details->>'dataset' = :dataset
          AND prior.details->>'source_id' = :source_id
    )
""")


def _value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping.get(name, default)
    return getattr(row, name, default)


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _number(value: Decimal) -> float:
    """A JSON-safe canonical number with no text/numeric comparison ambiguity."""

    return float(value)


def _fingerprint(value: Decimal) -> str:
    normalized = value.normalize()
    rendered = format(normalized, "f")
    return "0" if rendered in {"-0", ""} else rendered


def _direction(value: Decimal) -> str:
    return "increase" if value > 0 else "decrease"


def _alignment_direction(value: Decimal) -> str:
    """Normalize action direction to its effect on the Russia relationship."""

    return "warming" if value > 0 else "hardening"


def _build_rows(session, sql, window: ObservationWindow) -> list[Any]:
    return session.execute(sql, {
        "window_start": window.start,
        "window_end": window.end,
    }).fetchall()


def _registered_country_codes(session) -> frozenset[str]:
    rows = session.execute(_REGISTERED_COUNTRIES).fetchall()
    return frozenset(
        str(_value(row, "code", "")).strip().upper()
        for row in rows
        if len(str(_value(row, "code", "")).strip()) == 2
    )


def _source_fingerprint(
    *, dataset: str, country: str, observed_at: datetime, resolution: str,
    current: Decimal | None, previous: Decimal | None, delta: Decimal,
    period_year: int | None,
) -> str:
    period = str(period_year) if resolution == "year" else observed_at.isoformat()
    current_fingerprint = _fingerprint(current if current is not None else delta)
    previous_fingerprint = _fingerprint(previous if previous is not None else Decimal("0"))
    delta_fingerprint = _fingerprint(delta)
    return ":".join((
        dataset, country, period, current_fingerprint, previous_fingerprint,
        delta_fingerprint,
    ))


def _annual_fingerprint_exists(session, *, country: str, dataset: str, source_id: str) -> bool:
    return bool(session.execute(_ANNUAL_FINGERPRINT_GATE, {
        "country_code": country,
        "dataset": dataset,
        "source_id": source_id,
    }).scalar())


def _structured_observation(
    *, dataset: str, authority: str, metric: str, resolution: str,
    country: str, observed_at: datetime, current: Decimal | None,
    previous: Decimal | None, delta: Decimal, period_year: int | None = None,
    yoy_change_pct: Decimal | None = None, source_id: str | None = None,
    window: ObservationWindow,
) -> Observation:
    source_id = source_id or _source_fingerprint(
        dataset=dataset, country=country, observed_at=observed_at,
        resolution=resolution, current=current, previous=previous, delta=delta,
        period_year=period_year,
    )
    evidence = {
        "dataset": dataset,
        "status": "verified",
        "source_id": source_id,
        "temporal_resolution": resolution,
        "period_year": period_year,
        "updated_at": observed_at.isoformat() if resolution == "year" else None,
        "previous_value": _number(previous) if previous is not None else None,
        "current_value": _number(current) if current is not None else None,
        "delta": _number(delta),
        "yoy_change_pct": _number(yoy_change_pct) if yoy_change_pct is not None else None,
        "alignment_subject": ACTION_SUBJECTS[dataset],
        "alignment_direction": _alignment_direction(delta),
    }
    return make_observation(
        country_code=country,
        contour=Contour.ACTION,
        subject_key=ACTION_SUBJECTS[dataset],
        direction=_direction(delta),
        metric=metric,
        observed_at=observed_at,
        window=window,
        value=_number(delta),
        source_count=1,
        coverage_confidence=1.0,
        authority=authority,
        baseline={"temporal_resolution": resolution},
        evidence=evidence,
        evidence_ids=(source_id,),
    )


def build_action_observations(session, window: ObservationWindow) -> list[Observation]:
    """Use registry/formal changes only; media analysis is never an input."""

    registered_countries = _registered_country_codes(session)
    datasets = (
        ("un_votes", _UN_VOTES, "formal", "agreement_pct_delta", "year"),
        ("trade_data", _TRADE, "registry", "trade_change", "year"),
        ("ru_fossil_imports", _FOSSIL, "registry", "import_value_delta", "snapshot"),
    )
    observations: list[Observation] = []
    for dataset, query, authority, metric, resolution in datasets:
        for row in _build_rows(session, query, window):
            country = str(_value(row, "country_code", "")).upper()
            if country not in registered_countries:
                continue
            period_year = int(_value(row, "year")) if resolution == "year" and _value(row, "year") is not None else None
            observed_at = _value(row, "updated_at") if resolution == "year" else _value(row, "observed_at")
            if observed_at is None or (resolution == "year" and period_year is None):
                continue
            current = _decimal(_value(row, "current_value"))
            previous = _decimal(_value(row, "previous_value"))
            delta = _decimal(_value(row, "value"))
            if dataset == "ru_fossil_imports":
                if current is None or previous is None:
                    continue
                delta = current - previous
            elif resolution == "year":
                if current is None or previous is None:
                    continue
                delta = current - previous if delta is None else delta
            if delta is None or delta == 0:
                continue
            source_id = _source_fingerprint(
                dataset=dataset, country=country, observed_at=observed_at,
                resolution=resolution, current=current, previous=previous,
                delta=delta, period_year=period_year,
            )
            if resolution == "year" and _annual_fingerprint_exists(
                session, country=country, dataset=dataset, source_id=source_id,
            ):
                continue
            observations.append(_structured_observation(
                dataset=dataset, authority=authority, metric=metric,
                resolution=resolution, country=country, observed_at=observed_at,
                current=current, previous=previous, delta=delta, window=window,
                period_year=period_year,
                yoy_change_pct=_decimal(_value(row, "yoy_change_pct")),
                source_id=source_id,
            ))
    return observations
