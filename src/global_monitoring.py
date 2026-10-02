"""Bounded, source-transparent monitoring inventory across all foreign areas.

The article slice is a sample of local publisher output, never a count of
events in that country. It performs no model or network requests.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from src.collectors.publisher_attribution import feed_mode
from src.db import get_session
from src.early_signals import source_key
from src.engine.health import _publisher_families
from src.monitoring_registry import MONITORING_COUNTRIES, all_codes


_SOURCES = text("""
    SELECT id,name,TRIM(country_code) AS country_code,source_type AS type,
           url,config,tier,state_affiliated,last_status,last_fetch_at
    FROM sources WHERE active IS TRUE AND TRIM(country_code)=ANY(CAST(:codes AS text[]))
    ORDER BY country_code,id
""")

# Probe each resolved publisher separately before the country cap. The source
# and attributed-publisher indexes keep a prolific feed from hiding a quieter
# publisher beyond a country-wide top-N cut. A final round robin bounds output.
_ARTICLES = text("""
    WITH publisher_sample AS (
      SELECT target.code,publisher.id AS source_id,publisher.name AS source_name,
             TRIM(publisher.country_code) AS country_code,
             ar.id,ar.title,ar.excerpt,ar.url,ar.published_at,ar.collected_at
      FROM unnest(CAST(:codes AS text[])) AS target(code)
      JOIN sources publisher ON publisher.country_code=target.code
      CROSS JOIN LATERAL (
        SELECT candidates.* FROM (
          (SELECT article.id,article.title,
                  LEFT(COALESCE(NULLIF(article.body,''),article.summary,''),2000) AS excerpt,
                  COALESCE(NULLIF(article.resolved_url,''),article.url) AS url,
                  article.published_at,article.collected_at
           FROM articles article
           WHERE article.source_id=publisher.id AND article.publisher_source_id IS NULL
             AND COALESCE(publisher.config->>'feed_mode','publisher')<>'publisher_discovery'
             AND article.geo_country_code=target.code
             AND article.is_duplicate IS FALSE
             AND article.geo_status IN ('source_verified','publisher_verified','publisher_reassigned')
             AND article.published_at>=:week_start AND article.published_at<=:as_of
             AND article.collected_at>=article.published_at AND article.collected_at<=:as_of
             AND length(trim(COALESCE(article.title,'')))>0
             AND COALESCE(NULLIF(article.resolved_url,''),article.url) IS NOT NULL
           ORDER BY article.published_at DESC,article.id DESC LIMIT :publisher_cap)
          UNION ALL
          (SELECT article.id,article.title,
                  LEFT(COALESCE(NULLIF(article.body,''),article.summary,''),2000) AS excerpt,
                  COALESCE(NULLIF(article.resolved_url,''),article.url) AS url,
                  article.published_at,article.collected_at
           FROM articles article
           WHERE article.publisher_source_id=publisher.id
             AND article.geo_country_code=target.code
             AND article.is_duplicate IS FALSE
             AND article.geo_status IN ('source_verified','publisher_verified','publisher_reassigned')
             AND article.published_at>=:week_start AND article.published_at<=:as_of
             AND article.collected_at>=article.published_at AND article.collected_at<=:as_of
             AND length(trim(COALESCE(article.title,'')))>0
             AND COALESCE(NULLIF(article.resolved_url,''),article.url) IS NOT NULL
           ORDER BY article.published_at DESC,article.id DESC LIMIT :publisher_cap)
        ) candidates
        ORDER BY candidates.published_at DESC,candidates.id DESC LIMIT :publisher_cap
      ) ar
    ), publisher_ranked AS (
      SELECT *,row_number() OVER (
        PARTITION BY code,source_id ORDER BY published_at DESC,id DESC) AS source_rank
      FROM publisher_sample
    ), country_ranked AS (
      SELECT *,row_number() OVER (
        PARTITION BY code ORDER BY source_rank,published_at DESC,id DESC) AS country_rank
      FROM publisher_ranked
    )
    SELECT code,id,title,excerpt,source_id,source_name,country_code,url,published_at,collected_at
    FROM country_ranked WHERE country_rank<=:per_country
    ORDER BY code,country_rank
""")

_ARTICLE_FIELDS = ("id", "title", "excerpt", "source_id", "source_name",
                   "country_code", "url", "published_at", "collected_at")


def _instant(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return value.astimezone(timezone.utc)


def _fair_sample(rows: list[dict], limit: int) -> list[dict]:
    """Take newest reports round-robin by resolved publisher, at most eight each."""
    buckets = defaultdict(list)
    for row in rows:
        buckets[row["source_id"]].append(row)
    queues = {publisher: deque(sorted(items, key=lambda r: (r["published_at"], r["id"]), reverse=True)[:8])
              for publisher, items in buckets.items()}
    order = sorted(queues, key=lambda publisher: (-queues[publisher][0]["published_at"].timestamp(), publisher))
    selected = []
    while len(selected) < limit and any(queues.values()):
        for publisher in order:
            if queues[publisher]:
                selected.append(queues[publisher].popleft())
                if len(selected) == limit:
                    break
    return selected


def _source_counts(rows: list[dict], *, as_of: datetime) -> dict[str, dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[row["country_code"]].append(row)
    result = {}
    for code, configured in groups.items():
        direct = [row for row in configured
                  if row["type"] in {"rss", "web"}
                  and feed_mode(row["url"], row.get("config") or {}) != "publisher_discovery"]
        families = _publisher_families(direct)
        def recently_fetched(source):
            fetched = source.get("last_fetch_at")
            if source.get("last_status") != "ok" or fetched is None:
                return False
            try:
                when = _instant(fetched)
            except (ValueError, TypeError):
                return False
            return as_of - timedelta(hours=72) <= when <= as_of

        working = sum(any(recently_fetched(source) for source in family)
                      for family in families.values())
        result[code] = {"configured_sources": len(configured),
                        "working_direct_publishers": working}
    return result


def load_global_monitoring(*, as_of: datetime | None = None, per_country: int = 40) -> dict:
    """Snapshot every area, including zero-source countries, without paid I/O."""
    if type(per_country) is not int or not 1 <= per_country <= 40:
        raise ValueError("per_country must be 1..40")
    now = _instant(as_of)
    if now > datetime.now(timezone.utc):
        raise ValueError("future as_of")
    codes = all_codes()
    with get_session() as session:
        session.execute(text("SET LOCAL statement_timeout='15s'"))
        source_rows = [dict(row) for row in session.execute(_SOURCES, {"codes": codes}).mappings().all()]
        article_rows = [dict(row) for row in session.execute(_ARTICLES, {
            "codes": codes, "week_start": now - timedelta(days=7), "as_of": now,
            "publisher_cap": 8, "per_country": per_country,
        }).mappings().all()]
    counts = _source_counts(source_rows, as_of=now)
    by_code = defaultdict(list)
    for row in article_rows:
        if row["code"] not in MONITORING_COUNTRIES:
            continue
        article = {field: row[field] for field in _ARTICLE_FIELDS}
        try:
            source_key(article)  # Reject unsafe URLs and malformed provenance.
        except (ValueError, TypeError, KeyError):
            continue
        by_code[row["code"]].append(article)
    countries, articles = [], []
    for code, meta in MONITORING_COUNTRIES.items():
        picked = _fair_sample(by_code[code], per_country)
        articles.extend({**article, "published_at": article["published_at"].isoformat(),
                         "collected_at": article["collected_at"].isoformat()}
                        for article in picked)
        source = counts.get(code, {"configured_sources": 0, "working_direct_publishers": 0})
        gaps = []
        if not source["configured_sources"]:
            gaps.append("no_active_sources")
        elif not source["working_direct_publishers"]:
            gaps.append("no_successful_direct_fetch")
        if not picked:
            gaps.append("no_qualifying_sample_in_7d")
        state = ("sampled_articles" if picked else
                 "no_sources" if not source["configured_sources"] else
                 "no_working_direct_publishers" if not source["working_direct_publishers"] else
                 "no_recent_articles")
        countries.append({"code": code, "name_ru": meta["name_ru"],
                          **source, "sampled_articles_7d": len(picked),
                          "latest_local_published_at": max((a["published_at"] for a in picked), default=None).isoformat()
                          if picked else None,
                          "latest_local_collected_at": max((a["collected_at"] for a in picked), default=None).isoformat()
                          if picked else None,
                          "coverage_state": state, "source_gaps": gaps})
    return {"as_of": now.isoformat(), "countries": countries, "articles": articles,
            "limits": {"per_country": per_country, "window_days": 7,
                       "publisher_cap": 8,
                       "counts_are_bounded": True,
                       "working_direct_publishers_meaning": "successful_direct_fetch_within_72h_not_article_freshness",
                       "country_code_meaning": "publisher_origin_not_event_location"}}
