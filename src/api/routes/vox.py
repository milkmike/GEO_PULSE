"""
VOX POPULI — API Routes

Эндпоинты для народной температуры, комментариев и elite gap.
"""

from fastapi import APIRouter, Query
from ..main import get_conn

router = APIRouter(prefix="/api/v1/vox", tags=["vox"])


@router.get("")
def vox_overview(days: int = Query(default=7, ge=1, le=90)):
    """Обзор народной температуры по всем странам."""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT ON (country_code)
            country_code, temperature, comment_count, unique_authors,
            bot_ratio, elite_gap, media_temperature, dominant_emotion,
            pro_ratio, anti_ratio, time
        FROM vox_temperature
        ORDER BY country_code, time DESC
    """)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    countries = [dict(zip(cols, r)) for r in rows]

    # Общая статистика
    cur.execute("""
        SELECT COUNT(*) as total_comments,
               COUNT(DISTINCT author_hash) as unique_authors,
               COUNT(*) FILTER (WHERE ca.bot_score >= 0.5) as bot_comments
        FROM comments c
        LEFT JOIN comment_analysis ca ON ca.comment_id = c.id
        WHERE c.published_at >= NOW() - make_interval(days => %s)
    """, (days,))
    stats_row = cur.fetchone()

    conn.close()

    return {
        "countries": [{
            "code": c["country_code"],
            "vox_temperature": float(c["temperature"]) if c["temperature"] else None,
            "media_temperature": float(c["media_temperature"]) if c["media_temperature"] else None,
            "elite_gap": float(c["elite_gap"]) if c["elite_gap"] else None,
            "comment_count": c["comment_count"],
            "unique_authors": c["unique_authors"],
            "bot_ratio": float(c["bot_ratio"]) if c["bot_ratio"] else 0,
            "dominant_emotion": c["dominant_emotion"],
            "pro_ratio": float(c["pro_ratio"]) if c["pro_ratio"] else 0,
            "anti_ratio": float(c["anti_ratio"]) if c["anti_ratio"] else 0,
            "updated_at": c["time"].isoformat() if c["time"] else None,
        } for c in countries],
        "stats": {
            "total_comments": stats_row[0] if stats_row else 0,
            "unique_authors": stats_row[1] if stats_row else 0,
            "bot_comments": stats_row[2] if stats_row else 0,
            "period_days": days,
        }
    }


@router.get("/countries/{code}")
def vox_country(code: str, days: int = Query(default=14, ge=1, le=90)):
    """Детали народной температуры для конкретной страны."""
    code = code.upper()
    conn = get_conn()
    cur = conn.cursor()

    # Временной ряд
    cur.execute("""
        SELECT time, temperature, comment_count, unique_authors,
               bot_ratio, elite_gap, media_temperature,
               dominant_emotion, pro_ratio, anti_ratio
        FROM vox_temperature
        WHERE country_code = %s
          AND time >= NOW() - make_interval(days => %s)
        ORDER BY time
    """, (code, days))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    timeline = [dict(zip(cols, r)) for r in rows]

    # Последние комментарии
    cur.execute("""
        SELECT c.id, c.text, c.published_at, c.platform, c.likes,
               ca.sentiment, ca.emotion, ca.stance_russia, ca.bot_score, ca.topics
        FROM comments c
        JOIN comment_analysis ca ON ca.comment_id = c.id
        WHERE c.country_code = %s
          AND c.published_at >= NOW() - make_interval(days => %s)
          AND ca.bot_score < 0.5
        ORDER BY c.published_at DESC
        LIMIT 50
    """, (code, days))
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    comments = [dict(zip(cols, r)) for r in rows]

    # Эмоциональный профиль
    cur.execute("""
        SELECT ca.emotion, COUNT(*) as cnt
        FROM comments c
        JOIN comment_analysis ca ON ca.comment_id = c.id
        WHERE c.country_code = %s
          AND c.published_at >= NOW() - make_interval(days => %s)
          AND ca.bot_score < 0.5
        GROUP BY ca.emotion
        ORDER BY cnt DESC
    """, (code, days))
    emotions = {r[0]: r[1] for r in cur.fetchall()}

    # Топ темы
    cur.execute("""
        SELECT topic, COUNT(*) as cnt
        FROM (
            SELECT UNNEST(ca.topics) as topic
            FROM comments c
            JOIN comment_analysis ca ON ca.comment_id = c.id
            WHERE c.country_code = %s
              AND c.published_at >= NOW() - make_interval(days => %s)
              AND ca.bot_score < 0.5
        ) t
        GROUP BY topic
        ORDER BY cnt DESC
        LIMIT 15
    """, (code, days))
    top_topics = [{"topic": r[0], "count": r[1]} for r in cur.fetchall()]

    conn.close()

    return {
        "country": code,
        "days": days,
        "timeline": [{
            "time": t["time"].isoformat(),
            "vox_temperature": float(t["temperature"]) if t["temperature"] else None,
            "media_temperature": float(t["media_temperature"]) if t["media_temperature"] else None,
            "elite_gap": float(t["elite_gap"]) if t["elite_gap"] else None,
            "comment_count": t["comment_count"],
            "dominant_emotion": t["dominant_emotion"],
        } for t in timeline],
        "emotions": emotions,
        "top_topics": top_topics,
        "recent_comments": [{
            "id": c["id"],
            "text": c["text"][:300],
            "published_at": c["published_at"].isoformat(),
            "platform": c["platform"],
            "likes": c["likes"],
            "sentiment": float(c["sentiment"]),
            "emotion": c["emotion"],
            "stance": c["stance_russia"],
            "topics": c["topics"] or [],
        } for c in comments],
    }


@router.get("/elite-gap")
def elite_gap(days: int = Query(default=7, ge=1, le=90)):
    """Elite Gap — разница между медийной и народной температурой по странам."""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT DISTINCT ON (country_code)
            country_code, temperature as vox_temp,
            media_temperature as media_temp,
            elite_gap, comment_count, unique_authors, time
        FROM vox_temperature
        WHERE elite_gap IS NOT NULL
        ORDER BY country_code, time DESC
    """)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    data = [dict(zip(cols, r)) for r in rows]

    conn.close()

    return {
        "countries": sorted([{
            "code": d["country_code"],
            "vox_temperature": float(d["vox_temp"]) if d["vox_temp"] else None,
            "media_temperature": float(d["media_temp"]) if d["media_temp"] else None,
            "elite_gap": float(d["elite_gap"]) if d["elite_gap"] else None,
            "gap_direction": "народ холоднее" if d["elite_gap"] and float(d["elite_gap"]) > 0 else "народ теплее",
            "comment_count": d["comment_count"],
            "unique_authors": d["unique_authors"],
        } for d in data], key=lambda x: abs(x["elite_gap"] or 0), reverse=True)
    }


@router.get("/channels")
def vox_channels():
    """Список каналов для мониторинга комментариев."""
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT vc.id, vc.platform, vc.channel_username, vc.country_code,
               vc.name, vc.active, vc.last_collected,
               vc.discussion_id IS NOT NULL as has_discussion,
               COUNT(c.id) as total_comments
        FROM vox_channels vc
        LEFT JOIN comments c ON c.channel_id = vc.channel_id_ext::text
                             AND c.platform = vc.platform
        GROUP BY vc.id
        ORDER BY vc.country_code, vc.name
    """)
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]

    conn.close()

    return {
        "channels": [dict(zip(cols, r)) for r in rows]
    }
