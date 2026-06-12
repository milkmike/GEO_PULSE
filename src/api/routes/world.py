"""API v2 — world-wide Russia-relations endpoints.

Country selector → index, dossier, topics, signals, briefs, map, health.
Hot lists are served from the Redis cache warmed by the ru-index worker
(server-authoritative pattern); DB is the fallback.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from src.countries import COUNTRIES, REGIONS, country_name_ru
from src.db import get_session
from src.engine.health import health_summary, source_health
from src.entities import CATEGORIES as ENTITY_CATEGORIES, ENTITIES, get_entity
from src.pipeline.agreements import group_agreements
from src.pipeline.topics import TOPICS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v2", tags=["world"])

KNOWN_TIERS = {"official", "mainstream", "independent", "social",
               "domestic_opposition", "western_proxy", "analytics",
               # legacy v1 tier labels still present in the sources table
               "state", "opposition"}


def _cache_get(key: str):
    try:
        from src.queue import get_redis
        val = get_redis().get(key)
        return json.loads(val) if val else None
    except Exception:
        return None


@router.get("/meta")
def get_meta():
    """Registry metadata: regions, levels, topics, country list."""
    return {
        "regions": REGIONS,
        "topics": TOPICS,
        "levels": ["ally", "partner", "neutral", "cooling", "tension", "hostile"],
        "countries": [
            {"code": c["code"], "name": c["name_ru"], "name_en": c["name_en"],
             "iso3": c["iso3"], "flag": c["flag"], "region": c["region"],
             "tier": c["tier"], "memberships": c["memberships"],
             "unfriendly": c["unfriendly"],
             "sanctions_on_russia": c["sanctions_on_russia"],
             "langs": c.get("langs", [])}
            for c in COUNTRIES.values()
        ],
    }


@router.get("/countries")
def list_countries(region: Optional[str] = None, level: Optional[str] = None):
    """All countries with their current Russia Relations Index."""
    cached = _cache_get("cache:v2:countries")
    if cached:
        items = cached["countries"]
    else:
        with get_session() as session:
            rows = session.execute(
                text("""
                    SELECT DISTINCT ON (country_code)
                           country_code, score, structural, media, level,
                           delta_24h, delta_7d, article_count,
                           gdelt_volume, gdelt_tone, time
                    FROM ru_index
                    ORDER BY country_code, time DESC
                """)
            ).fetchall()
        items = []
        for r in rows:
            c = COUNTRIES.get(r.country_code)
            if not c:
                continue
            items.append({
                "code": r.country_code, "name": c["name_ru"], "name_en": c["name_en"],
                "iso3": c["iso3"], "flag": c["flag"], "region": c["region"],
                "tier": c["tier"], "score": float(r.score),
                "structural": float(r.structural) if r.structural is not None else None,
                "media": float(r.media) if r.media is not None else None,
                "level": r.level,
                "delta_24h": float(r.delta_24h) if r.delta_24h is not None else None,
                "delta_7d": float(r.delta_7d) if r.delta_7d is not None else None,
                "article_count": r.article_count,
                "gdelt_volume": float(r.gdelt_volume) if r.gdelt_volume is not None else None,
                "gdelt_tone": float(r.gdelt_tone) if r.gdelt_tone is not None else None,
                "updated_at": r.time.isoformat(),
            })

    if region:
        items = [i for i in items if i["region"] == region]
    if level:
        items = [i for i in items if i["level"] == level]

    items.sort(key=lambda i: i["score"], reverse=True)
    return {"countries": items, "total": len(items)}


@router.get("/map")
def world_map():
    """Choropleth payload: ISO3 → score/level/delta for the world map."""
    data = list_countries()
    return {
        "map": [
            {"iso3": i["iso3"], "code": i["code"], "name": i["name"],
             "score": i["score"], "level": i["level"], "delta_24h": i["delta_24h"]}
            for i in data["countries"]
        ]
    }


@router.get("/map/history")
def world_map_history(days: int = Query(90, ge=2, le=365)):
    """Per-day index scores for all countries — feeds the map timelapse."""
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT country_code, date_trunc('day', time)::date AS day,
                       round(AVG(score), 2) AS score
                FROM ru_index
                WHERE time > NOW() - make_interval(days => :days)
                GROUP BY country_code, day
                ORDER BY day
            """),
            {"days": days},
        ).fetchall()

    frames: dict[str, dict] = {}
    for r in rows:
        c = COUNTRIES.get(r.country_code)
        if not c:
            continue
        frame = frames.setdefault(r.day.isoformat(), {"iso3": [], "scores": []})
        frame["iso3"].append(c["iso3"])
        frame["scores"].append(float(r.score))

    return {
        "days": [{"day": day, **frame} for day, frame in sorted(frames.items())],
        "total_days": len(frames),
    }


@router.get("/countries/{code}/fx")
def country_fx(code: str, days: int = Query(90, ge=1, le=730)):
    """Currency series vs RUB for the country (CBR daily rates)."""
    from src.collectors.fx import COUNTRY_CURRENCY

    code = code.upper()
    if code not in COUNTRIES:
        raise HTTPException(404, f"Unknown country: {code}")
    currency = COUNTRY_CURRENCY.get(code)
    if not currency:
        return {"country_code": code, "currency": None, "series": [],
                "note": "Валюта страны не входит в ежедневный список ЦБ РФ"}

    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT day, rate_to_rub, change_1d_pct FROM fx_rates
                WHERE currency = :cur AND day > CURRENT_DATE - make_interval(days => :days)
                ORDER BY day
            """),
            {"cur": currency, "days": days},
        ).fetchall()

    return {
        "country_code": code,
        "currency": currency,
        "series": [
            {"day": r.day.isoformat(), "rate_to_rub": float(r.rate_to_rub),
             "change_1d_pct": float(r.change_1d_pct) if r.change_1d_pct is not None else None}
            for r in rows
        ],
    }


@router.get("/countries/{code}/entities")
def country_entities(code: str, days: int = Query(30, ge=1, le=180)):
    """Top Russia-orbit entities in this country's coverage (pipeline-matched)."""
    code = code.upper()
    if code not in COUNTRIES:
        raise HTTPException(404, f"Unknown country: {code}")

    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT ek AS entity_key, COUNT(*) AS n, AVG(a.sentiment) AS avg_sent
                FROM analysis a
                JOIN articles ar ON a.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                CROSS JOIN LATERAL jsonb_array_elements_text(a.entities) AS ek
                WHERE s.country_code = :cc AND a.is_relevant = TRUE
                  AND a.entities IS NOT NULL
                  AND ar.published_at > NOW() - make_interval(days => :days)
                GROUP BY ek ORDER BY n DESC LIMIT 25
            """),
            {"cc": code, "days": days},
        ).fetchall()

    out = []
    for r in rows:
        e = ENTITIES.get(r.entity_key)
        out.append({
            "key": r.entity_key,
            "name": e["name_ru"] if e else r.entity_key,
            "category": e["category"] if e else None,
            "mentions": int(r.n),
            "avg_sentiment": round(float(r.avg_sent), 2) if r.avg_sent is not None else None,
        })
    return {"country_code": code, "days": days, "entities": out}


@router.get("/countries/{code}/un-votes")
def country_un_votes(code: str):
    """UN GA voting agreement with Russia by year."""
    code = code.upper()
    if code not in COUNTRIES:
        raise HTTPException(404, f"Unknown country: {code}")
    with get_session() as session:
        rows = session.execute(
            text("""SELECT year, total_votes, agree_with_russia,
                           disagree_with_russia, abstain, agreement_pct
                    FROM un_votes WHERE country_code = :cc ORDER BY year"""),
            {"cc": code}).fetchall()
    return {"country_code": code, "data": [
        {"year": r.year, "total_votes": r.total_votes,
         "agree_with_russia": r.agree_with_russia,
         "disagree_with_russia": r.disagree_with_russia, "abstain": r.abstain,
         "agreement_pct": float(r.agreement_pct) if r.agreement_pct is not None else None}
        for r in rows]}


@router.get("/countries/{code}/trade")
def country_trade(code: str):
    """Russia trade volumes by year."""
    code = code.upper()
    if code not in COUNTRIES:
        raise HTTPException(404, f"Unknown country: {code}")
    with get_session() as session:
        rows = session.execute(
            text("""SELECT year, ru_export_usd, ru_import_usd, total_trade_usd,
                           trade_balance_usd, yoy_change_pct
                    FROM trade_data WHERE country_code = :cc ORDER BY year"""),
            {"cc": code}).fetchall()
    return {"country_code": code, "data": [
        {"year": r.year,
         "ru_export_usd": int(r.ru_export_usd) if r.ru_export_usd is not None else None,
         "ru_import_usd": int(r.ru_import_usd) if r.ru_import_usd is not None else None,
         "total_trade_usd": int(r.total_trade_usd) if r.total_trade_usd is not None else None,
         "trade_balance_usd": int(r.trade_balance_usd) if r.trade_balance_usd is not None else None,
         "yoy_change_pct": float(r.yoy_change_pct) if r.yoy_change_pct is not None else None}
        for r in rows]}


@router.get("/countries/{code}/agreements")
def country_agreements(code: str, days: int = Query(180, ge=7, le=365)):
    """Diplomatic/economic high-action events grouped by event_key."""
    code = code.upper()
    if code not in COUNTRIES:
        raise HTTPException(404, f"Unknown country: {code}")
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT a.event_key, a.event_type, a.action_level,
                       ar.title, ar.url, s.name AS source,
                       ar.published_at
                FROM analysis a
                JOIN articles ar ON a.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE s.country_code = :cc
                  AND a.event_type IN ('diplomatic', 'economic')
                  AND a.action_level >= 3
                  AND a.event_key IS NOT NULL AND a.event_key != ''
                  AND ar.is_duplicate = FALSE
                  AND ar.published_at > NOW() - make_interval(days => :days)
                ORDER BY ar.published_at DESC
                LIMIT 500
            """), {"cc": code, "days": days}).fetchall()
    flat = [{"event_key": r.event_key, "event_type": r.event_type,
             "action_level": r.action_level, "title": r.title, "url": r.url,
             "source": r.source,
             "published_at": r.published_at.isoformat() if r.published_at else ""}
            for r in rows]
    return {"country_code": code, "agreements": group_agreements(flat)}


@router.get("/countries/{code}")
def country_dossier(code: str, days: int = Query(30, ge=1, le=365)):
    """Full dossier: index history, components, structural detail, signals."""
    code = code.upper()
    country = COUNTRIES.get(code)
    if not country:
        raise HTTPException(404, f"Unknown country: {code}")

    with get_session() as session:
        latest = session.execute(
            text("""
                SELECT * FROM ru_index WHERE country_code = :cc
                ORDER BY time DESC LIMIT 1
            """),
            {"cc": code},
        ).fetchone()

        history = session.execute(
            text("""
                SELECT time_bucket, AVG(score) AS score, AVG(structural) AS structural,
                       AVG(media) AS media
                FROM (
                    SELECT date_trunc('day', time) AS time_bucket, score, structural, media
                    FROM ru_index
                    WHERE country_code = :cc AND time > NOW() - make_interval(days => :days)
                ) t
                GROUP BY time_bucket ORDER BY time_bucket
            """),
            {"cc": code, "days": days},
        ).fetchall()

        signals = session.execute(
            text("""
                SELECT id, signal_type, severity, confidence, title, description,
                       payload, created_at
                FROM signals
                WHERE country_code = :cc AND created_at > NOW() - make_interval(days => :days)
                ORDER BY created_at DESC LIMIT 50
            """),
            {"cc": code, "days": days},
        ).fetchall()

        gdelt = session.execute(
            text("""
                SELECT day, volume, volume_share, tone_avg FROM gdelt_daily
                WHERE country_code = :cc AND day > CURRENT_DATE - make_interval(days => :days)
                ORDER BY day
            """),
            {"cc": code, "days": days},
        ).fetchall()

        temperature = session.execute(
            text("""
                SELECT time, temperature, article_count FROM temperature
                WHERE country_code = :cc AND time > NOW() - make_interval(days => :days)
                ORDER BY time
            """),
            {"cc": code, "days": days},
        ).fetchall()

    return {
        "country": {
            "code": code, "name": country["name_ru"], "name_en": country["name_en"],
            "iso3": country["iso3"], "flag": country["flag"],
            "region": country["region"], "region_name": REGIONS.get(country["region"]),
            "tier": country["tier"], "memberships": country["memberships"],
            "unfriendly": country["unfriendly"],
            "sanctions_on_russia": country["sanctions_on_russia"],
            "war_with_russia": country["war_with_russia"],
            "baseline_note": country["baseline_note"],
        },
        "index": {
            "score": float(latest.score), "level": latest.level,
            "structural": float(latest.structural) if latest.structural is not None else None,
            "media": float(latest.media) if latest.media is not None else None,
            "boost": float(latest.boost) if latest.boost is not None else None,
            "delta_24h": float(latest.delta_24h) if latest.delta_24h is not None else None,
            "delta_7d": float(latest.delta_7d) if latest.delta_7d is not None else None,
            "details": latest.details,
            "updated_at": latest.time.isoformat(),
            "version": latest.version,
        } if latest else None,
        "index_history": [
            {"day": r.time_bucket.date().isoformat(), "score": round(float(r.score), 2),
             "structural": round(float(r.structural), 2) if r.structural is not None else None,
             "media": round(float(r.media), 2) if r.media is not None else None}
            for r in history
        ],
        "temperature_history": [
            {"time": r.time.isoformat(), "temperature": float(r.temperature),
             "article_count": r.article_count}
            for r in temperature
        ],
        "gdelt": [
            {"day": r.day.isoformat(),
             "volume": float(r.volume) if r.volume is not None else None,
             "volume_share": float(r.volume_share) if r.volume_share is not None else None,
             "tone": float(r.tone_avg) if r.tone_avg is not None else None}
            for r in gdelt
        ],
        "signals": [
            {"id": r.id, "type": r.signal_type, "severity": r.severity,
             "confidence": float(r.confidence or 0), "title": r.title,
             "description": r.description, "payload": r.payload,
             "created_at": r.created_at.isoformat()}
            for r in signals
        ],
    }


@router.get("/countries/{code}/topics")
def country_topics(code: str, days: int = Query(30, ge=1, le=365)):
    """Topic breakdown: volume + tone per taxonomy label (deep-coverage countries)."""
    code = code.upper()
    if code not in COUNTRIES:
        raise HTTPException(404, f"Unknown country: {code}")

    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT topic, COUNT(*) AS n, AVG(a.sentiment) AS avg_sent,
                       MAX(a.action_level) AS max_al
                FROM analysis a
                JOIN articles ar ON a.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                CROSS JOIN LATERAL unnest(a.topics) AS topic
                WHERE s.country_code = :cc AND a.is_relevant = TRUE
                  AND ar.published_at > NOW() - make_interval(days => :days)
                GROUP BY topic ORDER BY n DESC
            """),
            {"cc": code, "days": days},
        ).fetchall()

    return {
        "country_code": code,
        "days": days,
        "topics": [
            {"topic": r.topic, "label": TOPICS.get(r.topic, r.topic),
             "articles": int(r.n), "avg_sentiment": round(float(r.avg_sent or 0), 2),
             "max_action_level": int(r.max_al or 1)}
            for r in rows
        ],
        "note": "Тематика доступна для статей, проанализированных промптом v2.0+",
    }


@router.get("/countries/{code}/headlines")
def country_headlines(code: str, days: int = Query(3, ge=1, le=30),
                      limit: int = Query(20, ge=1, le=100)):
    """Recent Russia-related headlines: own media (tier 1) or GDELT samples."""
    code = code.upper()
    if code not in COUNTRIES:
        raise HTTPException(404, f"Unknown country: {code}")

    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT ar.title, ar.url, ar.published_at, s.name AS source_name,
                       s.tier, a.sentiment, a.action_level, a.topics
                FROM analysis a
                JOIN articles ar ON a.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE s.country_code = :cc AND a.is_relevant = TRUE
                  AND ar.published_at > NOW() - make_interval(days => :days)
                ORDER BY ar.published_at DESC LIMIT :lim
            """),
            {"cc": code, "days": days, "lim": limit},
        ).fetchall()

        if rows:
            return {
                "country_code": code, "source": "own_media",
                "headlines": [
                    {"title": r.title, "url": r.url,
                     "published_at": r.published_at.isoformat(),
                     "source": r.source_name, "tier": r.tier,
                     "sentiment": float(r.sentiment) if r.sentiment is not None else None,
                     "action_level": r.action_level, "topics": r.topics}
                    for r in rows
                ],
            }

        gdelt = session.execute(
            text("""
                SELECT day, article_samples FROM gdelt_daily
                WHERE country_code = :cc AND article_samples IS NOT NULL
                ORDER BY day DESC LIMIT 1
            """),
            {"cc": code},
        ).fetchone()

    samples = (gdelt.article_samples or []) if gdelt else []
    return {
        "country_code": code, "source": "gdelt",
        "headlines": [
            {"title": s.get("title"), "url": s.get("url"), "source": s.get("domain"),
             "language": s.get("language"), "seendate": s.get("seendate")}
            for s in samples[:limit]
        ],
    }


@router.get("/countries/{code}/brief")
def country_brief(code: str, refresh: bool = False):
    """AI dossier brief for the country (cached 6h, regenerated on demand)."""
    code = code.upper()
    if code not in COUNTRIES:
        raise HTTPException(404, f"Unknown country: {code}")

    from src.pipeline.briefs import generate_country_brief
    brief = generate_country_brief(code, force=refresh)
    if not brief:
        raise HTTPException(404, "Недостаточно данных для брифинга по этой стране")
    return {"country_code": code, **brief}


@router.get("/brief")
def world_brief():
    """Latest world brief «Россия и мир»."""
    with get_session() as session:
        row = session.execute(
            text("""
                SELECT content, model, created_at, meta FROM briefs
                WHERE scope = 'world' ORDER BY created_at DESC LIMIT 1
            """)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Мировой брифинг ещё не сгенерирован")
    return {"content": row.content, "model": row.model,
            "created_at": row.created_at.isoformat(), "meta": row.meta}


@router.get("/signals")
def list_signals(days: int = Query(3, ge=1, le=30),
                 country: Optional[str] = None,
                 signal_type: Optional[str] = None,
                 active_only: bool = False,
                 limit: int = Query(100, ge=1, le=500)):
    """Signal feed across all countries."""
    conditions = ["created_at > NOW() - make_interval(days => :days)"]
    params: dict = {"days": days, "lim": limit}
    if country:
        conditions.append("country_code = :cc")
        params["cc"] = country.upper()
    if signal_type:
        conditions.append("signal_type = :st")
        params["st"] = signal_type
    if active_only:
        conditions.append("expires_at > NOW()")

    with get_session() as session:
        rows = session.execute(
            text(f"""
                SELECT id, signal_type, country_code, severity, confidence,
                       title, description, payload, created_at, expires_at
                FROM signals
                WHERE {' AND '.join(conditions)}
                ORDER BY created_at DESC LIMIT :lim
            """),
            params,
        ).fetchall()

    return {
        "signals": [
            {"id": r.id, "type": r.signal_type, "country_code": r.country_code,
             "country_name": country_name_ru(r.country_code) if r.country_code else None,
             "severity": r.severity, "confidence": float(r.confidence or 0),
             "title": r.title, "description": r.description, "payload": r.payload,
             "created_at": r.created_at.isoformat(),
             "expires_at": r.expires_at.isoformat() if r.expires_at else None,
             "active": bool(r.expires_at and r.expires_at > datetime.now(timezone.utc))}
            for r in rows
        ],
        "total": len(rows),
    }


@router.get("/headlines")
def world_headlines(hours: int = Query(24, ge=1, le=168),
                    tier: Optional[str] = None,
                    country: Optional[str] = None,
                    region: Optional[str] = None,
                    limit: int = Query(20, ge=1, le=100)):
    """Top relevant headlines across all countries (main page 'news of the day').

    `country` filters by the SOURCE's home country; when set, no diversity caps
    are applied (single-country view wants all its headlines).

    `region` filters to countries in that region (source home country's region);
    unknown region key → 400.  When region or global view is active (no `country`
    param), diversity caps are applied: ≤ 2 articles per source and ≤ 4 per
    country, so no single country monopolises the feed.

    `total` is the number of returned rows (capped by `limit`).
    """
    if tier and tier not in KNOWN_TIERS:
        raise HTTPException(400, f"Unknown tier: {tier}")
    if region and region not in REGIONS:
        raise HTTPException(400, f"Unknown region: {region}")

    conditions = ["a.is_relevant = TRUE",
                  "ar.is_duplicate = FALSE",
                  "ar.published_at > NOW() - make_interval(hours => :h)",
                  "ar.url IS NOT NULL"]
    params: dict = {"h": hours, "lim": limit}
    if tier:
        conditions.append("s.tier = :tier")
        params["tier"] = tier
    if country:
        conditions.append("s.country_code = :cc")
        params["cc"] = country.upper()
    if region:
        region_codes = [c for c, v in COUNTRIES.items() if v["region"] == region]
        conditions.append("s.country_code = ANY(:region_codes)")
        params["region_codes"] = region_codes

    where_clause = " AND ".join(conditions)

    if country:
        # Single-country view: no diversity caps, simple query.
        sql = f"""
            SELECT ar.title, ar.url, s.name AS source_name, s.tier,
                   s.country_code, ar.published_at,
                   a.sentiment, a.action_level
            FROM analysis a
            JOIN articles ar ON a.article_id = ar.id
            JOIN sources s ON ar.source_id = s.id
            WHERE {where_clause}
            ORDER BY a.action_level DESC NULLS LAST,
                     ar.reprint_count DESC NULLS LAST,
                     ar.published_at DESC
            LIMIT :lim
        """
    else:
        # Global / region view: cap ≤ 2 per source and ≤ 4 per country.
        sql = f"""
            SELECT title, url, source_name, tier, country_code, published_at,
                   sentiment, action_level
            FROM (
                SELECT ar.title, ar.url, s.name AS source_name, s.tier,
                       s.country_code, ar.published_at, ar.reprint_count,
                       a.sentiment, a.action_level,
                       ROW_NUMBER() OVER (
                           PARTITION BY ar.source_id
                           ORDER BY a.action_level DESC NULLS LAST,
                                    ar.published_at DESC
                       ) AS src_rank,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.country_code
                           ORDER BY a.action_level DESC NULLS LAST,
                                    ar.published_at DESC
                       ) AS cc_rank
                FROM analysis a
                JOIN articles ar ON a.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE {where_clause}
            ) t
            WHERE src_rank <= 2 AND cc_rank <= 4
            ORDER BY action_level DESC NULLS LAST,
                     reprint_count DESC NULLS LAST,
                     published_at DESC
            LIMIT :lim
        """

    with get_session() as session:
        rows = session.execute(text(sql), params).fetchall()

    return {"headlines": [
        {"title": r.title, "url": r.url, "source": r.source_name, "tier": r.tier,
         "country_code": r.country_code,
         "country_name": country_name_ru(r.country_code),
         "flag": (COUNTRIES.get(r.country_code) or {}).get("flag", ""),
         "published_at": r.published_at.isoformat() if r.published_at else None,
         "sentiment": float(r.sentiment) if r.sentiment is not None else None,
         "action_level": int(r.action_level or 1)}
        for r in rows], "total": len(rows)}


@router.get("/topics/{topic}/countries")
def topic_countries(topic: str, days: int = Query(30, ge=1, le=180)):
    """Topic lens: which countries discuss this topic, how much and in what tone."""
    if topic not in TOPICS:
        raise HTTPException(404, f"Unknown topic: {topic}")

    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT s.country_code, COUNT(*) AS n, AVG(a.sentiment) AS avg_sent
                FROM analysis a
                JOIN articles ar ON a.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE a.is_relevant = TRUE AND :topic = ANY(a.topics)
                  AND ar.published_at > NOW() - make_interval(days => :days)
                GROUP BY s.country_code ORDER BY n DESC
            """),
            {"topic": topic, "days": days},
        ).fetchall()

    return {
        "topic": topic,
        "label": TOPICS[topic],
        "days": days,
        "countries": [
            {"country_code": r.country_code,
             "country_name": country_name_ru(r.country_code),
             "articles": int(r.n),
             "avg_sentiment": round(float(r.avg_sent), 2) if r.avg_sent is not None else None}
            for r in rows
        ],
    }


@router.get("/entities")
def list_entities(category: Optional[str] = None):
    """Russia-orbit entity registry (actors, blocs, companies, concepts)."""
    items = list(ENTITIES.values())
    if category:
        items = [e for e in items if e["category"] == category]
    return {"entities": items, "categories": ENTITY_CATEGORIES, "total": len(items)}


@router.get("/entities/{key}/mentions")
def entity_mentions(key: str, days: int = Query(30, ge=1, le=180),
                    limit: int = Query(15, ge=1, le=50)):
    """Where and how an entity is mentioned: per-country volume + tone."""
    entity = get_entity(key)
    if not entity:
        raise HTTPException(404, f"Unknown entity: {key}")

    patterns = {f"a{i}": f"%{alias}%" for i, alias in enumerate(entity["aliases"])}
    like_clause = " OR ".join(f"ar.title ILIKE :{name}" for name in patterns)

    with get_session() as session:
        by_country = session.execute(
            text(f"""
                SELECT s.country_code, COUNT(*) AS n, AVG(a.sentiment) AS avg_sent,
                       MAX(ar.published_at) AS last_seen
                FROM articles ar
                JOIN sources s ON ar.source_id = s.id
                LEFT JOIN analysis a ON a.article_id = ar.id AND a.is_relevant = TRUE
                WHERE ar.published_at > NOW() - make_interval(days => :days)
                  AND ({like_clause})
                GROUP BY s.country_code ORDER BY n DESC
            """),
            {"days": days, **patterns},
        ).fetchall()

        recent = session.execute(
            text(f"""
                SELECT ar.title, ar.url, ar.published_at, s.country_code, a.sentiment
                FROM articles ar
                JOIN sources s ON ar.source_id = s.id
                LEFT JOIN analysis a ON a.article_id = ar.id
                WHERE ar.published_at > NOW() - make_interval(days => :days)
                  AND ({like_clause})
                ORDER BY ar.published_at DESC LIMIT :lim
            """),
            {"days": days, "lim": limit, **patterns},
        ).fetchall()

    return {
        "entity": entity,
        "days": days,
        "by_country": [
            {"country_code": r.country_code,
             "country_name": country_name_ru(r.country_code),
             "mentions": int(r.n),
             "avg_sentiment": round(float(r.avg_sent), 2) if r.avg_sent is not None else None,
             "last_seen": r.last_seen.isoformat()}
            for r in by_country
        ],
        "recent": [
            {"title": r.title, "url": r.url, "country_code": r.country_code,
             "published_at": r.published_at.isoformat(),
             "sentiment": float(r.sentiment) if r.sentiment is not None else None}
            for r in recent
        ],
    }


@router.get("/health")
def world_health():
    """Overall data-freshness verdict (HEALTHY/WARNING/DEGRADED/UNHEALTHY)."""
    return health_summary()


@router.get("/health/sources")
def world_health_sources(status: Optional[str] = None):
    """Per-source freshness with learned cadence."""
    sources = source_health()
    if status:
        sources = [s for s in sources if s["status"] == status.upper()]
    return {"sources": sources, "total": len(sources)}
