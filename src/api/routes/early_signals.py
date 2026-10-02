"""Public read-only view of editorially released early hypotheses."""
from fastapi import APIRouter, HTTPException, Query

from src.monitoring_registry import MONITORING_COUNTRIES as COUNTRIES
from src.early_signal_store import list_dossiers
from src.global_signal_store import latest_monitor

router = APIRouter(prefix="/api/v2", tags=["early-signals"])


@router.get("/early-signals/coverage")
def early_signal_coverage():
    """Read the last background snapshot; never call models or collectors here."""
    return latest_monitor()


@router.get("/early-signals")
def early_signals(country: str | None = Query(default=None, pattern=r"^[A-Za-z]{2}$"),
                  limit: int = Query(default=6, ge=1, le=12)):
    country = country.upper() if country is not None else None
    if country is not None and country not in COUNTRIES:
        raise HTTPException(status_code=422, detail="invalid country")
    return list_dossiers(country=country, limit=limit)
