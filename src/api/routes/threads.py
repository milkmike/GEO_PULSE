"""API routes for Narrative Threads v2."""
import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from src.config import COUNTRY_NAMES
from src.db import get_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["threads"])

THREAD_FIELDS = """
    t.id, t.country_code, t.thread_key, t.title, t.narrative,
    t.status, t.arc_phase, t.first_seen, t.last_seen,
    t.article_count, t.avg_sentiment, t.max_action_level,
    t.importance_score, t.velocity, t.sentiment_shift,
    t.merged_keys, t.related_threads, t.summary_json,
    t.generated_at
"""


class ThreadResponse(BaseModel):
    id: int
    country_code: str
    country_name: str
    thread_key: str
    title: str
    narrative: str | None
    status: str
    arc_phase: str
    first_seen: str | None
    last_seen: str | None
    article_count: int
    avg_sentiment: float | None
    max_action_level: int | None
    importance_score: float
    velocity: float
    sentiment_shift: float
    merged_keys: list[str]
    related_threads: list[int]
    summary: dict[str, Any] | None
    generated_at: str | None


class ThreadTimelineItem(BaseModel):
    article_id: int
    title: str | None
    url: str | None
    published_at: str | None
    sentiment: float | None
    action_level: int
    event_type: str | None
    source: str
    tier: str | None


class ThreadsListResponse(BaseModel):
    threads: list[ThreadResponse]


class RelatedThreadsResponse(BaseModel):
    related: list[ThreadResponse]


class ThreadDetailResponse(ThreadResponse):
    timeline: list[ThreadTimelineItem]
    related: list[ThreadResponse] | None = None


class CountryThreadsResponse(BaseModel):
    country: str
    name: str
    threads: list[ThreadResponse]


def thread_to_dict(r) -> dict:
    cc = r.country_code.strip()
    result = {
        "id": r.id,
        "country_code": cc,
        "country_name": COUNTRY_NAMES.get(cc, cc),
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
        "velocity": float(r.velocity) if r.velocity is not None else 0,
        "sentiment_shift": float(r.sentiment_shift) if r.sentiment_shift is not None else 0,
        "merged_keys": r.merged_keys or [],
        "related_threads": r.related_threads or [],
        "summary": r.summary_json if r.summary_json else None,
        "generated_at": r.generated_at.isoformat() if r.generated_at else None,
    }
    return result


@router.get("/threads", response_model=ThreadsListResponse)
def list_threads(
    country: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    arc_phase: Optional[str] = Query(default=None),
    min_importance: float = Query(default=0),
    sort: str = Query(default="importance", pattern="^(importance|velocity|recent|articles)$"),
    limit: int = Query(default=30, le=100),
):
    """List narrative threads with filters and sorting."""
    conditions = ["t.importance_score >= :min_imp"]
    params: dict = {"lim": limit, "min_imp": min_importance}

    if country:
        conditions.append("t.country_code = :country")
        params["country"] = country.upper()
    if status:
        conditions.append("t.status = :status")
        params["status"] = status
    if arc_phase:
        conditions.append("t.arc_phase = :arc_phase")
        params["arc_phase"] = arc_phase

    where = "WHERE " + " AND ".join(conditions)

    order_map = {
        "importance": "t.importance_score DESC",
        "velocity": "t.velocity DESC NULLS LAST",
        "recent": "t.last_seen DESC NULLS LAST",
        "articles": "t.article_count DESC",
    }
    order = order_map.get(sort, order_map["importance"])

    with get_session() as session:
        rows = session.execute(text(f"""
            SELECT {THREAD_FIELDS}
            FROM threads t
            {where}
            ORDER BY {order}
            LIMIT :lim
        """), params).fetchall()

        return {"threads": [thread_to_dict(r) for r in rows]}


@router.get("/threads/{thread_id}", response_model=ThreadDetailResponse)
def get_thread(thread_id: int):
    """Get thread with full details + article timeline."""
    with get_session() as session:
        thread = session.execute(text(f"""
            SELECT {THREAD_FIELDS}
            FROM threads t WHERE t.id = :tid
        """), {"tid": thread_id}).fetchone()

        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")

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

        result = thread_to_dict(thread)
        result["timeline"] = [
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
        ]

        # Fetch related threads details
        if thread.related_threads:
            related = session.execute(text(f"""
                SELECT {THREAD_FIELDS}
                FROM threads t WHERE t.id = ANY(:ids)
                ORDER BY t.importance_score DESC
            """), {"ids": thread.related_threads}).fetchall()
            result["related"] = [thread_to_dict(r) for r in related]

        return result


@router.get("/threads/{thread_id}/related", response_model=RelatedThreadsResponse)
def get_related_threads(thread_id: int):
    """Get threads related to this one (cross-country)."""
    with get_session() as session:
        thread = session.execute(text("""
            SELECT thread_key, country_code, related_threads FROM threads WHERE id = :tid
        """), {"tid": thread_id}).fetchone()

        if not thread:
            raise HTTPException(status_code=404, detail="Thread not found")

        # By explicit links
        related_ids = thread.related_threads or []

        # Also find by similarity
        similar = session.execute(text(f"""
            SELECT {THREAD_FIELDS}
            FROM threads t
            WHERE t.id != :tid
              AND (
                  t.id = ANY(:related_ids)
                  OR similarity(t.thread_key, :key) > 0.3
              )
            ORDER BY t.importance_score DESC
            LIMIT 10
        """), {
            "tid": thread_id,
            "related_ids": related_ids,
            "key": thread.thread_key,
        }).fetchall()

        return {"related": [thread_to_dict(r) for r in similar]}


@router.post("/threads/merge")
def merge_threads(thread_ids: list[int]):
    """Manually merge threads. First id becomes primary."""
    if len(thread_ids) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 thread IDs")

    primary_id = thread_ids[0]

    with get_session() as session:
        # Verify all exist
        threads = session.execute(text(f"""
            SELECT {THREAD_FIELDS}
            FROM threads t WHERE t.id = ANY(:ids)
        """), {"ids": thread_ids}).fetchall()

        if len(threads) != len(thread_ids):
            raise HTTPException(status_code=404, detail="Some threads not found")

        # Move all articles to primary
        for tid in thread_ids[1:]:
            session.execute(text("""
                UPDATE thread_articles SET thread_id = :primary
                WHERE thread_id = :old
                  AND article_id NOT IN (SELECT article_id FROM thread_articles WHERE thread_id = :primary)
            """), {"primary": primary_id, "old": tid})
            session.execute(text("DELETE FROM thread_articles WHERE thread_id = :old"), {"old": tid})

        # Aggregate merged keys
        all_keys = set()
        for t in threads:
            if t.merged_keys:
                all_keys.update(t.merged_keys)
            all_keys.add(t.thread_key)

        # Update primary with aggregated data
        session.execute(text("""
            UPDATE threads SET
                article_count = (SELECT COUNT(*) FROM thread_articles WHERE thread_id = :pid),
                merged_keys = :keys,
                first_seen = (SELECT MIN(first_seen) FROM threads WHERE id = ANY(:ids)),
                last_seen = (SELECT MAX(last_seen) FROM threads WHERE id = ANY(:ids)),
                generated_at = NOW()
            WHERE id = :pid
        """), {"pid": primary_id, "keys": list(all_keys), "ids": thread_ids})

        # Delete secondary threads
        for tid in thread_ids[1:]:
            session.execute(text("DELETE FROM threads WHERE id = :tid"), {"tid": tid})

        return {
            "message": f"Merged {len(thread_ids)} threads into #{primary_id}",
            "primary_id": primary_id,
            "deleted": thread_ids[1:],
        }


@router.get("/countries/{code}/threads", response_model=CountryThreadsResponse)
def get_country_threads(
    code: str,
    status: Optional[str] = Query(default=None),
    limit: int = Query(default=15, le=50),
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
            SELECT {THREAD_FIELDS}
            FROM threads t
            {where}
            ORDER BY t.importance_score DESC
            LIMIT :lim
        """), params).fetchall()

        return {
            "country": code,
            "name": COUNTRY_NAMES.get(code, code),
            "threads": [thread_to_dict(r) for r in rows],
        }
# Append to threads.py
