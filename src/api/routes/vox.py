"""
VOX POPULI — API Routes

Эндпоинты для народной температуры, комментариев и elite gap.
"""

from fastapi import APIRouter, Query
from sqlalchemy import text

from src.db import get_session

router = APIRouter(prefix="/api/v1/vox", tags=["vox"])


@router.get("")
def vox_overview(days: int = Query(default=30, ge=1, le=999)):
    """Обзор народной температуры по всем странам."""
    with get_session() as session:
        rows = session.execute(text("""
            SELECT DISTINCT ON (country_code)
                country_code, temperature, comment_count, unique_authors,
                bot_ratio, elite_gap, media_temperature, dominant_emotion,
                pro_ratio, anti_ratio, time
            FROM vox_temperature
            ORDER BY country_code, time DESC
        """)).fetchall()

        countries = [{
            "code": r.country_code,
            "vox_temperature": float(r.temperature) if r.temperature else None,
            "media_temperature": float(r.media_temperature) if r.media_temperature else None,
            "elite_gap": float(r.elite_gap) if r.elite_gap else None,
            "comment_count": r.comment_count,
            "unique_authors": r.unique_authors,
            "bot_ratio": float(r.bot_ratio) if r.bot_ratio else 0,
            "dominant_emotion": r.dominant_emotion,
            "pro_ratio": float(r.pro_ratio) if r.pro_ratio else 0,
            "anti_ratio": float(r.anti_ratio) if r.anti_ratio else 0,
            "updated_at": r.time.isoformat() if r.time else None,
        } for r in rows]

        stats = session.execute(text("""
            SELECT COUNT(*) as total_comments,
                   COUNT(DISTINCT author_hash) as unique_authors,
                   COUNT(*) FILTER (WHERE ca.bot_score >= 0.5) as bot_comments
            FROM comments c
            LEFT JOIN comment_analysis ca ON ca.comment_id = c.id
            WHERE c.published_at >= NOW() - make_interval(days => :days)
        """), {"days": days}).fetchone()

        return {
            "countries": countries,
            "stats": {
                "total_comments": stats.total_comments if stats else 0,
                "unique_authors": stats.unique_authors if stats else 0,
                "bot_comments": stats.bot_comments if stats else 0,
                "period_days": days,
            }
        }


@router.get("/countries/{code}")
def vox_country(code: str, days: int = Query(default=14, ge=1, le=90)):
    """Детали народной температуры для конкретной страны."""
    code = code.upper()
    with get_session() as session:
        # Временной ряд
        timeline = session.execute(text("""
            SELECT time, temperature, comment_count, unique_authors,
                   bot_ratio, elite_gap, media_temperature,
                   dominant_emotion, pro_ratio, anti_ratio
            FROM vox_temperature
            WHERE country_code = :code
              AND time >= NOW() - make_interval(days => :days)
            ORDER BY time
        """), {"code": code, "days": days}).fetchall()

        # Последние комментарии
        comments = session.execute(text("""
            SELECT c.id, c.text, c.published_at, c.platform, c.likes,
                   ca.sentiment, ca.emotion, ca.stance_russia, ca.bot_score, ca.topics
            FROM comments c
            JOIN comment_analysis ca ON ca.comment_id = c.id
            WHERE c.country_code = :code
              AND c.published_at >= NOW() - make_interval(days => :days)
              AND ca.bot_score < 0.5
            ORDER BY c.published_at DESC
            LIMIT 50
        """), {"code": code, "days": days}).fetchall()

        # Эмоциональный профиль
        emotions_rows = session.execute(text("""
            SELECT ca.emotion, COUNT(*) as cnt
            FROM comments c
            JOIN comment_analysis ca ON ca.comment_id = c.id
            WHERE c.country_code = :code
              AND c.published_at >= NOW() - make_interval(days => :days)
              AND ca.bot_score < 0.5
            GROUP BY ca.emotion
            ORDER BY cnt DESC
        """), {"code": code, "days": days}).fetchall()
        emotions = {r.emotion: r.cnt for r in emotions_rows}

        # Топ темы
        topics_rows = session.execute(text("""
            SELECT topic, COUNT(*) as cnt
            FROM (
                SELECT UNNEST(ca.topics) as topic
                FROM comments c
                JOIN comment_analysis ca ON ca.comment_id = c.id
                WHERE c.country_code = :code
                  AND c.published_at >= NOW() - make_interval(days => :days)
                  AND ca.bot_score < 0.5
            ) t
            GROUP BY topic
            ORDER BY cnt DESC
            LIMIT 15
        """), {"code": code, "days": days}).fetchall()
        top_topics = [{"topic": r.topic, "count": r.cnt} for r in topics_rows]

        return {
            "country": code,
            "days": days,
            "timeline": [{
                "time": t.time.isoformat(),
                "vox_temperature": float(t.temperature) if t.temperature else None,
                "media_temperature": float(t.media_temperature) if t.media_temperature else None,
                "elite_gap": float(t.elite_gap) if t.elite_gap else None,
                "comment_count": t.comment_count,
                "dominant_emotion": t.dominant_emotion,
            } for t in timeline],
            "emotions": emotions,
            "top_topics": top_topics,
            "recent_comments": [{
                "id": c.id,
                "text": c.text[:300],
                "published_at": c.published_at.isoformat(),
                "platform": c.platform,
                "likes": c.likes,
                "sentiment": float(c.sentiment),
                "emotion": c.emotion,
                "stance": c.stance_russia,
                "topics": list(c.topics) if c.topics else [],
            } for c in comments],
        }


@router.get("/elite-gap")
def elite_gap(days: int = Query(default=30, ge=1, le=999)):
    """Elite Gap — разница между медийной и народной температурой по странам."""
    with get_session() as session:
        rows = session.execute(text("""
            SELECT DISTINCT ON (country_code)
                country_code, temperature as vox_temp,
                media_temperature as media_temp,
                elite_gap, comment_count, unique_authors, time
            FROM vox_temperature
            WHERE elite_gap IS NOT NULL
            ORDER BY country_code, time DESC
        """)).fetchall()

        return {
            "countries": sorted([{
                "code": d.country_code,
                "vox_temperature": float(d.vox_temp) if d.vox_temp else None,
                "media_temperature": float(d.media_temp) if d.media_temp else None,
                "elite_gap": float(d.elite_gap) if d.elite_gap else None,
                "gap_direction": "народ холоднее" if d.elite_gap and float(d.elite_gap) > 0 else "народ теплее",
                "comment_count": d.comment_count,
                "unique_authors": d.unique_authors,
            } for d in rows], key=lambda x: abs(x["elite_gap"] or 0), reverse=True)
        }


@router.get("/channels")
def vox_channels():
    """Список каналов для мониторинга комментариев."""
    with get_session() as session:
        rows = session.execute(text("""
            SELECT vc.id, vc.platform, vc.channel_username, vc.country_code,
                   vc.name, vc.active, vc.last_collected,
                   vc.discussion_id IS NOT NULL as has_discussion,
                   COUNT(c.id) as total_comments
            FROM vox_channels vc
            LEFT JOIN comments c ON c.channel_id = vc.channel_id_ext::text
                                 AND c.platform = vc.platform
            GROUP BY vc.id
            ORDER BY vc.country_code, vc.name
        """)).fetchall()

        return {
            "channels": [{
                "id": r.id,
                "platform": r.platform,
                "channel_username": r.channel_username,
                "country_code": r.country_code,
                "name": r.name,
                "active": r.active,
                "last_collected": r.last_collected.isoformat() if r.last_collected else None,
                "has_discussion": r.has_discussion,
                "total_comments": r.total_comments,
            } for r in rows]
        }


@router.get("/comments")
def vox_comments_feed(
    country: str = Query(default=None),
    days: int = Query(default=30, ge=1, le=999),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """Feed of comments from all channels."""
    with get_session() as session:
        conditions = ["c.published_at >= NOW() - make_interval(days => :days)"]
        params = {"days": days, "limit": limit, "offset": offset}

        if country:
            conditions.append("c.country_code = :country")
            params["country"] = country.upper()

        where = " AND ".join(conditions)

        rows = session.execute(text(
            f"SELECT c.id, c.text, c.published_at, c.platform, c.country_code, "
            f"c.likes, c.author_hash, c.channel_id, "
            f"ca.sentiment, ca.emotion, ca.stance_russia, ca.bot_score, ca.topics "
            f"FROM comments c "
            f"LEFT JOIN comment_analysis ca ON ca.comment_id = c.id "
            f"WHERE {where} "
            f"ORDER BY c.published_at DESC "
            f"LIMIT :limit OFFSET :offset"
        ), params).fetchall()

        total = session.execute(text(
            f"SELECT COUNT(*) FROM comments c WHERE {where}"
        ), params).scalar()

        result_comments = []
        for r in rows:
            result_comments.append({
                "id": r.id,
                "text": r.text[:500] if r.text else "",
                "published_at": r.published_at.isoformat() if r.published_at else None,
                "platform": r.platform,
                "country_code": r.country_code.strip() if r.country_code else "",
                "likes": r.likes or 0,
                "channel_id": r.channel_id,
                "sentiment": float(r.sentiment) if r.sentiment else None,
                "emotion": r.emotion,
                "stance": r.stance_russia,
                "bot_score": float(r.bot_score) if r.bot_score else None,
                "topics": list(r.topics) if r.topics else [],
            })

        return {
            "comments": result_comments,
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@router.get("/insights")
def vox_insights(
    country: str = Query(default=None),
    days: int = Query(default=30, ge=1, le=999),
):
    """Aggregated insights: emotions, topics cloud, stance distribution."""
    with get_session() as session:
        conditions = ["c.published_at >= NOW() - make_interval(days => :days)"]
        params = {"days": days}
        if country:
            conditions.append("c.country_code = :country")
            params["country"] = country.upper()
        where = " AND ".join(conditions)

        # Emotion distribution
        emotions = session.execute(text(
            f"SELECT ca.emotion, COUNT(*) as cnt "
            f"FROM comment_analysis ca "
            f"JOIN comments c ON c.id = ca.comment_id "
            f"WHERE {where} AND ca.emotion IS NOT NULL "
            f"GROUP BY ca.emotion ORDER BY cnt DESC"
        ), params).fetchall()

        # Stance distribution
        stances = session.execute(text(
            f"SELECT ca.stance_russia, COUNT(*) as cnt "
            f"FROM comment_analysis ca "
            f"JOIN comments c ON c.id = ca.comment_id "
            f"WHERE {where} AND ca.stance_russia IS NOT NULL "
            f"GROUP BY ca.stance_russia ORDER BY cnt DESC"
        ), params).fetchall()

        # Topic cloud
        topics = session.execute(text(
            f"SELECT t.topic, COUNT(*) as cnt "
            f"FROM comment_analysis ca "
            f"JOIN comments c ON c.id = ca.comment_id "
            f"CROSS JOIN LATERAL unnest(ca.topics) as t(topic) "
            f"WHERE {where} "
            f"GROUP BY t.topic ORDER BY cnt DESC LIMIT 40"
        ), params).fetchall()

        # Sentiment histogram
        sent_sql = (
            f"SELECT "
            f"  CASE "
            f"    WHEN ca.sentiment < -1.5 THEN 'very_negative' "
            f"    WHEN ca.sentiment < -0.3 THEN 'negative' "
            f"    WHEN ca.sentiment <= 0.3 THEN 'neutral' "
            f"    WHEN ca.sentiment <= 1.5 THEN 'positive' "
            f"    ELSE 'very_positive' "
            f"  END as bucket, "
            f"  COUNT(*) as cnt "
            f"FROM comment_analysis ca "
            f"JOIN comments c ON c.id = ca.comment_id "
            f"WHERE {where} AND ca.sentiment IS NOT NULL "
            f"GROUP BY bucket"
        )
        sentiments = session.execute(text(sent_sql), params).fetchall()

        # Sample comments per emotion (3 each)
        emotion_samples_raw = session.execute(text(
            f"SELECT ca.emotion, c.text, ca.sentiment, c.country_code "
            f"FROM comment_analysis ca "
            f"JOIN comments c ON c.id = ca.comment_id "
            f"WHERE {where} AND ca.emotion IS NOT NULL "
            f"ORDER BY c.published_at DESC "
            f"LIMIT 100"
        ), params).fetchall()

        emotion_samples = {}
        for r in emotion_samples_raw:
            em = r.emotion
            if em not in emotion_samples:
                emotion_samples[em] = []
            if len(emotion_samples[em]) < 3:
                emotion_samples[em].append({
                    "text": r.text[:200] if r.text else "",
                    "sentiment": float(r.sentiment) if r.sentiment else 0,
                    "country": r.country_code.strip() if r.country_code else "",
                })

        # Total analyzed
        total_analyzed = session.execute(text(
            f"SELECT COUNT(*) FROM comment_analysis ca "
            f"JOIN comments c ON c.id = ca.comment_id "
            f"WHERE {where}"
        ), params).scalar()

        total_comments = session.execute(text(
            f"SELECT COUNT(*) FROM comments c WHERE {where}"
        ), params).scalar()

        # Language distribution (comments)
        comment_langs = session.execute(text(
            f"SELECT c.language, COUNT(*) as cnt "
            f"FROM comments c "
            f"WHERE {where} AND c.language IS NOT NULL "
            f"GROUP BY c.language ORDER BY cnt DESC"
        ), params).fetchall()

        # Language distribution (articles)
        art_params = dict(params)
        art_lang_cond = "a.published_at >= NOW() - make_interval(days => :days)"
        if country:
            art_lang_cond += " AND s.country_code = :country"
        article_langs = session.execute(text(
            f"SELECT a.language, s.country_code, COUNT(*) as cnt "
            f"FROM articles a "
            f"JOIN sources s ON s.id = a.source_id "
            f"WHERE {art_lang_cond} "
            f"AND a.language IS NOT NULL "
            f"GROUP BY a.language, s.country_code ORDER BY cnt DESC"
        ), art_params).fetchall()

        # Language by country summary
        lang_by_country = {}
        for r in article_langs:
            cc = r.country_code.strip() if r.country_code else ""
            if cc not in lang_by_country:
                lang_by_country[cc] = {}
            lang_by_country[cc][r.language] = r.cnt

        return {
            "total_comments": total_comments,
            "total_analyzed": total_analyzed,
            "emotions": [{"emotion": r.emotion, "count": r.cnt} for r in emotions],
            "stances": [{"stance": r.stance_russia, "count": r.cnt} for r in stances],
            "topics": [{"topic": r.topic, "count": r.cnt} for r in topics],
            "sentiment_buckets": [{"bucket": r.bucket, "count": r.cnt} for r in sentiments],
            "emotion_samples": emotion_samples,
            "comment_languages": [{"language": r.language, "count": r.cnt} for r in comment_langs],
            "article_languages": lang_by_country,
        }
