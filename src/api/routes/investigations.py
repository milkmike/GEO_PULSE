"""Read-only RRI shift explanations and Thermometer methodology APIs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.public_urls import safe_public_url
from src.countries import COUNTRIES
from src.engine.explanations import load_index_explanation
from src.engine.ru_index import INDEX_VERSION
from src.methodology import temperature_methodology_payload


router = APIRouter(prefix="/api/v2", tags=["investigations"])
ExplanationService = Callable[..., dict]
DEFAULT_WINDOW_HOURS = 48


def get_explanation_service() -> ExplanationService:
    """Dependency boundary for the persisted, deterministic read service."""

    return load_index_explanation


def _require_timezone(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=422, detail=f"{field} must include a timezone")


def _resolve_window(
    *,
    at: datetime | None,
    window_hours: int | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> tuple[datetime, datetime]:
    if at is not None:
        if date_from is not None or date_to is not None:
            raise HTTPException(
                status_code=422,
                detail="use at/window_hours or from/to, not both",
            )
        _require_timezone(at, "at")
        hours = window_hours if window_hours is not None else DEFAULT_WINDOW_HOURS
        canonical_at = at.astimezone(timezone.utc)
        return canonical_at - timedelta(hours=hours), canonical_at
    if date_from is None or date_to is None:
        raise HTTPException(
            status_code=422,
            detail="provide at or both from and to",
        )
    if window_hours is not None:
        raise HTTPException(
            status_code=422,
            detail="window_hours is only valid with at",
        )
    _require_timezone(date_from, "from")
    _require_timezone(date_to, "to")
    if date_from >= date_to:
        raise HTTPException(status_code=422, detail="from must be earlier than to")
    return (
        date_from.astimezone(timezone.utc),
        date_to.astimezone(timezone.utc),
    )


@router.get("/countries/{code}/index-explanation")
def get_index_explanation(
    code: str,
    at: Optional[datetime] = Query(default=None),
    window_hours: Optional[int] = Query(default=None, ge=1, le=720),
    date_from: Optional[datetime] = Query(default=None, alias="from"),
    date_to: Optional[datetime] = Query(default=None, alias="to"),
    rri_version: str = Query(default=INDEX_VERSION, pattern=r"^v[0-9]+$"),
    service: ExplanationService = Depends(get_explanation_service),
):
    """Explain a persisted RRI interval without provider or write side effects."""

    country_code = code.strip().upper()
    if country_code not in COUNTRIES:
        raise HTTPException(status_code=404, detail=f"Unknown country: {country_code}")
    from_time, to_time = _resolve_window(
        at=at,
        window_hours=window_hours,
        date_from=date_from,
        date_to=date_to,
    )
    try:
        raw_payload = service(
            country_code=country_code,
            from_time=from_time,
            to_time=to_time,
            rri_version=rri_version,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    payload = dict(raw_payload)
    payload["context"] = [
        {**dict(item), "url": safe_public_url(item.get("url"))}
        for item in raw_payload.get("context", [])
    ]
    return payload


@router.get("/methodology/temperature")
def get_temperature_methodology():
    """Return the immutable Thermometer v1 definition used by the engine."""

    return temperature_methodology_payload()
