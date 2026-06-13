"""Source freshness & coverage health monitor (worldmonitor-style verdicts).

Every source gets a learned cadence (median interval between its articles
over 30 days) and a status:

  OK     last article within 3× its median cadence (min 2h, max 72h)
  STALE  beyond expected cadence — feed alive recently but quiet too long
  DEAD   nothing for 7+ days (or never collected)

Coverage matters because silent source failures bias the index: if a tier
goes dark, the temperature drifts toward the remaining tiers' tone. The
summary therefore reports per-country tier coverage and an overall verdict:
HEALTHY / WARNING / DEGRADED / UNHEALTHY.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from src.countries import country_name_ru
from src.db import get_session

logger = logging.getLogger(__name__)

MIN_EXPECTED_HOURS = 2.0
MAX_EXPECTED_HOURS = 72.0
DEAD_DAYS = 7

# GDELT groups: a country's gdelt_daily row should refresh at least daily;
# the collector runs every 6h
GDELT_STALE_HOURS = 26


def source_health() -> list[dict]:
    """Per-source freshness with learned cadence."""
    now = datetime.now(timezone.utc)
    with get_session() as session:
        rows = session.execute(
            text("""
                WITH cadence AS (
                    SELECT ar.source_id,
                           PERCENTILE_CONT(0.5) WITHIN GROUP (
                               ORDER BY EXTRACT(EPOCH FROM diff)
                           ) AS median_gap_sec
                    FROM (
                        SELECT source_id,
                               published_at - LAG(published_at) OVER (
                                   PARTITION BY source_id ORDER BY published_at
                               ) AS diff
                        FROM articles
                        WHERE published_at > NOW() - INTERVAL '30 days'
                    ) ar
                    WHERE diff IS NOT NULL AND diff > INTERVAL '0'
                    GROUP BY ar.source_id
                ),
                last_seen AS (
                    SELECT source_id, MAX(published_at) AS last_at, COUNT(*) AS n30
                    FROM articles
                    WHERE published_at > NOW() - INTERVAL '30 days'
                    GROUP BY source_id
                )
                SELECT s.id, s.name, s.country_code, s.tier, s.source_type, s.active,
                       s.last_status, s.last_error, s.consecutive_failures, s.last_fetch_at,
                       ls.last_at, ls.n30, c.median_gap_sec
                FROM sources s
                LEFT JOIN last_seen ls ON ls.source_id = s.id
                LEFT JOIN cadence c ON c.source_id = s.id
                WHERE s.active = TRUE
                ORDER BY s.country_code, s.tier, s.name
            """)
        ).fetchall()

    result = []
    for r in rows:
        median_h = (float(r.median_gap_sec) / 3600) if r.median_gap_sec else None
        expected_h = MAX_EXPECTED_HOURS
        if median_h:
            expected_h = min(MAX_EXPECTED_HOURS, max(MIN_EXPECTED_HOURS, median_h * 3))

        if r.last_at is None:
            status, silent_h = "DEAD", None
        else:
            silent_h = (now - r.last_at).total_seconds() / 3600
            if silent_h > DEAD_DAYS * 24:
                status = "DEAD"
            elif silent_h > expected_h:
                status = "STALE"
            else:
                status = "OK"

        result.append({
            "source_id": r.id,
            "name": r.name,
            "country_code": r.country_code,
            "tier": r.tier,
            "type": r.source_type,
            "status": status,
            "last_article_at": r.last_at.isoformat() if r.last_at else None,
            "silent_hours": round(silent_h, 1) if silent_h is not None else None,
            "expected_max_hours": round(expected_h, 1),
            "articles_30d": int(r.n30 or 0),
            # Fetch-level diagnosis (migration 015): why the source is quiet.
            "last_status": r.last_status,
            "last_error": r.last_error,
            "consecutive_failures": int(r.consecutive_failures or 0),
            "last_fetch_at": r.last_fetch_at.isoformat() if r.last_fetch_at else None,
        })
    return result


def gdelt_health() -> dict:
    """Freshness of the GDELT observation group."""
    with get_session() as session:
        row = session.execute(
            text("""
                SELECT MAX(fetched_at) AS last_fetch,
                       COUNT(DISTINCT country_code) FILTER (
                           WHERE day > CURRENT_DATE - 3
                       ) AS countries_fresh
                FROM gdelt_daily
            """)
        ).fetchone()

    now = datetime.now(timezone.utc)
    last_fetch = row.last_fetch if row else None
    age_h = (now - last_fetch).total_seconds() / 3600 if last_fetch else None
    status = "DEAD"
    if age_h is not None:
        status = "OK" if age_h <= GDELT_STALE_HOURS else "STALE"
    return {
        "status": status,
        "last_fetch_at": last_fetch.isoformat() if last_fetch else None,
        "age_hours": round(age_h, 1) if age_h is not None else None,
        "countries_with_fresh_data": int(row.countries_fresh or 0) if row else 0,
    }


def health_summary() -> dict:
    """Aggregated verdict: per-country coverage + overall state."""
    sources = source_health()

    by_country: dict[str, dict] = {}
    for s in sources:
        c = by_country.setdefault(s["country_code"], {"ok": 0, "stale": 0, "dead": 0, "tiers": {}})
        key = s["status"].lower()
        c[key] = c.get(key, 0) + 1
        t = c["tiers"].setdefault(s["tier"], {"ok": 0, "total": 0})
        t["total"] += 1
        if s["status"] == "OK":
            t["ok"] += 1

    countries = []
    for code, c in sorted(by_country.items()):
        total = c["ok"] + c["stale"] + c["dead"]
        coverage = c["ok"] / total if total else 0
        degraded_tiers = [
            tier for tier, t in c["tiers"].items()
            if t["total"] > 0 and t["ok"] == 0
        ]
        countries.append({
            "country_code": code,
            "country_name": country_name_ru(code),
            "sources_total": total,
            "ok": c["ok"], "stale": c["stale"], "dead": c["dead"],
            "coverage_pct": round(coverage * 100, 1),
            "degraded_tiers": degraded_tiers,
        })

    total = len(sources)
    ok = sum(1 for s in sources if s["status"] == "OK")
    dead = sum(1 for s in sources if s["status"] == "DEAD")
    coverage = (ok / total * 100) if total else 0.0

    gdelt = gdelt_health()

    if total == 0:
        verdict = "UNHEALTHY"
    elif coverage >= 80 and gdelt["status"] == "OK":
        verdict = "HEALTHY"
    elif coverage >= 60:
        verdict = "WARNING"
    elif coverage >= 40:
        verdict = "DEGRADED"
    else:
        verdict = "UNHEALTHY"

    return {
        "verdict": verdict,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "sources_total": total,
        "sources_ok": ok,
        "sources_stale": total - ok - dead,
        "sources_dead": dead,
        "coverage_pct": round(coverage, 1),
        "gdelt": gdelt,
        "countries": countries,
    }
