"""Dry-run-first recomputation of existing Thermometer keys after attribution repair."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import text

from src.db import get_session, wait_for_db
from src.engine.index import (
    WINDOW_DAYS,
    calculate_temperature_at,
    suppress_temperature_alerts,
    use_temperature_history,
)
from src.methodology import TEMPERATURE_METHODOLOGY


TEMPERATURE_VALUE_FIELDS = (
    "temperature",
    "raw_sentiment",
    "diplomatic",
    "military",
    "economic",
    "cultural",
    "security",
    "article_count",
    "source_count",
    "trend",
    "anomaly_score",
)


@dataclass(frozen=True, slots=True)
class TemperatureDelta:
    time: datetime
    country_code: str
    before: dict[str, Any]
    after: dict[str, Any]
    changed_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecomputeReport:
    apply: bool
    output_days: int
    input_days: int
    input_start: datetime
    window_start: datetime
    window_end: datetime
    keys_read: int
    recalculated: int
    changed: int
    unchanged: int
    skipped: int
    upserted: int
    deltas: tuple[TemperatureDelta, ...]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _value(row: Any, name: str, default: Any = None) -> Any:
    if hasattr(row, name):
        return getattr(row, name)
    mapping = getattr(row, "_mapping", row if isinstance(row, dict) else {})
    return mapping.get(name, default)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def _values_from_row(row: Any) -> dict[str, Any]:
    return {
        field: _json_value(_value(row, field))
        for field in TEMPERATURE_VALUE_FIELDS
    }


def _values_from_calculation(calculation: dict[str, Any]) -> dict[str, Any]:
    return {
        field: _json_value(calculation.get(field))
        for field in TEMPERATURE_VALUE_FIELDS
    }


def _load_existing_rows(window_start: datetime, window_end: datetime) -> list[Any]:
    with get_session() as session:
        return session.execute(text("""
            /* attribution_recompute:existing_keys */
            SELECT time, country_code, temperature, raw_sentiment,
                   diplomatic, military, economic, cultural, security,
                   article_count, source_count, trend, anomaly_score
            FROM temperature
            WHERE time >= :window_start
              AND time <= :window_end
            ORDER BY time, country_code
        """), {
            "window_start": window_start,
            "window_end": window_end,
        }).fetchall()


def _load_history_seeds(
    country_codes: Sequence[str],
    window_start: datetime,
) -> list[Any]:
    if not country_codes:
        return []
    history_points = max(
        TEMPERATURE_METHODOLOGY.trend_history_points,
        TEMPERATURE_METHODOLOGY.anomaly_history_points,
    )
    with get_session() as session:
        return session.execute(text("""
            /* attribution_recompute:history_seeds */
            WITH ranked AS (
                SELECT time, country_code, temperature,
                       ROW_NUMBER() OVER (
                           PARTITION BY country_code ORDER BY time DESC
                       ) AS history_rank
                FROM temperature
                WHERE country_code = ANY(:country_codes)
                  AND time < :window_start
            )
            SELECT time, country_code, temperature
            FROM ranked
            WHERE history_rank <= :history_points
            ORDER BY time, country_code
        """), {
            "country_codes": list(country_codes),
            "window_start": window_start,
            "history_points": history_points,
        }).fetchall()


def _upsert_batch(batch: Sequence[dict[str, Any]]) -> None:
    if not batch:
        return
    with get_session() as session:
        session.execute(text("""
            INSERT INTO temperature (
                time, country_code, temperature, raw_sentiment,
                diplomatic, military, economic, cultural, security,
                article_count, source_count, trend, anomaly_score
            ) VALUES (
                :time, :country_code, :temperature, :raw_sentiment,
                :diplomatic, :military, :economic, :cultural, :security,
                :article_count, :source_count, :trend, :anomaly_score
            )
            ON CONFLICT (time, country_code) DO UPDATE SET
                temperature = EXCLUDED.temperature,
                raw_sentiment = EXCLUDED.raw_sentiment,
                diplomatic = EXCLUDED.diplomatic,
                military = EXCLUDED.military,
                economic = EXCLUDED.economic,
                cultural = EXCLUDED.cultural,
                security = EXCLUDED.security,
                article_count = EXCLUDED.article_count,
                source_count = EXCLUDED.source_count,
                trend = EXCLUDED.trend,
                anomaly_score = EXCLUDED.anomaly_score
        """), list(batch))


def _run_post_apply_jobs() -> None:
    """Refresh current derivatives, then rebuild only the normal 30-day threads."""

    from scripts.build_threads import rebuild_recent_threads_and_stories
    from scripts.calc_ru_index import calc_all as calculate_current_rri
    from scripts.generate_briefs import run_pass as generate_current_briefs
    from src.engine.signals import detect_all as detect_current_signals

    calculate_current_rri()
    detect_current_signals()
    generate_current_briefs(force=True)
    rebuild_recent_threads_and_stories(days=30)


def recompute_window(
    days: int = 90,
    apply: bool = False,
    *,
    batch_size: int = 100,
) -> RecomputeReport:
    """Recalculate existing keys only; write nothing unless ``apply`` is true."""

    if days < 1:
        raise ValueError("days must be positive")
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    window_end = _utc_now()
    window_start = window_end - timedelta(days=days)
    input_days = days + WINDOW_DAYS
    input_start = window_end - timedelta(days=input_days)
    existing_rows = _load_existing_rows(window_start, window_end)
    country_codes = sorted({str(_value(row, "country_code")) for row in existing_rows})
    history_seeds = _load_history_seeds(country_codes, window_start)
    history_by_country: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for row in history_seeds:
        temperature = _value(row, "temperature")
        if temperature is not None:
            history_by_country[str(_value(row, "country_code"))].append((
                _value(row, "time"),
                float(temperature),
            ))

    def rolling_history(
        country_code: str,
        as_of: datetime,
        limit: int,
    ) -> list[float]:
        eligible = [
            temperature
            for at, temperature in history_by_country[country_code]
            if at < as_of
        ]
        return list(reversed(eligible[-limit:]))

    deltas: list[TemperatureDelta] = []
    calculations: list[dict[str, Any]] = []
    changed = 0
    skipped = 0
    with suppress_temperature_alerts(), use_temperature_history(rolling_history):
        for row in existing_rows:
            key_time = _value(row, "time")
            country_code = str(_value(row, "country_code"))
            calculation = calculate_temperature_at(
                country_code,
                key_time,
                exclude_backfill=True,
            )
            if calculation is None:
                skipped += 1
                old_temperature = _value(row, "temperature")
                if old_temperature is not None:
                    history_by_country[country_code].append((
                        key_time,
                        float(old_temperature),
                    ))
                continue
            if (
                calculation.get("time") != key_time
                or calculation.get("country_code") != country_code
            ):
                raise RuntimeError("temperature calculator changed an existing key")

            before = _values_from_row(row)
            after = _values_from_calculation(calculation)
            changed_fields = tuple(
                field
                for field in TEMPERATURE_VALUE_FIELDS
                if before[field] != after[field]
            )
            if changed_fields:
                changed += 1
            deltas.append(TemperatureDelta(
                time=key_time,
                country_code=country_code,
                before=before,
                after=after,
                changed_fields=changed_fields,
            ))
            calculations.append({
                "time": key_time,
                "country_code": country_code,
                **after,
            })
            if after["temperature"] is not None:
                history_by_country[country_code].append((
                    key_time,
                    float(after["temperature"]),
                ))

    if apply:
        for start in range(0, len(calculations), batch_size):
            _upsert_batch(calculations[start:start + batch_size])
        _run_post_apply_jobs()

    recalculated = len(calculations)
    return RecomputeReport(
        apply=apply,
        output_days=days,
        input_days=input_days,
        input_start=input_start,
        window_start=window_start,
        window_end=window_end,
        keys_read=len(existing_rows),
        recalculated=recalculated,
        changed=changed,
        unchanged=recalculated - changed,
        skipped=skipped,
        upserted=recalculated if apply else 0,
        deltas=tuple(deltas),
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Cannot serialize {type(value).__name__}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recompute existing temperature keys after attribution repair",
    )
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    wait_for_db()
    report = recompute_window(
        days=args.days,
        apply=args.apply,
        batch_size=args.batch_size,
    )
    payload = json.dumps(
        asdict(report),
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
        default=_json_default,
    )
    print(payload)
    if args.report:
        args.report.write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
