"""API routes for Narrative Threads."""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from src.config import COUNTRY_NAMES
from src.db import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["threads"])


@router.get("/threads")
def list_threads(
    country: Optional[str] = Query(default=None, description="Country code filter (e.g. KZ)"),
    status: Optional[str] = Query(default=None, description="Status filter: developing|resolved|dormant"),
    limit: int = Query(default=20, le=100),
):
    """List narrative threads, sorted by importance_score DESC."""
    conditions = []
    params: dict = {"lim": limit}

    if country:
        conditions.append("t.country_code = :country")
        params["country"] = country.upper()

    if status:
        conditions.append("t.status = :status")
        params["status"] = status

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    with get_session() as session:
        rows = session.execute(text(f"""
            SELECT t.id, t.country_code, t.thread_key, t.title, t.narrative,
                   t.status, t.arc_phase, t.first_seen, t.last_seen,
                   t.article_count, t.avg_sentiment, t.max_action_level,
                   t.importance_score, t.generated_at
            FROM threads t
            {where}
            ORDER BY t.importance_score DESC
            LIMIT :lim
        """), params).fetchall()

        return {
            "threads": [
                {
                    "id": r.id,
                    "country_code": r.country_code.strip(),
                    "country_name": COUNTRY_NAMES.get(r.country_code.strip(), r.country_code.strip()),
                    "thread_key": r.thread_key,
                    "title": r.title,
                    "narrative": r.narrative,
                    "status": r.status,
                    "arc_phase": r.arc_phase,
                    "first_seen": r.first_seen.isoformat() if r.first_seen else None,
                    "last_seen": r.last_seen.isoformat() if r.last_seen else None,
                    "article_count": r.article_count,
                    "avg_sentiment": float(r.avg_sentiment) if r.avg_sentiment is not None else None,
                    "max_action_level": r.max_action_level,
                    "importance_score": float(r.importance_score) if r.importance_score is not None else 0,
                    "generated_at": r.generated_at.isoformat() if r.generated_at else None,
                }
                for r in rows
            ]
        }


@router.get("/threads/{thread_id}")
def get_thread(thread_id: int):
    """Get thread details with article timeline."""
    with get_session() as session:
        thread = session.execute(text("""
            SELECT t.id, t.country_code, t.thread_key, t.title, t.narrative,
                   t.status, t.arc_phase, t.first_seen, t.last_seen,
                   t.article_count, t.avg_sentiment, t.max_action_level,
                   t.importance_score, t.generated_at
            FROM threads t
            WHERE t.id = :tid
        """), {"tid": thread_id}).fetchone()

        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")

        # Fetch articles timeline
        articles = session.execute(text("""
            SELECT ar.id, ar.title, ar.url, ar.published_at,
                   an.sentiment, an.action_level, an.event_type,
                   s.name AS source_name, s.tier
            FROM thread_articles ta
            JOIN articles ar ON ta.article_id = ar.id
            JOIN analysis an ON an.article_id = ar.id
            JOIN sources s ON ar.source_id = s.id
            WHERE ta.thread_id = :tid
            ORDER BY ar.published_at ASC
        """), {"tid": thread_id}).fetchall()

        cc = thread.country_code.strip()

        return {
            "id": thread.id,
            "country_code": cc,
            "country_name": COUNTRY_NAMES.get(cc, cc),
            "thread_key": thread.thread_key,
            "title": thread.title,
            "narrative": thread.narrative,
            "status": thread.status,
            "arc_phase": thread.arc_phase,
            "first_seen": thread.first_seen.isoformat() if thread.first_seen else None,
            "last_seen": thread.last_seen.isoformat() if thread.last_seen else None,
            "article_count": thread.article_count,
            "avg_sentiment": float(thread.avg_sentiment) if thread.avg_sentiment is not None else None,
            "max_action_level": thread.max_action_level,
            "importance_score": float(thread.importance_score) if thread.importance_score is not None else 0,
            "generated_at": thread.generated_at.isoformat() if thread.generated_at else None,
            "timeline": [
                {
                    "article_id": a.id,
                    "title": a.title,
                    "url": a.url,
                    "published_at": a.published_at.isoformat() if a.published_at else None,
                    "sentiment": float(a.sentiment) if a.sentiment is not None else None,
                    "action_level": a.action_level or 1,
                    "event_type": a.event_type,
                    "source": a.source_name,
                    "tier": a.tier,
                }
                for a in articles
            ],
        }


@router.get("/countries/{code}/threads")
def get_country_threads(
    code: str,
    status: Optional[str] = Query(default=None, description="Status filter"),
    limit: int = Query(default=10, le=50),
):
    """Get threads for a specific country."""
    code = code.upper()
    if code not in COUNTRY_NAMES:
        raise HTTPException(status_code=404, detail="Unknown country code")

    conditions = ["t.country_code = :cc"]
    params: dict = {"cc": code, "lim": limit}

    if status:
        conditions.append("t.status = :status")
        params["status"] = status

    where = "WHERE " + " AND ".join(conditions)

    with get_session() as session:
        rows = session.execute(text(f"""
            SELECT t.id, t.country_code, t.thread_key, t.title, t.narrative,
                   t.status, t.arc_phase, t.first_seen, t.last_seen,
                   t.article_count, t.avg_sentiment, t.max_action_level,
                   t.importance_score, t.generated_at
            FROM threads t
            {where}
            ORDER BY t.importance_score DESC
            LIMIT :lim
        """), params).fetchall()

        return {
            "country": code,
            "name": COUNTRY_NAMES.get(code, code),
            "threads": [
                {
                    "id": r.id,
                    "country_code": r.country_code.strip(),
                    "thread_key": r.thread_key,
                    "title": r.title,
                    "narrative": r.narrative,
                    "status": r.status,
                    "arc_phase": r.arc_phase,
                    "first_seen": r.first_seen.isoformat() if r.first_seen else None,
                    "last_seen": r.last_seen.isoformat() if r.last_seen else None,
                    "article_count": r.article_count,
                    "avg_sentiment": float(r.avg_sentiment) if r.avg_sentiment is not None else None,
                    "max_action_level": r.max_action_level,
                    "importance_score": float(r.importance_score) if r.importance_score is not None else 0,
                }
                for r in rows
            ],
        }
