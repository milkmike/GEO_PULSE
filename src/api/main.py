"""FastAPI application."""
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from src.config import COUNTRY_NAMES, COUNTRY_ISO3
from src.db import get_session
from src.api.routes.sources import router as sources_router
from src.api.routes.threads import router as threads_router

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
    "mainstream": "📰 Mainstream",
    "analytics": "🔍 Аналитика",
    "social": "💬 Соцсети",
    "opposition": "📢 Оппозиция",
}

TIER_ORDER = ["official", "mainstream", "analytics", "social", "opposition"]


@app.get("/api/v1/countries/{code}/tiers")
def get_country_tiers(code: str, days: int = Query(default=14, le=365)):
    """Get sentiment breakdown by source tier for a country."""
    code = code.upper()
    with get_session() as session:
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
                  AND ar.published_at > NOW() - INTERVAL ':days days'
            """.replace(":days", str(days))),
            {"cc": code},
        ).fetchall()

        # Aggregate by tier
        tier_data = {}
        for row in rows:
            t = row.tier or "mainstream"
            if t not in tier_data:
                tier_data[t] = {"sentiments": [], "sources": set()}
            tier_data[t]["sentiments"].append(float(row.sentiment))
            tier_data[t]["sources"].add(row.source_name)

        tiers = []
        sentiments_all = []
        for t in TIER_ORDER:
            if t in tier_data:
                d = tier_data[t]
                avg = sum(d["sentiments"]) / len(d["sentiments"])
                sentiments_all.append(avg)
                tiers.append({
                    "tier": t,
                    "label": TIER_LABELS.get(t, t),
                    "sentiment": round(avg, 2),
                    "article_count": len(d["sentiments"]),
                    "sources": sorted(d["sources"]),
                })

        # Overall
        all_sentiments = [float(r.sentiment) for r in rows]
        overall = round(sum(all_sentiments) / len(all_sentiments), 2) if all_sentiments else 0

        # Divergence
        divergence = 0.0
        if sentiments_all:
            divergence = round(max(sentiments_all) - min(sentiments_all), 2)

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
