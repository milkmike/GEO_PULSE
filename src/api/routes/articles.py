"""
Articles Feed — API Routes

Лента статей с фильтрами по стране, источнику, типу.
"""

from fastapi import APIRouter, Query
from sqlalchemy import text

from src.db import get_session

router = APIRouter(prefix="/api/v1/articles", tags=["articles"])


@router.get("/feed")
def articles_feed(
    country: str = Query(default=None, description="Country code filter (e.g. KZ)"),
    source_type: str = Query(default=None, description="Source type: telegram, rss, web"),
    source_id: int = Query(default=None, description="Specific source ID"),
    search: str = Query(default=None, description="Search in title/body"),
    days: int = Query(default=3, ge=1, le=30),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Лента статей с фильтрами."""
    with get_session() as session:
        conditions = ["a.published_at >= NOW() - make_interval(days => :days)", "a.is_backfill = false"]
        params = {"days": days, "limit": limit, "offset": offset}

        if country:
            conditions.append("s.country_code = :country")
            params["country"] = country.upper()
        if source_type:
            conditions.append("s.source_type = :source_type")
            params["source_type"] = source_type
        if source_id:
            conditions.append("a.source_id = :source_id")
            params["source_id"] = source_id
        if search:
            conditions.append("(a.title ILIKE :search OR a.body ILIKE :search)")
            params["search"] = f"%{search}%"

        where = " AND ".join(conditions)

        rows = session.execute(text(f"""
            SELECT a.id, a.title, a.body, a.url, a.published_at, a.language,
                   a.is_duplicate,
                   s.name as source_name, s.country_code, s.source_type, s.tier
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE {where}
              AND a.is_duplicate = false
            ORDER BY a.published_at DESC
            LIMIT :limit OFFSET :offset
        """), params).fetchall()

        total = session.execute(text(f"""
            SELECT COUNT(*) FROM articles a
            JOIN sources s ON s.id = a.source_id
            WHERE {where} AND a.is_duplicate = false
        """), params).scalar()

        return {
            "articles": [{
                "id": r.id,
                "title": r.title[:300] if r.title else "",
                "body": (r.body[:500] + "...") if r.body and len(r.body) > 500 else (r.body or ""),
                "url": r.url,
                "published_at": r.published_at.isoformat() if r.published_at else None,
                "language": r.language,
                "source_name": r.source_name,
                "country_code": r.country_code.strip() if r.country_code else "",
                "source_type": r.source_type,
                "tier": r.tier,
            } for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@router.get("/feed/sources")
def articles_sources():
    """Список источников для фильтрации."""
    with get_session() as session:
        rows = session.execute(text("""
            SELECT s.id, s.name, s.country_code, s.source_type, s.tier, s.url,
                   COUNT(a.id) as article_count,
                   MAX(a.published_at) as last_article
            FROM sources s
            LEFT JOIN articles a ON a.source_id = s.id
                AND a.published_at >= NOW() - interval '7 days'
            WHERE s.active = true
            GROUP BY s.id
            ORDER BY s.country_code, s.source_type, s.name
        """)).fetchall()

        return {
            "sources": [{
                "id": r.id,
                "name": r.name,
                "country_code": r.country_code.strip(),
                "source_type": r.source_type,
                "tier": r.tier,
                "url": r.url,
                "article_count_7d": r.article_count,
                "last_article": r.last_article.isoformat() if r.last_article else None,
            } for r in rows]
        }


@router.get("/{article_id}")
def article_detail(article_id: int):
    """Полный текст статьи."""
    with get_session() as session:
        row = session.execute(text("""
            SELECT a.id, a.title, a.body, a.url, a.published_at, a.language,
                   a.external_id,
                   s.name as source_name, s.country_code, s.source_type, s.tier, s.url as source_url,
                   an.sentiment, an.action_level, an.topics, an.summary as ai_summary,
                   an.narrative_alignment
            FROM articles a
            JOIN sources s ON s.id = a.source_id
            LEFT JOIN analyses an ON an.article_id = a.id
            WHERE a.id = :id
        """), {"id": article_id}).fetchone()

        if not row:
            return {"error": "Article not found"}

        return {
            "id": row.id,
            "title": row.title,
            "body": row.body,
            "url": row.url,
            "published_at": row.published_at.isoformat() if row.published_at else None,
            "language": row.language,
            "source_name": row.source_name,
            "country_code": row.country_code.strip() if row.country_code else "",
            "source_type": row.source_type,
            "tier": row.tier,
            "source_url": row.source_url,
            "analysis": {
                "sentiment": float(row.sentiment) if row.sentiment else None,
                "action_level": row.action_level,
                "topics": list(row.topics) if row.topics else [],
                "summary": row.ai_summary,
                "narrative_alignment": row.narrative_alignment,
            } if row.sentiment else None,
        }
