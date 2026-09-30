"""Read-only analyst decision workspace endpoint."""

from fastapi import APIRouter, HTTPException, Query

from src.decision_workspace import load_decision_workspace


router = APIRouter(prefix="/api/v2", tags=["decision-workspace"])


@router.get("/decision-workspace")
def decision_workspace(country: str | None = Query(default=None, pattern=r"^[A-Za-z]{2}$")):
    try:
        return load_decision_workspace(country.upper() if country else None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
