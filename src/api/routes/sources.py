"""CRUD API for managing sources."""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
import feedparser
from bs4 import BeautifulSoup
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import text

from src.db import get_session, Source

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/sources", tags=["sources"])

_CACHE_KEY = "cache:v1:sources"
_CACHE_TTL = 300


def _cache_get(key):
    try:
        from src.queue import get_redis
        v = get_redis().get(key)
        return json.loads(v) if v else None
    except Exception:
        return None


def _cache_set(key, payload, ttl):
    try:
        from src.queue import get_redis
        get_redis().setex(key, ttl, json.dumps(payload))
    except Exception:
        pass


# ---------- Schemas ----------
class SourceCreate(BaseModel):
    name: str
    url: str
    country_code: str
    source_type: str = "rss"
    weight: float = 1.0
    language: str = "ru"
    config: dict = {}
    active: bool = True
    tier: str = "mainstream"

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL должен начинаться с http:// или https://")
        return v

    @field_validator("source_type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        if v not in ("rss", "web", "telegram"):
            raise ValueError("Тип должен быть rss, web или telegram")
        return v

    @field_validator("country_code")
    @classmethod
    def validate_cc(cls, v: str) -> str:
        allowed = {"KZ", "AM", "UZ", "KG", "TJ", "TM", "AZ", "GE", "MD", "BY"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"Код страны должен быть одним из: {', '.join(sorted(allowed))}")
        return v


class SourceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    country_code: Optional[str] = None
    source_type: Optional[str] = None
    weight: Optional[float] = None
    language: Optional[str] = None
    config: Optional[dict] = None
    active: Optional[bool] = None
    tier: Optional[str] = None


# ---------- Helpers ----------
def source_to_dict(row) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "url": row.url,
        "country_code": row.country_code,
        "source_type": row.source_type,
        "weight": float(row.weight) if row.weight else 1.0,
        "language": row.language,
        "config": row.config or {},
        "active": row.active,
        "tier": getattr(row, "tier", "mainstream"),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


# ---------- Routes ----------
@router.get("")
def list_sources():
    """List all sources with article stats (cached 5 min)."""
    cached = _cache_get(_CACHE_KEY)
    if cached is not None:
        return cached

    with get_session() as session:
        sources = session.execute(text("""
            WITH art AS (
                SELECT source_id, COUNT(*) AS article_count, MAX(collected_at) AS last_collected
                FROM articles GROUP BY source_id
            ),
            rel AS (
                SELECT a.source_id, COUNT(*) AS relevant_count, AVG(an.sentiment) AS avg_sentiment
                FROM analysis an JOIN articles a ON an.article_id = a.id
                WHERE an.is_relevant = true
                GROUP BY a.source_id
            )
            SELECT s.*,
                   COALESCE(art.article_count, 0) AS article_count,
                   art.last_collected,
                   COALESCE(rel.relevant_count, 0) AS relevant_count,
                   rel.avg_sentiment
            FROM sources s
            LEFT JOIN art ON art.source_id = s.id
            LEFT JOIN rel ON rel.source_id = s.id
            ORDER BY s.country_code, s.name
        """)).fetchall()

        payload = {
            "sources": [
                {
                    "id": r.id,
                    "name": r.name,
                    "url": r.url,
                    "country_code": r.country_code,
                    "source_type": r.source_type,
                    "weight": float(r.weight) if r.weight else 1.0,
                    "language": r.language,
                    "config": r.config or {},
                    "active": r.active,
                    "tier": getattr(r, "tier", "mainstream"),
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "article_count": r.article_count,
                    "last_collected": r.last_collected.isoformat() if r.last_collected else None,
                    "relevant_count": r.relevant_count,
                    "avg_sentiment": float(r.avg_sentiment) if r.avg_sentiment is not None else None,
                }
                for r in sources
            ]
        }

    _cache_set(_CACHE_KEY, payload, _CACHE_TTL)
    return payload


@router.get("/{source_id}")
def get_source(source_id: int):
    """Get source details."""
    with get_session() as session:
        row = session.query(Source).filter(Source.id == source_id).first()
        if not row:
            raise HTTPException(status_code=404, detail="Источник не найден")

        # Stats
        stats = session.execute(text("""
            SELECT COUNT(*) as total,
                   COUNT(*) FILTER (WHERE a.collected_at > NOW() - INTERVAL '24 hours') as last_24h,
                   MIN(a.collected_at) as first_collected,
                   MAX(a.collected_at) as last_collected
            FROM articles a WHERE a.source_id = :sid
        """), {"sid": source_id}).fetchone()

        rel = session.execute(text("""
            SELECT COUNT(*) as relevant_count,
                   AVG(an.sentiment) as avg_sentiment
            FROM analysis an
            JOIN articles a ON an.article_id = a.id
            WHERE a.source_id = :sid AND an.is_relevant = true
        """), {"sid": source_id}).fetchone()

        days_active = 1
        if stats.first_collected and stats.last_collected:
            delta = stats.last_collected - stats.first_collected
            days_active = max(delta.days, 1)

        result = source_to_dict(row)
        result.update({
            "article_count": stats.total,
            "articles_last_24h": stats.last_24h,
            "first_collected": stats.first_collected.isoformat() if stats.first_collected else None,
            "last_collected": stats.last_collected.isoformat() if stats.last_collected else None,
            "avg_articles_per_day": round(stats.total / days_active, 1),
            "relevant_count": rel.relevant_count if rel else 0,
            "avg_sentiment": float(rel.avg_sentiment) if rel and rel.avg_sentiment is not None else None,
            "relevance_pct": round(rel.relevant_count / stats.total * 100, 1) if stats.total > 0 and rel else 0,
        })
        return result


@router.post("")
def create_source(data: SourceCreate):
    """Create a new source."""
    with get_session() as session:
        # Check duplicate URL
        existing = session.execute(
            text("SELECT id FROM sources WHERE url = :url"),
            {"url": data.url},
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Источник с таким URL уже существует")

        source = Source(
            name=data.name,
            url=data.url,
            country_code=data.country_code,
            source_type=data.source_type,
            weight=data.weight,
            language=data.language,
            config=data.config,
            active=data.active,
            tier=data.tier,
        )
        session.add(source)
        session.flush()
        return {"message": "Источник создан", "source": source_to_dict(source)}


@router.put("/{source_id}")
def update_source(source_id: int, data: SourceUpdate):
    """Update a source."""
    with get_session() as session:
        source = session.query(Source).filter(Source.id == source_id).first()
        if not source:
            raise HTTPException(status_code=404, detail="Источник не найден")

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(source, key, value)

        session.flush()
        return {"message": "Источник обновлён", "source": source_to_dict(source)}


@router.delete("/{source_id}")
def delete_source(source_id: int):
    """Delete a source and its articles."""
    with get_session() as session:
        source = session.query(Source).filter(Source.id == source_id).first()
        if not source:
            raise HTTPException(status_code=404, detail="Источник не найден")

        name = source.name
        # Delete related data
        session.execute(text("""
            DELETE FROM analysis WHERE article_id IN (
                SELECT id FROM articles WHERE source_id = :sid
            )
        """), {"sid": source_id})
        session.execute(text("DELETE FROM articles WHERE source_id = :sid"), {"sid": source_id})
        session.delete(source)
        return {"message": f"Источник «{name}» удалён"}


@router.patch("/{source_id}/toggle")
def toggle_source(source_id: int):
    """Toggle source active status."""
    with get_session() as session:
        source = session.query(Source).filter(Source.id == source_id).first()
        if not source:
            raise HTTPException(status_code=404, detail="Источник не найден")

        source.active = not source.active
        session.flush()
        status = "включён" if source.active else "отключён"
        return {"message": f"Источник «{source.name}» {status}", "active": source.active}


@router.post("/{source_id}/test")
def test_source(source_id: int):
    """Test-collect one article from the source."""
    with get_session() as session:
        source = session.query(Source).filter(Source.id == source_id).first()
        if not source:
            raise HTTPException(status_code=404, detail="Источник не найден")

    return _test_url(source.url, source.source_type, source.name)


@router.post("/test-url")
def test_url(data: SourceCreate):
    """Test-collect from a URL without saving."""
    return _test_url(data.url, data.source_type, data.name)


def _test_url(url: str, source_type: str, name: str) -> dict:
    """Attempt to collect one article from the given URL."""
    try:
        if source_type == "rss":
            response = httpx.get(url, headers={"User-Agent": "CIS-Thermometer/1.0"}, timeout=15.0, follow_redirects=True)
            response.raise_for_status()
            feed = feedparser.parse(response.text)
            if feed.entries:
                entry = feed.entries[0]
                body = ""
                if hasattr(entry, "content") and entry.content:
                    body = entry.content[0].get("value", "")
                elif hasattr(entry, "summary"):
                    body = entry.summary or ""
                if body:
                    body = BeautifulSoup(body, "lxml").get_text(separator=" ", strip=True)
                return {
                    "success": True,
                    "message": f"Найдено {len(feed.entries)} записей в RSS",
                    "sample": {
                        "title": getattr(entry, "title", ""),
                        "url": getattr(entry, "link", ""),
                        "body": body[:500],
                    },
                }
            else:
                return {"success": False, "message": "RSS-лента пуста или не распознана"}

        elif source_type == "web":
            response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15.0, follow_redirects=True)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "lxml")
            links = []
            for a in soup.find_all("a", href=True):
                title = a.get_text(strip=True)
                if title and len(title) >= 20:
                    links.append({"title": title[:200], "url": a["href"]})
            if links:
                return {
                    "success": True,
                    "message": f"Найдено {len(links)} ссылок на статьи",
                    "sample": links[0],
                }
            return {"success": False, "message": "Не удалось найти ссылки на статьи"}

        else:
            return {"success": False, "message": f"Тип '{source_type}' пока не поддерживается для тестирования"}

    except httpx.HTTPError as e:
        return {"success": False, "message": f"HTTP ошибка: {e}"}
    except Exception as e:
        return {"success": False, "message": f"Ошибка: {e}"}
