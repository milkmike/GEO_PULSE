"""FastAPI application."""
import logging
import os
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from src.config import COUNTRY_NAMES, COUNTRY_ISO3
from src.db import get_session
from src.api.routes.sources import router as sources_router
from src.api.routes.threads import router as threads_router
from src.api.routes.vox import router as vox_router
from src.api.routes.articles import router as articles_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="CIS Thermometer API", version="1.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(sources_router)
app.include_router(threads_router)
app.include_router(vox_router)
app.include_router(articles_router)


@app.get("/")
def root():
    return {"status": "ok", "service": "CIS Thermometer API", "version": "1.1.0"}


@app.get("/api/v1/countries")
def get_countries(days: int = Query(default=0, ge=0, le=1460)):
    """List all countries with current temperature. days=0 means all time."""
    with get_session() as session:
        results = []
        date_filter = f"AND ar.published_at > NOW() - INTERVAL '{days} days'" if days > 0 else ""
        date_filter_a = f"AND a.published_at > NOW() - INTERVAL '{days} days'" if days > 0 else ""

        for code, name in COUNTRY_NAMES.items():
            # Temperature: if days specified, compute average over period; otherwise latest snapshot
            if days > 0:
                row = session.execute(
                    text(f"""
                        SELECT AVG(temperature) as temperature,
                               AVG(raw_sentiment) as raw_sentiment,
                               MAX(time) as time,
                               CASE
                                   WHEN COUNT(*) >= 2 THEN
                                       CASE WHEN (SELECT temperature FROM temperature
                                                  WHERE country_code = :cc ORDER BY time DESC LIMIT 1) >
                                                 (SELECT AVG(temperature) FROM temperature
                                                  WHERE country_code = :cc
                                                    AND time > NOW() - INTERVAL '{days} days')
                                       THEN 'rising' ELSE 'falling' END
                                   ELSE 'stable'
                               END as trend,
                               SUM(article_count) as article_count
                        FROM temperature
                        WHERE country_code = :cc
                          AND time > NOW() - INTERVAL '{days} days'
                    """),
                    {"cc": code},
                ).fetchone()
            else:
                row = session.execute(
                    text("""
                        SELECT temperature, raw_sentiment, trend, article_count, time
                        FROM temperature
                        WHERE country_code = :cc
                        ORDER BY time DESC LIMIT 1
                    """),
                    {"cc": code},
                ).fetchone()

            art_count = session.execute(
                text(f"""
                    SELECT COUNT(*) FROM articles a
                    JOIN sources s ON a.source_id = s.id
                    WHERE s.country_code = :cc {date_filter_a}
                """),
                {"cc": code},
            ).scalar()

            # Tier divergence
            divergence_interval = f"{days} days" if days > 0 else "14 days"
            tier_rows = session.execute(
                text(f"""
                    SELECT COALESCE(s.tier, 'mainstream') AS tier,
                           AVG(an.sentiment) AS avg_sent
                    FROM analysis an
                    JOIN articles ar ON an.article_id = ar.id
                    JOIN sources s ON ar.source_id = s.id
                    WHERE s.country_code = :cc
                      AND an.is_relevant = true
                      AND an.sentiment IS NOT NULL
                      AND ar.published_at > NOW() - INTERVAL '{divergence_interval}'
                    GROUP BY s.tier
                """),
                {"cc": code},
            ).fetchall()
            tier_sents = [float(r.avg_sent) for r in tier_rows if r.avg_sent is not None]
            divergence = round(max(tier_sents) - min(tier_sents), 2) if len(tier_sents) >= 2 else 0.0

            entry = {
                "code": code,
                "name": name,
                "iso3": COUNTRY_ISO3.get(code),
                "article_count": art_count or 0,
            }

            if row:
                entry.update({
                    "temperature": float(row.temperature) if row.temperature else None,
                    "raw_sentiment": float(row.raw_sentiment) if row.raw_sentiment else None,
                    "trend": row.trend,
                    "last_updated": row.time.isoformat() if row.time else None,
                    "divergence": divergence,
                })
            else:
                entry.update({
                    "temperature": None,
                    "raw_sentiment": None,
                    "trend": None,
                    "last_updated": None,
                    "divergence": divergence,
                })

            results.append(entry)

        return {"countries": results}


@app.get("/api/v1/countries/{code}/temperature")
def get_temperature(code: str, days: int = Query(default=30, le=365)):
    """Get temperature time series for a country."""
    code = code.upper()
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT time, temperature, raw_sentiment, trend, anomaly_score,
                       article_count, source_count,
                       diplomatic, military, economic, cultural, security
                FROM temperature
                WHERE country_code = :cc
                  AND time > NOW() - INTERVAL ':days days'
                ORDER BY time ASC
            """.replace(":days", str(days))),
            {"cc": code},
        ).fetchall()

        return {
            "country": code,
            "name": COUNTRY_NAMES.get(code, code),
            "period_days": days,
            "data": [
                {
                    "time": r.time.isoformat(),
                    "temperature": float(r.temperature) if r.temperature else None,
                    "raw_sentiment": float(r.raw_sentiment) if r.raw_sentiment else None,
                    "trend": r.trend,
                    "anomaly_score": float(r.anomaly_score) if r.anomaly_score else None,
                    "article_count": r.article_count,
                    "components": {
                        "diplomatic": float(r.diplomatic) if r.diplomatic else None,
                        "military": float(r.military) if r.military else None,
                        "economic": float(r.economic) if r.economic else None,
                        "cultural": float(r.cultural) if r.cultural else None,
                        "security": float(r.security) if r.security else None,
                    },
                }
                for r in rows
            ],
        }


@app.get("/api/v1/countries/{code}/events")
def get_events(
    code: str,
    limit: int = Query(default=50, le=200),
    sort: str = Query(default="date", regex="^(date|impact)$"),
):
    """Get recent events (analyzed articles) for a country."""
    code = code.upper()
    
    order_clause = "ar.published_at DESC"
    if sort == "impact":
        order_clause = "COALESCE(an.action_level, 1) * ABS(COALESCE(an.sentiment, 0)) DESC"
    
    with get_session() as session:
        rows = session.execute(
            text(f"""
                SELECT ar.title, ar.url, ar.published_at,
                       COALESCE(ar.reprint_count, 0) as reprint_count,
                       an.sentiment, an.event_type, an.sentiment_confidence,
                       an.action_level,
                       an.raw_response,
                       s.name as source_name
                FROM analysis an
                JOIN articles ar ON an.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE s.country_code = :cc
                  AND an.is_relevant = true
                ORDER BY {order_clause}
                LIMIT :lim
            """),
            {"cc": code, "lim": limit},
        ).fetchall()

        return {
            "country": code,
            "name": COUNTRY_NAMES.get(code, code),
            "events": [
                {
                    "title": r.title,
                    "url": r.url,
                    "published_at": r.published_at.isoformat() if r.published_at else None,
                    "sentiment": float(r.sentiment) if r.sentiment else None,
                    "event_type": r.event_type,
                    "confidence": float(r.sentiment_confidence) if r.sentiment_confidence else None,
                    "action_level": r.action_level or 1,
                    "source": r.source_name,
                    "reprint_count": r.reprint_count,
                    "reasoning": r.raw_response.get("reasoning", "") if r.raw_response else "",
                }
                for r in rows
            ],
        }


@app.get("/api/v1/compare")
def compare_countries(countries: str = Query(default="KZ,AM,UZ"), days: int = Query(default=30, le=365)):
    """Compare temperature across countries."""
    codes = [c.strip().upper() for c in countries.split(",")]
    result = {}

    with get_session() as session:
        for code in codes:
            rows = session.execute(
                text("""
                    SELECT time, temperature
                    FROM temperature
                    WHERE country_code = :cc
                      AND time > NOW() - INTERVAL ':days days'
                    ORDER BY time ASC
                """.replace(":days", str(days))),
                {"cc": code},
            ).fetchall()

            result[code] = {
                "name": COUNTRY_NAMES.get(code, code),
                "data": [
                    {"time": r.time.isoformat(), "temperature": float(r.temperature) if r.temperature else None}
                    for r in rows
                ],
            }

    return {"comparison": result, "period_days": days}


@app.get("/api/v1/alerts")
def get_alerts(limit: int = Query(default=50, le=200)):
    """Get recent alerts."""
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT id, country_code, alert_type, severity, title, description, data, created_at
                FROM alerts
                ORDER BY created_at DESC
                LIMIT :lim
            """),
            {"lim": limit},
        ).fetchall()

        return {
            "alerts": [
                {
                    "id": r.id,
                    "country": r.country_code,
                    "country_name": COUNTRY_NAMES.get(r.country_code, r.country_code),
                    "type": r.alert_type,
                    "severity": r.severity,
                    "title": r.title,
                    "description": r.description,
                    "data": r.data,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ],
        }


@app.get("/api/v1/stats")
def get_stats(days: int = Query(default=0, ge=0, le=1460)):
    """Get overall system statistics. days=0 means all time."""
    with get_session() as session:
        date_filter_articles = ""
        date_filter_analysis = ""
        if days > 0:
            date_filter_articles = f"AND a.published_at > NOW() - INTERVAL '{days} days'"
            date_filter_analysis = f"AND ar.published_at > NOW() - INTERVAL '{days} days'"

        total_articles = session.execute(
            text(f"SELECT COUNT(*) FROM articles a WHERE 1=1 {date_filter_articles}")
        ).scalar()
        total_analyzed = session.execute(
            text(f"""SELECT COUNT(*) FROM analysis an
                     JOIN articles ar ON an.article_id = ar.id
                     WHERE 1=1 {date_filter_analysis}""")
        ).scalar()
        total_relevant = session.execute(
            text(f"""SELECT COUNT(*) FROM analysis an
                     JOIN articles ar ON an.article_id = ar.id
                     WHERE an.is_relevant = true {date_filter_analysis}""")
        ).scalar()
        total_duplicates = session.execute(
            text(f"""SELECT COUNT(*) FROM articles a
                     WHERE a.is_duplicate = TRUE {date_filter_articles}""")
        ).scalar()

        # Active sources: if filtering by period, count sources that produced articles in that period
        if days > 0:
            total_sources = session.execute(
                text(f"""SELECT COUNT(DISTINCT s.id) FROM sources s
                         JOIN articles a ON a.source_id = s.id
                         WHERE s.active = true {date_filter_articles}""")
            ).scalar()
        else:
            total_sources = session.execute(
                text("SELECT COUNT(*) FROM sources WHERE active = true")
            ).scalar()

        # Date range
        date_range = session.execute(
            text(f"""SELECT MIN(a.published_at) AS oldest,
                            MAX(a.published_at) AS newest
                     FROM articles a WHERE 1=1 {date_filter_articles}""")
        ).fetchone()

        # Last temperature update
        last_temp = session.execute(
            text("SELECT MAX(time) FROM temperature")
        ).scalar()

        return {
            "total_articles": total_articles,
            "total_analyzed": total_analyzed,
            "total_relevant": total_relevant,
            "active_sources": total_sources,
            "total_duplicates": total_duplicates,
            "period_days": days if days > 0 else None,
            "oldest_article": date_range.oldest.isoformat() if date_range and date_range.oldest else None,
            "newest_article": date_range.newest.isoformat() if date_range and date_range.newest else None,
            "last_temperature_update": last_temp.isoformat() if last_temp else None,
        }


TIER_LABELS = {
    "official": "🏛️ Официальный",
    "state": "🏛️ Государственный",
    "mainstream": "📰 Mainstream",
    "analytics": "🔍 Аналитика",
    "independent": "📋 Независимые",
    "social": "💬 Соцсети",
    "opposition": "📢 Оппозиция",
    "western_proxy": "🌐 Западные прокси",
    "domestic_opposition": "📢 Внутренняя оппозиция",
}

TIER_ORDER = ["official", "state", "mainstream", "analytics", "independent", "social", "opposition", "western_proxy", "domestic_opposition"]


@app.get("/api/v1/countries/{code}/tiers")
def get_country_tiers(code: str, days: int = Query(default=14, le=365)):
    """Get sentiment breakdown by source tier for a country, with headlines."""
    code = code.upper()
    with get_session() as session:
        # Main aggregation query
        rows = session.execute(
            text("""
                SELECT COALESCE(s.tier, 'mainstream') AS tier,
                       s.name AS source_name,
                       an.sentiment,
                       s.weight
                FROM analysis an
                JOIN articles ar ON an.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE s.country_code = :cc
                  AND an.is_relevant = true
                  AND an.sentiment IS NOT NULL
                  AND ar.published_at > NOW() - make_interval(days => :days)
            """),
            {"cc": code, "days": days},
        ).fetchall()

        # Headlines query: top articles per tier by |sentiment|
        headline_rows = session.execute(
            text("""
                SELECT COALESCE(s.tier, 'mainstream') AS tier,
                       ar.title,
                       ar.url,
                       an.sentiment,
                       s.name AS source_name,
                       ROW_NUMBER() OVER (
                           PARTITION BY COALESCE(s.tier, 'mainstream')
                           ORDER BY ABS(an.sentiment) DESC, ar.published_at DESC
                       ) AS rn
                FROM analysis an
                JOIN articles ar ON an.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE s.country_code = :cc
                  AND an.is_relevant = true
                  AND an.sentiment IS NOT NULL
                  AND ar.title IS NOT NULL
                  AND ar.title != ''
                  AND ar.published_at > NOW() - make_interval(days => :days)
            """),
            {"cc": code, "days": days},
        ).fetchall()

        # Group headlines by tier (top 5)
        tier_headlines = {}
        for hr in headline_rows:
            if hr.rn <= 5:
                tier_headlines.setdefault(hr.tier, []).append({
                    "title": hr.title or "",
                    "url": hr.url or "",
                    "sentiment": round(float(hr.sentiment), 2),
                    "source": hr.source_name,
                })

        # Aggregate by tier
        tier_data = {}
        for row in rows:
            t = row.tier or "mainstream"
            if t not in tier_data:
                tier_data[t] = {"sentiments": [], "sources": set()}
            tier_data[t]["sentiments"].append(float(row.sentiment))
            tier_data[t]["sources"].add(row.source_name)

        tiers = []
        reliable_sentiments = []  # Only tiers with >= 3 articles for divergence
        for t in TIER_ORDER:
            if t in tier_data:
                d = tier_data[t]
                avg = sum(d["sentiments"]) / len(d["sentiments"])
                count = len(d["sentiments"])
                is_reliable = count >= 3
                if is_reliable:
                    reliable_sentiments.append(avg)
                tiers.append({
                    "tier": t,
                    "label": TIER_LABELS.get(t, t),
                    "sentiment": round(avg, 2),
                    "article_count": count,
                    "sources": sorted(d["sources"]),
                    "headlines": tier_headlines.get(t, []),
                    "low_data": not is_reliable,
                })
        # Include tiers not in TIER_ORDER
        for t in tier_data:
            if t not in TIER_ORDER:
                d = tier_data[t]
                avg = sum(d["sentiments"]) / len(d["sentiments"])
                count = len(d["sentiments"])
                is_reliable = count >= 3
                if is_reliable:
                    reliable_sentiments.append(avg)
                tiers.append({
                    "tier": t,
                    "label": TIER_LABELS.get(t, t),
                    "sentiment": round(avg, 2),
                    "article_count": count,
                    "sources": sorted(d["sources"]),
                    "headlines": tier_headlines.get(t, []),
                    "low_data": not is_reliable,
                })

        # Overall
        all_sentiments = [float(r.sentiment) for r in rows]
        overall = round(sum(all_sentiments) / len(all_sentiments), 2) if all_sentiments else 0

        # Divergence (only from reliable tiers with >= 3 articles)
        divergence = 0.0
        if len(reliable_sentiments) >= 2:
            divergence = round(max(reliable_sentiments) - min(reliable_sentiments), 2)

        return {
            "country_code": code,
            "country_name": COUNTRY_NAMES.get(code, code),
            "overall_sentiment": overall,
            "tiers": tiers,
            "divergence": divergence,
        }


@app.get("/api/v1/countries/{code}/digest")
def get_digest(code: str):
    from sqlalchemy import text as sa_text
    with get_session() as session:
        row = session.execute(sa_text("""
            SELECT digest_text, generated_at, temperature_end, key_events
            FROM digests WHERE country_code = :cc
            ORDER BY generated_at DESC LIMIT 1
        """), {"cc": code.upper()}).fetchone()
        if not row:
            return {"digest": None}
        return {
            "digest": row.digest_text,
            "generated_at": row.generated_at.isoformat() if row.generated_at else None,
            "temperature": row.temperature_end,
        }


@app.get("/api/v1/countries/{code}/un-votes")
def get_un_votes(code: str):
    """Get UN General Assembly voting agreement with Russia by year."""
    code = code.upper()
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT year, total_votes, agree_with_russia,
                       disagree_with_russia, abstain, agreement_pct
                FROM un_votes
                WHERE country_code = :cc
                ORDER BY year ASC
            """),
            {"cc": code},
        ).fetchall()

        return {
            "country": code,
            "name": COUNTRY_NAMES.get(code, code),
            "data": [
                {
                    "year": r.year,
                    "total_votes": r.total_votes,
                    "agree_with_russia": r.agree_with_russia,
                    "disagree_with_russia": r.disagree_with_russia,
                    "abstain": r.abstain,
                    "agreement_pct": float(r.agreement_pct) if r.agreement_pct else None,
                }
                for r in rows
            ],
        }


@app.get("/api/v1/countries/{code}/trade")
def get_trade(code: str):
    """Get Russia-country trade data by year."""
    code = code.upper()
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT year, ru_export_usd, ru_import_usd,
                       total_trade_usd, trade_balance_usd, yoy_change_pct
                FROM trade_data
                WHERE country_code = :cc
                ORDER BY year ASC
            """),
            {"cc": code},
        ).fetchall()

        return {
            "country": code,
            "name": COUNTRY_NAMES.get(code, code),
            "data": [
                {
                    "year": r.year,
                    "ru_export_usd": r.ru_export_usd,
                    "ru_import_usd": r.ru_import_usd,
                    "total_trade_usd": r.total_trade_usd,
                    "trade_balance_usd": r.trade_balance_usd,
                    "yoy_change_pct": float(r.yoy_change_pct) if r.yoy_change_pct else None,
                }
                for r in rows
            ],
        }


@app.get("/api/v1/un-votes/summary")
def get_un_votes_summary():
    """Get latest UN vote agreement % for all countries."""
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT DISTINCT ON (country_code)
                       country_code, year, agreement_pct
                FROM un_votes
                ORDER BY country_code, year DESC
            """),
        ).fetchall()

        return {
            "summary": {
                r.country_code: {
                    "year": r.year,
                    "agreement_pct": float(r.agreement_pct) if r.agreement_pct else None,
                }
                for r in rows
            }
        }



@app.get("/api/v1/countries/{code}/resonance")
def get_resonance_events(code: str, days: int = 14, limit: int = 10):
    """Get top events by resonance score for a country."""
    code = code.upper()
    if code not in COUNTRY_NAMES:
        return {"error": "Unknown country code"}
    
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT 
                    a.event_key,
                    COUNT(*) as article_count,
                    COUNT(DISTINCT ar.source_id) as source_count,
                    COUNT(DISTINCT s.tier) as tier_count,
                    AVG(a.sentiment) as avg_sentiment,
                    MAX(a.action_level) as max_action_level,
                    MIN(ar.published_at) as first_seen,
                    MAX(ar.published_at) as last_seen,
                    EXTRACT(EPOCH FROM (MAX(ar.published_at) - MIN(ar.published_at))) / 3600.0 as spread_hours,
                    array_agg(DISTINCT s.name) as source_names,
                    array_agg(DISTINCT s.tier) as tiers
                FROM analysis a
                JOIN articles ar ON a.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE s.country_code = :cc
                  AND a.is_relevant = true
                  AND a.sentiment IS NOT NULL
                  AND a.event_key IS NOT NULL
                  AND LENGTH(a.event_key) > 3
                  AND ar.published_at > NOW() - make_interval(days => :days)
                GROUP BY a.event_key
                HAVING COUNT(*) >= 3
                ORDER BY COUNT(*) DESC
                LIMIT :lim
            """),
            {"cc": code, "days": days, "lim": limit},
        ).fetchall()
        
        import math
        events = []
        for r in rows:
            ac = r.article_count
            sc = r.source_count
            tc = r.tier_count
            sh = float(r.spread_hours or 0)
            al = r.max_action_level or 1
            
            # Resonance formula
            volume = math.log(ac + 1) * 2
            diversity = (sc / max(ac, 1) + 0.5) * (tc * 0.8)
            speed = 1.5 if sh < 6 else (1.2 if sh < 24 else 1.0)
            action_w = {1: 0.5, 2: 0.8, 3: 1.2, 4: 1.8, 5: 2.5, 6: 3.0}.get(al, 1.0)
            
            resonance = round(volume * diversity * speed * action_w, 1)
            
            events.append({
                "event_key": r.event_key,
                "resonance_score": resonance,
                "article_count": ac,
                "source_count": sc,
                "tier_count": tc,
                "avg_sentiment": round(float(r.avg_sentiment), 2),
                "max_action_level": al,
                "first_seen": r.first_seen.isoformat() if r.first_seen else None,
                "last_seen": r.last_seen.isoformat() if r.last_seen else None,
                "spread_hours": round(sh, 1),
                "source_names": list(r.source_names) if r.source_names else [],
                "tiers": list(r.tiers) if r.tiers else [],
            })
        
        # Sort by resonance
        # Filter garbage event_keys
        _blacklist = ['прогноз погоды', 'архив сайта', 'поиск на сайте', 'курс валют', 'новости дня', 'лента новостей', 'главные новости']
        events = [e for e in events if not any(bl in e['event_key'] for bl in _blacklist)]
        events.sort(key=lambda x: -x["resonance_score"])
        
        return {"country": code, "days": days, "events": events}


@app.get("/api/v1/analytics/coverage")
def get_coverage(days: int = Query(default=30, ge=7, le=90)):
    """Get daily article counts per country for coverage heatmap."""
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT s.country_code,
                       DATE(ar.published_at) AS day,
                       COUNT(*) AS total,
                       COUNT(*) FILTER (WHERE an.id IS NOT NULL) AS analyzed,
                       COUNT(*) FILTER (WHERE an.is_relevant = true) AS relevant
                FROM articles ar
                JOIN sources s ON ar.source_id = s.id
                LEFT JOIN analysis an ON an.article_id = ar.id
                WHERE ar.published_at > NOW() - make_interval(days => :days)
                  AND s.country_code IS NOT NULL
                GROUP BY s.country_code, DATE(ar.published_at)
                ORDER BY s.country_code, day
            """),
            {"days": days},
        ).fetchall()

        # Group by country
        coverage: dict = {}
        for r in rows:
            cc = r.country_code
            if cc not in coverage:
                coverage[cc] = {"code": cc, "name": COUNTRY_NAMES.get(cc, cc), "days": []}
            coverage[cc]["days"].append({
                "date": r.day.isoformat(),
                "total": r.total,
                "analyzed": r.analyzed,
                "relevant": r.relevant,
            })

        return {"period_days": days, "countries": list(coverage.values())}


@app.get("/api/v1/analytics/tier-divergence")
def get_tier_divergence(days: int = Query(default=14, ge=1, le=90)):
    """Get tier sentiment divergence across all countries in a single query."""
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT s.country_code,
                       COALESCE(s.tier, 'mainstream') AS tier,
                       AVG(an.sentiment) AS avg_sentiment,
                       COUNT(*) AS article_count,
                       COUNT(DISTINCT s.id) AS source_count
                FROM analysis an
                JOIN articles ar ON an.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE an.is_relevant = true
                  AND an.sentiment IS NOT NULL
                  AND ar.published_at > NOW() - make_interval(days => :days)
                  AND s.country_code IS NOT NULL
                GROUP BY s.country_code, COALESCE(s.tier, 'mainstream')
                ORDER BY s.country_code, tier
            """),
            {"days": days},
        ).fetchall()

        # Aggregate per country
        countries_data: dict = {}
        for r in rows:
            cc = r.country_code
            if cc not in countries_data:
                countries_data[cc] = {"code": cc, "name": COUNTRY_NAMES.get(cc, cc), "tiers": []}
            countries_data[cc]["tiers"].append({
                "tier": r.tier,
                "label": TIER_LABELS.get(r.tier, r.tier),
                "sentiment": round(float(r.avg_sentiment), 3),
                "article_count": r.article_count,
                "source_count": r.source_count,
            })

        # Calculate divergence + overall for each country
        result = []
        for cc, d in countries_data.items():
            sents = [t["sentiment"] for t in d["tiers"]]
            total_articles = sum(t["article_count"] for t in d["tiers"])
            weighted_sum = sum(t["sentiment"] * t["article_count"] for t in d["tiers"])
            overall = round(weighted_sum / total_articles, 3) if total_articles > 0 else 0

            divergence = round(max(sents) - min(sents), 3) if len(sents) >= 2 else 0.0

            # Identify which tier is most positive vs negative
            tier_sorted = sorted(d["tiers"], key=lambda t: t["sentiment"])
            most_negative = tier_sorted[0]["tier"] if tier_sorted else None
            most_positive = tier_sorted[-1]["tier"] if tier_sorted else None

            result.append({
                **d,
                "divergence": divergence,
                "overall_sentiment": overall,
                "total_articles": total_articles,
                "most_positive_tier": most_positive,
                "most_negative_tier": most_negative,
            })

        # Sort by divergence descending (most interesting first)
        result.sort(key=lambda x: -x["divergence"])

        return {"period_days": days, "countries": result}


@app.get("/api/v1/pipeline/stats")
def get_pipeline_stats_endpoint():
    """Get pipeline queue statistics."""
    try:
        from src.queue import get_pipeline_stats
        return get_pipeline_stats()
    except Exception as e:
        return {"error": str(e), "redis_available": False}


# ── Admin: API Usage & Monitoring ───────────────────────


@app.get("/api/v1/admin/usage")
def get_api_usage(
    period: str = Query(default="week", regex="^(day|week|month)$"),
    service: str = Query(default="all"),
):
    """Get API usage statistics grouped by date and service."""
    interval_map = {"day": "1 day", "week": "7 days", "month": "30 days"}
    interval = interval_map[period]

    service_filter = ""
    params: dict = {"interval": interval}
    if service != "all":
        service_filter = "AND service = :svc"
        params["svc"] = service

    with get_session() as session:
        rows = session.execute(
            text(f"""
                SELECT DATE(created_at) AS date,
                       service,
                       COUNT(*) AS total_calls,
                       SUM(tokens_in) AS tokens_in,
                       SUM(tokens_out) AS tokens_out,
                       SUM(cost_usd) AS cost_usd
                FROM api_usage
                WHERE created_at > NOW() - INTERVAL :interval
                  {service_filter}
                GROUP BY DATE(created_at), service
                ORDER BY date DESC, service
            """),
            params,
        ).fetchall()

        return {
            "period": period,
            "data": [
                {
                    "date": r.date.isoformat(),
                    "service": r.service,
                    "total_calls": r.total_calls,
                    "tokens_in": int(r.tokens_in or 0),
                    "tokens_out": int(r.tokens_out or 0),
                    "cost_usd": float(r.cost_usd or 0),
                }
                for r in rows
            ],
        }


@app.get("/api/v1/admin/keys")
def get_api_keys():
    """List API keys (masked) and their status."""
    keys_config = [
        {"service": "OpenRouter", "env_var": "OPENROUTER_API_KEY"},
        {"service": "Jina AI", "env_var": "JINA_API_KEY"},
        {"service": "Comtrade", "env_var": "COMTRADE_API_KEY"},
        {"service": "OpenAI", "env_var": "OPENAI_API_KEY"},
    ]

    result = []
    for kc in keys_config:
        value = os.environ.get(kc["env_var"], "")
        if value and len(value) > 12:
            masked = value[:8] + "…" + value[-4:]
            status = "active"
        elif value:
            masked = "***"
            status = "active"
        else:
            masked = ""
            status = "missing"

        result.append({
            "service": kc["service"],
            "env_var": kc["env_var"],
            "key_masked": masked,
            "status": status,
        })

    return {"keys": result}


@app.get("/api/v1/admin/health")
def get_api_health():
    """Ping external services and return latency/status."""
    import httpx

    services = [
        {
            "service": "OpenRouter",
            "url": "https://openrouter.ai/api/v1/models",
            "headers": {},
        },
        {
            "service": "Jina AI",
            "url": "https://api.jina.ai/",
            "headers": {},
        },
        {
            "service": "Comtrade",
            "url": "https://comtradeapi.un.org/public/v1/preview/C/A/HS?reporterCode=643&period=2023&partnerCode=398&flowCode=X&cmdCode=TOTAL",
            "headers": {},
        },
        {
            "service": "OpenAI",
            "url": "https://api.openai.com/v1/models",
            "headers": {},
        },
    ]

    results = []
    for svc in services:
        try:
            start = time.time()
            resp = httpx.get(svc["url"], headers=svc["headers"], timeout=10.0)
            latency_ms = int((time.time() - start) * 1000)
            results.append({
                "service": svc["service"],
                "status": "ok" if resp.status_code < 500 else "degraded",
                "http_code": resp.status_code,
                "latency_ms": latency_ms,
            })
        except Exception as e:
            results.append({
                "service": svc["service"],
                "status": "unreachable",
                "http_code": None,
                "latency_ms": None,
                "error": str(e)[:200],
            })

    return {"services": results}


@app.get("/api/v1/admin/summary")
def get_api_summary():
    """Get admin dashboard summary: costs, calls, top service/model."""
    with get_session() as session:
        # Today
        today = session.execute(text("""
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(cost_usd), 0) AS cost
            FROM api_usage
            WHERE created_at > DATE_TRUNC('day', NOW())
        """)).fetchone()

        # This week
        week = session.execute(text("""
            SELECT COALESCE(SUM(cost_usd), 0) AS cost
            FROM api_usage
            WHERE created_at > NOW() - INTERVAL '7 days'
        """)).fetchone()

        # This month
        month = session.execute(text("""
            SELECT COALESCE(SUM(cost_usd), 0) AS cost
            FROM api_usage
            WHERE created_at > NOW() - INTERVAL '30 days'
        """)).fetchone()

        # Top service
        top_svc = session.execute(text("""
            SELECT service, COUNT(*) AS cnt
            FROM api_usage
            WHERE created_at > NOW() - INTERVAL '7 days'
            GROUP BY service
            ORDER BY cnt DESC LIMIT 1
        """)).fetchone()

        # Top model
        top_model = session.execute(text("""
            SELECT model, COUNT(*) AS cnt
            FROM api_usage
            WHERE created_at > NOW() - INTERVAL '7 days'
              AND model IS NOT NULL AND model != ''
            GROUP BY model
            ORDER BY cnt DESC LIMIT 1
        """)).fetchone()

        return {
            "total_cost_today": float(today.cost) if today else 0,
            "total_cost_week": float(week.cost) if week else 0,
            "total_cost_month": float(month.cost) if month else 0,
            "calls_today": today.calls if today else 0,
            "top_service": top_svc.service if top_svc else None,
            "top_model": top_model.model if top_model else None,
        }


@app.get("/api/v1/admin/usage-by-script")
def get_usage_by_script(days: int = Query(default=7, ge=1, le=90)):
    """Get API usage grouped by script."""
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT script,
                       COUNT(*) AS total_calls,
                       SUM(tokens_in) AS tokens_in,
                       SUM(tokens_out) AS tokens_out,
                       SUM(cost_usd) AS cost_usd,
                       AVG(duration_ms) AS avg_duration_ms
                FROM api_usage
                WHERE created_at > NOW() - make_interval(days => :days)
                  AND script IS NOT NULL AND script != ''
                GROUP BY script
                ORDER BY cost_usd DESC
            """),
            {"days": days},
        ).fetchall()

        return {
            "period_days": days,
            "scripts": [
                {
                    "script": r.script,
                    "total_calls": r.total_calls,
                    "tokens_in": int(r.tokens_in or 0),
                    "tokens_out": int(r.tokens_out or 0),
                    "cost_usd": float(r.cost_usd or 0),
                    "avg_duration_ms": int(r.avg_duration_ms or 0),
                }
                for r in rows
            ],
        }




@app.get("/api/v1/countries/{code}/tiers/narrative")
def get_tiers_narrative(code: str, days: int = Query(default=14, le=365)):
    """Get narrative breakdown: per topic, show tier sentiments + top headlines.
    This powers the 'key divergences' section of the Narrative widget."""
    code = code.upper()
    with get_session() as session:
        # Per topic per tier: avg sentiment + count
        rows = session.execute(
            text("""
                SELECT an.event_type AS topic,
                       COALESCE(s.tier, 'mainstream') AS tier,
                       AVG(an.sentiment) AS avg_sentiment,
                       COUNT(*) AS article_count
                FROM analysis an
                JOIN articles ar ON an.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE s.country_code = :cc
                  AND an.is_relevant = true
                  AND an.sentiment IS NOT NULL
                  AND an.event_type IS NOT NULL
                  AND an.event_type != ''
                  AND ar.published_at > NOW() - make_interval(days => :days)
                GROUP BY an.event_type, COALESCE(s.tier, 'mainstream')
                HAVING COUNT(*) >= 1
                ORDER BY an.event_type, tier
            """),
            {"cc": code, "days": days},
        ).fetchall()

        # Top headline per topic per tier
        headline_rows = session.execute(
            text("""
                SELECT sub.topic, sub.tier, sub.title, sub.url, sub.sentiment, sub.source_name
                FROM (
                    SELECT an.event_type AS topic,
                           COALESCE(s.tier, 'mainstream') AS tier,
                           ar.title,
                           ar.url,
                           an.sentiment,
                           s.name AS source_name,
                           ROW_NUMBER() OVER (
                               PARTITION BY an.event_type, COALESCE(s.tier, 'mainstream')
                               ORDER BY ABS(an.sentiment) DESC, ar.published_at DESC
                           ) AS rn
                    FROM analysis an
                    JOIN articles ar ON an.article_id = ar.id
                    JOIN sources s ON ar.source_id = s.id
                    WHERE s.country_code = :cc
                      AND an.is_relevant = true
                      AND an.sentiment IS NOT NULL
                      AND an.event_type IS NOT NULL
                      AND an.event_type != ''
                      AND ar.title IS NOT NULL
                      AND ar.title != ''
                                                  AND ar.published_at > NOW() - make_interval(days => :days)
                ) sub
                WHERE sub.rn = 1
            """),
            {"cc": code, "days": days},
        ).fetchall()

        # Index headlines
        hl_index = {}
        for hr in headline_rows:
            hl_index[(hr.topic, hr.tier)] = {
                "title": hr.title or "",
                "url": hr.url or "",
                "sentiment": round(float(hr.sentiment), 2),
                "source": hr.source_name,
            }

        event_labels = {
            "economic": "Экономика", "military": "Военные", "diplomatic": "Дипломатия",
            "cultural": "Культура", "security": "Безопасность", "political": "Политика",
            "social": "Социальное", "energy": "Энергетика", "trade": "Торговля",
            "humanitarian": "Гуманитарное",
        }

        # Group by topic
        topics = {}
        for r in rows:
            topic = r.topic
            if topic not in topics:
                topics[topic] = {"tiers": [], "max_sent": -999, "min_sent": 999}
            avg_s = round(float(r.avg_sentiment), 2)
            topics[topic]["tiers"].append({
                "tier": r.tier,
                "label": TIER_LABELS.get(r.tier, r.tier),
                "sentiment": avg_s,
                "article_count": r.article_count,
                "headline": hl_index.get((topic, r.tier)),
            })
            if avg_s > topics[topic]["max_sent"]:
                topics[topic]["max_sent"] = avg_s
                topics[topic]["max_tier"] = r.tier
            if avg_s < topics[topic]["min_sent"]:
                topics[topic]["min_sent"] = avg_s
                topics[topic]["min_tier"] = r.tier

        # Build result sorted by divergence
        result = []
        for topic, data in topics.items():
            if len(data["tiers"]) < 2:
                continue
            div = round(data["max_sent"] - data["min_sent"], 2)
            result.append({
                "topic": topic,
                "label": event_labels.get(topic, topic.replace("_", " ").title()),
                "divergence": div,
                "most_positive_tier": data.get("max_tier"),
                "most_negative_tier": data.get("min_tier"),
                "tiers": sorted(data["tiers"], key=lambda t: -t["sentiment"]),
            })
        result.sort(key=lambda x: -x["divergence"])

        return {"country_code": code, "topics": result}


@app.get("/api/v1/countries/{code}/tiers/daily")
def get_tiers_daily(code: str, days: int = Query(default=30, ge=1, le=365)):
    """Get daily sentiment breakdown by source tier for heatmap."""
    code = code.upper()
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT DATE(ar.published_at) AS day,
                       COALESCE(s.tier, 'mainstream') AS tier,
                       AVG(an.sentiment) AS avg_sentiment
                FROM analysis an
                JOIN articles ar ON an.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE s.country_code = :cc
                  AND an.is_relevant = true
                  AND an.sentiment IS NOT NULL
                  AND ar.published_at > NOW() - make_interval(days => :days)
                GROUP BY DATE(ar.published_at), COALESCE(s.tier, 'mainstream')
                ORDER BY day ASC, tier
            """),
            {"cc": code, "days": days},
        ).fetchall()

        # Collect unique days and tiers
        day_set: dict[str, int] = {}
        tier_data: dict[str, list] = {}

        for r in rows:
            day_str = r.day.isoformat()
            if day_str not in day_set:
                day_set[day_str] = len(day_set)
            t = r.tier
            if t not in tier_data:
                tier_data[t] = {}
            tier_data[t][day_str] = round(float(r.avg_sentiment), 2)

        day_list = sorted(day_set.keys())

        tiers = []
        for t in TIER_ORDER:
            if t in tier_data:
                values = [tier_data[t].get(d, None) for d in day_list]
                tiers.append({
                    "tier": t,
                    "label": TIER_LABELS.get(t, t),
                    "values": values,
                })

        # Also include tiers not in TIER_ORDER
        for t in tier_data:
            if t not in TIER_ORDER:
                values = [tier_data[t].get(d, None) for d in day_list]
                tiers.append({
                    "tier": t,
                    "label": TIER_LABELS.get(t, t),
                    "values": values,
                })

        return {"days": day_list, "tiers": tiers}


@app.get("/api/v1/countries/{code}/divergence/history")
def get_divergence_history(code: str, days: int = Query(default=30, ge=1, le=365)):
    """Get daily narrative divergence history (max tier sentiment - min tier sentiment per day)."""
    code = code.upper()
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT day, MAX(avg_sent) - MIN(avg_sent) AS divergence
                FROM (
                    SELECT DATE(ar.published_at) AS day,
                           COALESCE(s.tier, 'mainstream') AS tier,
                           AVG(an.sentiment) AS avg_sent
                    FROM analysis an
                    JOIN articles ar ON an.article_id = ar.id
                    JOIN sources s ON ar.source_id = s.id
                    WHERE s.country_code = :cc
                      AND an.is_relevant = true
                      AND an.sentiment IS NOT NULL
                      AND ar.published_at > NOW() - make_interval(days => :days)
                    GROUP BY DATE(ar.published_at), COALESCE(s.tier, 'mainstream')
                ) sub
                GROUP BY day
                HAVING COUNT(DISTINCT tier) >= 2
                ORDER BY day ASC
            """),
            {"cc": code, "days": days},
        ).fetchall()

        return {
            "data": [
                {
                    "date": r.day.isoformat(),
                    "divergence": round(float(r.divergence), 2),
                }
                for r in rows
            ],
        }


@app.get("/api/v1/countries/{code}/topics/divergence")
def get_topics_divergence(code: str, days: int = Query(default=30, ge=1, le=365)):
    """Get divergence by event_type (topic): for each topic, max - min tier sentiment."""
    code = code.upper()
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT event_type,
                       MAX(avg_sent) - MIN(avg_sent) AS divergence
                FROM (
                    SELECT an.event_type,
                           COALESCE(s.tier, 'mainstream') AS tier,
                           AVG(an.sentiment) AS avg_sent
                    FROM analysis an
                    JOIN articles ar ON an.article_id = ar.id
                    JOIN sources s ON ar.source_id = s.id
                    WHERE s.country_code = :cc
                      AND an.is_relevant = true
                      AND an.sentiment IS NOT NULL
                      AND an.event_type IS NOT NULL
                      AND an.event_type != ''
                      AND ar.published_at > NOW() - make_interval(days => :days)
                    GROUP BY an.event_type, COALESCE(s.tier, 'mainstream')
                ) sub
                GROUP BY event_type
                HAVING COUNT(DISTINCT tier) >= 2
                ORDER BY divergence DESC
            """),
            {"cc": code, "days": days},
        ).fetchall()

        event_labels = {
            "economic": "Экономика",
            "military": "Военные",
            "diplomatic": "Дипломатия",
            "cultural": "Культура",
            "security": "Безопасность",
            "political": "Политика",
            "social": "Социальное",
            "energy": "Энергетика",
            "trade": "Торговля",
            "humanitarian": "Гуманитарное",
        }

        return {
            "data": [
                {
                    "topic": r.event_type,
                    "label": event_labels.get(r.event_type, r.event_type.replace("_", " ").title()),
                    "divergence": round(float(r.divergence), 2),
                }
                for r in rows
            ],
        }

@app.get("/api/v1/headline")
def get_headline():
    """Generate a dynamic headline based on the most anomalous country."""
    with get_session() as session:
        row = session.execute(text("""
            SELECT country_code, temperature, anomaly_score, trend
            FROM temperature
            WHERE time = (SELECT MAX(time) FROM temperature)
            ORDER BY ABS(anomaly_score) DESC NULLS LAST, ABS(temperature) DESC
            LIMIT 1
        """)).fetchone()

        if not row:
            return {"headline": None, "subline": None}

        code = row[0]
        temp = float(row[1]) if row[1] else 0
        anomaly = float(row[2]) if row[2] else 0
        name = COUNTRY_NAMES.get(code, code)

        headline_type = "anomaly" if abs(anomaly) > 2 else "temperature"

        if anomaly > 3:
            headline = f"{name}: аномальный рост напряжённости"
            subline = f"Температура {temp:+.1f}° при аномалии {anomaly:.1f}σ — статистическое отклонение превышает три стандартных отклонения."
        elif anomaly < -3:
            headline = f"{name} в медийном штопоре"
            subline = f"Показывает критические аномалии с температурой {temp:+.1f}°. Отклонение {abs(anomaly):.1f}σ — рекордный показатель среди стран СНГ."
        elif temp < -20:
            headline = f"{name}: глубокая заморозка отношений"
            subline = f"Температура опустилась до {temp:+.1f}° — одна из самых холодных точек на карте СНГ."
        elif temp > 20:
            headline = f"{name}: пик позитивной динамики"
            subline = f"Температура {temp:+.1f}° — самый тёплый показатель среди отслеживаемых стран."
        else:
            headline = f"{name}: {temp:+.1f}° — ключевой сигнал дня"
            subline = f"Аномалия {anomaly:.1f}σ при температуре {temp:+.1f}°. Требует внимания аналитиков."

        return {
            "headline": headline,
            "subline": subline,
            "country_code": code,
            "type": headline_type,
            "generated": True
        }


@app.get("/api/v1/events/high-impact")
def get_high_impact_events(
    days: int = Query(default=14, ge=1, le=365),
    min_action_level: int = Query(default=3, ge=1, le=6),
    limit: int = Query(default=10, ge=1, le=50),
):
    """Get high-impact events across all countries."""
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT ar.title, ar.url, s.name AS source, 
                       COALESCE(s.tier, 'mainstream') AS tier,
                       s.country_code,
                       an.sentiment, an.action_level, an.event_type,
                       ar.published_at
                FROM analysis an
                JOIN articles ar ON an.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE an.is_relevant = true
                  AND an.action_level >= :min_al
                  AND ar.published_at > NOW() - make_interval(days => :days)
                  AND s.country_code IS NOT NULL
                ORDER BY ar.published_at DESC
                LIMIT :lim
            """),
            {"min_al": min_action_level, "days": days, "lim": limit},
        ).fetchall()

        return {
            "events": [
                {
                    "title": r.title,
                    "url": r.url,
                    "source": r.source,
                    "tier": r.tier,
                    "country_code": r.country_code,
                    "country_name": COUNTRY_NAMES.get(r.country_code, r.country_code),
                    "sentiment": float(r.sentiment) if r.sentiment else 0,
                    "action_level": r.action_level,
                    "event_type": r.event_type,
                    "published_at": r.published_at.isoformat() if r.published_at else None,
                }
                for r in rows
            ],
        }


@app.get("/api/v1/audience-split")
def get_audience_split(
    country: str = Query(default=None),
    source: str = Query(default=None),
    days: int = Query(default=30, ge=1, le=365),
):
    """Get audience split data — bilingual sources with divergent sentiment."""
    with get_session() as session:
        filters = ["ap.detected_at > NOW() - make_interval(days => :days)"]
        params = {"days": days}

        if country:
            filters.append("s.country_code = :country")
            params["country"] = country.upper()
        if source:
            filters.append("s.name ILIKE :source")
            params["source"] = f"%{source}%"

        where_clause = " AND ".join(filters)

        rows = session.execute(
            text(f"""
                SELECT
                    s.id as source_id,
                    s.name as source_name,
                    s.country_code,
                    ap.article_id_1, ap.article_id_2,
                    ap.similarity, ap.sentiment_delta,
                    ap.lang_1, ap.lang_2,
                    a1.title as title_1, a1.url as url_1,
                    a1.body as body_1, a1.published_at as pub_1,
                    a2.title as title_2, a2.url as url_2,
                    a2.body as body_2, a2.published_at as pub_2,
                    an1.sentiment as sent_1,
                    an2.sentiment as sent_2
                FROM article_pairs ap
                JOIN sources s ON ap.source_id = s.id
                JOIN articles a1 ON ap.article_id_1 = a1.id
                JOIN articles a2 ON ap.article_id_2 = a2.id
                LEFT JOIN analysis an1 ON an1.article_id = a1.id
                LEFT JOIN analysis an2 ON an2.article_id = a2.id
                WHERE {where_clause}
                ORDER BY ap.sentiment_delta DESC
                LIMIT 500
            """),
            params,
        ).fetchall()

        source_map = {}
        for r in rows:
            key = r.source_id
            if key not in source_map:
                source_map[key] = {
                    "source": r.source_name,
                    "country_code": r.country_code.strip() if r.country_code else "",
                    "pairs": [],
                    "sentiments_by_lang": {},
                }
            sm = source_map[key]

            pair_data = {
                "similarity": float(r.similarity) if r.similarity else 0,
                "delta": float(r.sentiment_delta) if r.sentiment_delta else 0,
                "published_at": min(r.pub_1, r.pub_2).date().isoformat() if r.pub_1 and r.pub_2 else None,
            }

            lang1 = r.lang_1 or "ru"
            lang2 = r.lang_2 or "en"
            sent1 = float(r.sent_1) if r.sent_1 is not None else 0
            sent2 = float(r.sent_2) if r.sent_2 is not None else 0

            pair_data[f"article_{lang1}"] = {
                "id": r.article_id_1,
                "title": r.title_1,
                "sentiment": sent1,
                "url": r.url_1,
                "body_preview": (r.body_1 or "")[:200],
            }
            pair_data[f"article_{lang2}"] = {
                "id": r.article_id_2,
                "title": r.title_2,
                "sentiment": sent2,
                "url": r.url_2,
                "body_preview": (r.body_2 or "")[:200],
            }

            sm["pairs"].append(pair_data)

            for lang, sent in [(lang1, sent1), (lang2, sent2)]:
                if lang not in sm["sentiments_by_lang"]:
                    sm["sentiments_by_lang"][lang] = []
                sm["sentiments_by_lang"][lang].append(sent)

        splits = []
        total_significant = 0
        all_deltas = []

        for key, sm in source_map.items():
            lang_avgs = {}
            for lang, sents in sm["sentiments_by_lang"].items():
                lang_avgs[lang] = round(sum(sents) / len(sents), 2) if sents else 0

            lang_keys = sorted(lang_avgs.keys())
            if len(lang_keys) >= 2:
                delta = abs(lang_avgs[lang_keys[0]] - lang_avgs[lang_keys[1]])
            elif sm["pairs"]:
                delta = max(p["delta"] for p in sm["pairs"])
            else:
                delta = 0

            split_entry = {
                "source": sm["source"],
                "country_code": sm["country_code"],
                "pairs_count": len(sm["pairs"]),
                "delta": round(delta, 2),
                "pairs": sm["pairs"][:20],
            }

            for lang, avg in lang_avgs.items():
                split_entry[f"avg_sentiment_{lang}"] = avg

            splits.append(split_entry)
            all_deltas.append(delta)
            if delta > 0.3:
                total_significant += 1

        splits.sort(key=lambda x: x["delta"], reverse=True)

        bilingual_filters = ["a.language IS NOT NULL", "a.published_at > NOW() - make_interval(days => :days)"]
        bilingual_params = {"days": days}
        if country:
            bilingual_filters.append("s.country_code = :bc")
            bilingual_params["bc"] = country.upper()
        bilingual_where = " AND ".join(bilingual_filters)
        bilingual_count = session.execute(
            text(f"""
                SELECT COUNT(*) FROM (
                    SELECT s.id
                    FROM sources s
                    JOIN articles a ON a.source_id = s.id
                    WHERE {bilingual_where}
                    GROUP BY s.id
                    HAVING COUNT(DISTINCT a.language) >= 2
                ) sub
            """),
            bilingual_params,
        ).scalar() or 0

        return {
            "splits": splits,
            "summary": {
                "total_bilingual_sources": bilingual_count,
                "sources_with_significant_split": total_significant,
                "avg_split": round(sum(all_deltas) / len(all_deltas), 2) if all_deltas else 0,
            },
        }
