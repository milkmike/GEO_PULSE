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
import json
import logging
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone

from sqlalchemy import text

from src.collectors.publisher_attribution import (
    expected_site_domain,
    feed_mode,
    is_aggregator_domain,
    normalize_publisher_domain,
)
from src.countries import COUNTRIES, country_name_ru
from src.db import get_session

logger = logging.getLogger(__name__)

MIN_EXPECTED_HOURS = 2.0
MAX_EXPECTED_HOURS = 72.0
DEAD_DAYS = 7

# GDELT groups: a country's gdelt_daily row should refresh at least daily;
# the collector runs every 6h
GDELT_STALE_HOURS = 26
_CACHE_TTL_SECONDS = 300
_HEALTH_CACHE_KEY = "cache:v2:health"
_SOURCE_COVERAGE_CACHE_KEY = "cache:v2:health:source-coverage"


def _cache_get(key: str):
    """Read a JSON cache entry; cache outages must never hide live health data."""
    try:
        from src.queue import get_redis
        payload = get_redis().get(key)
        return json.loads(payload) if payload else None
    except Exception:
        return None


def _cache_set(key: str, payload: dict, ttl: int = _CACHE_TTL_SECONDS) -> None:
    try:
        from src.queue import get_redis
        get_redis().setex(key, ttl, json.dumps(payload))
    except Exception:
        pass


def source_health() -> list[dict]:
    """Per-source freshness with learned cadence."""
    now = datetime.now(timezone.utc)
    with get_session() as session:
        # Fetch-health columns (migration 015) may not exist yet on a freshly
        # deployed but not-yet-migrated DB — select NULLs instead of 500ing.
        has_fetch_cols = session.execute(
            text("""SELECT COUNT(*) FROM information_schema.columns
                    WHERE table_name = 'sources' AND column_name = 'last_status'""")
        ).scalar()
        fetch_cols = (
            "s.last_status, s.last_error, s.consecutive_failures, s.last_fetch_at"
            if has_fetch_cols else
            "NULL AS last_status, NULL AS last_error, "
            "0 AS consecutive_failures, NULL AS last_fetch_at"
        )
        rows = session.execute(
            text(f"""
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
                       s.url, s.config, s.state_affiliated,
                       {fetch_cols},
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
            "url": r.url,
            "config": r.config or {},
            "state_affiliated": bool(r.state_affiliated),
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


INDEPENDENT_TIERS = {"independent", "domestic_opposition"}


def _publisher_family_identities(source: dict) -> set[str]:
    """Return the curated identities which connect one publisher source."""
    config = source.get("config")
    config = config if isinstance(config, Mapping) else {}
    mode = feed_mode(source.get("url"), config)

    canonical = normalize_publisher_domain(config.get("publisher_domain"))
    raw_aliases = config.get("publisher_domain_aliases", config.get("publisher_aliases", ()))
    if isinstance(raw_aliases, str):
        raw_aliases = (raw_aliases,)
    if not isinstance(raw_aliases, (list, tuple, set)):
        raw_aliases = ()

    site_domain = expected_site_domain(source.get("url"))
    fallback_domain = None
    if canonical is None and site_domain is None:
        fallback_domain = normalize_publisher_domain(source.get("url"))

    identities = {
        domain
        for value in (canonical, *raw_aliases, site_domain, fallback_domain)
        if isinstance(value, str)
        for domain in (normalize_publisher_domain(value),)
        if domain
    }
    feed_domain = normalize_publisher_domain(source.get("url"))
    valid_google_site_wrapper = mode == "site_wrapper" and site_domain is not None
    if (
        any(is_aggregator_domain(domain) for domain in identities)
        or (
            is_aggregator_domain(feed_domain)
            and not valid_google_site_wrapper
        )
    ):
        return set()
    return identities


def _publisher_families(sources: list[dict]) -> dict[str, list[dict]]:
    """Build connected publisher families from overlapping domain identities."""
    families: list[dict] = []
    for source in sources:
        identities = _publisher_family_identities(source)
        if not identities:
            continue
        matching = [family for family in families if family["identities"] & identities]
        if not matching:
            families.append({"identities": identities, "sources": [source]})
            continue

        combined_identities = set(identities)
        combined_sources = [source]
        for family in matching:
            combined_identities.update(family["identities"])
            combined_sources.extend(family["sources"])
            families.remove(family)
        families.append({
            "identities": combined_identities,
            "sources": combined_sources,
        })

    return {
        min(family["identities"]): family["sources"]
        for family in families
    }


def source_coverage(sources: list[dict] | None = None) -> dict:
    """Report distinct, working direct-publisher coverage by country."""
    cacheable = sources is None
    if cacheable:
        cached = _cache_get(_SOURCE_COVERAGE_CACHE_KEY)
        if cached is not None:
            return cached
        sources = source_health()
    grouped = defaultdict(list)
    for source in sources:
        grouped[str(source["country_code"]).strip().upper()].append(source)

    countries = []
    states = Counter()
    for code in sorted(COUNTRIES):
        rows = grouped.get(code, [])
        discovery = [
            row for row in rows
            if feed_mode(row.get("url"), row.get("config") or {}) == "publisher_discovery"
        ]
        direct = [
            row for row in rows
            if row not in discovery and row.get("type") in {"rss", "web"}
        ]
        by_domain = _publisher_families(direct)
        working = {
            domain: family for domain, family in by_domain.items()
            if any(row.get("last_status") == "ok" for row in family)
        }
        mix = {"official": 0, "mainstream": 0, "independent": 0}
        for family in working.values():
            tiers = {row.get("tier") for row in family}
            if any(row.get("state_affiliated") for row in family) or "official" in tiers:
                mix["official"] += 1
            if "mainstream" in tiers:
                mix["mainstream"] += 1
            if tiers & INDEPENDENT_TIERS:
                mix["independent"] += 1
        count = len(working)
        state = (
            "uncovered" if count == 0 else
            "thin" if count < 3 else
            "balanced" if all(mix.values()) else
            "baseline"
        )
        states[state] += 1
        countries.append({
            "country_code": code,
            "country_name": country_name_ru(code),
            "configured": len(rows),
            "discovery": len(discovery),
            "direct_publishers": len(by_domain),
            "working_direct_publishers": count,
            "mix": mix,
            "duplicate_families": sorted(
                domain for domain, family in by_domain.items() if len(family) > 1
            ),
            "target_state": state,
        })
    payload = {"summary": dict(states), "countries": countries}
    if cacheable:
        _cache_set(_SOURCE_COVERAGE_CACHE_KEY, payload, _CACHE_TTL_SECONDS)
    return payload


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
    cached = _cache_get(_HEALTH_CACHE_KEY)
    if cached is not None:
        return cached
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

    payload = {
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
    _cache_set(_HEALTH_CACHE_KEY, payload, _CACHE_TTL_SECONDS)
    return payload
