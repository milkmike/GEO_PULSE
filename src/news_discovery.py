"""Read-only, explicitly unreviewed news leads for the decision workspace.

No model clients are imported here. Country involvement comes from the cached
classification only when its primary country is resolved. Local collection and
processing counters use publisher origin.
"""
from datetime import datetime, timedelta
import re

from sqlalchemy import text

from src.api.public_urls import safe_public_url

MODEL = "typesafe/jev-1.13"
VERSION = "news-triage-v2"
CAMPAIGN = "jev-agenda-2026-09-30"
TOPICS = {"sanctions": "Санкции", "travel": "Поездки", "business": "Бизнес",
          "education": "Образование", "culture": "Культура", "security": "Безопасность",
          "diplomacy": "Дипломатия", "other": "Другие темы"}
EVENTS = {"statement", "proposal", "decision", "incident", "analysis", "other"}
ACTORS = {"government", "business", "media", "ngo", "other"}
RELATIONS = {"direct", "indirect", "uncertain"}
MAX_LEADS = 24


def resolved_primary(primary, countries) -> bool:
    """A secondary mention cannot establish scope when the main country is unclear."""
    return (isinstance(primary, str) and re.fullmatch(r"[A-Z]{2}", primary) is not None
            and primary != "RU" and primary in countries)


# Apply before LIMIT and aggregation, including old cached classifications.
SCOPED_COUNTRIES = """(CASE WHEN
    nt.classification->>'country_primary' ~ '^[A-Z]{2}$'
    AND nt.classification->>'country_primary' <> 'RU'
    AND jsonb_typeof(nt.classification->'countries')='array'
    AND (nt.classification->'countries') ? (nt.classification->>'country_primary')
    THEN nt.classification->'countries' ELSE '[]'::jsonb END)"""


def processing_status(collected: int, classified: int, remaining: float | None) -> str:
    if collected and classified >= collected:
        return "up_to_date"
    if remaining is not None and remaining < .01:
        return "budget_exhausted"
    return "partial" if classified else "not_started"


def project_leads(rows, *, country: str | None, now: datetime) -> dict:
    day, week = [], []
    for row in rows:
        published, collected = row.get("published_at"), row.get("collected_at")
        if (not isinstance(published, datetime) or not isinstance(collected, datetime)
                or published.tzinfo is None or collected.tzinfo is None
                or not now-timedelta(days=7) <= published <= now or collected > now
                or row.get("source_title") != row.get("title")
                or row.get("source_excerpt") != row.get("body_excerpt")):
            continue
        tags = row.get("classification")
        if not isinstance(tags, dict):
            continue
        countries = tags.get("countries")
        if not isinstance(countries, list) or not all(isinstance(c, str) for c in countries):
            continue
        if not resolved_primary(tags.get("country_primary"), countries):
            countries = []
        if ((country not in countries if country else bool(countries))
                or tags.get("russia_relation") not in RELATIONS
                or tags.get("topic") not in TOPICS or tags.get("event_type") not in EVENTS
                or tags.get("actor_type") not in ACTORS):
            continue
        translated = row.get("title") if row.get("language") == "ru" else row.get("title_ru")
        if (not isinstance(translated, str) or not re.search(r"[А-Яа-яЁё]", translated)
                or len(translated) > 500 or re.search(r"[<>`]|https?://", translated)):
            translated = None
        lead = {
            "article_id": int(row["article_id"]), "title_ru": translated,
            "title_original": row["title"], "url": safe_public_url(row.get("url")),
            "publisher_name": row.get("publisher_name") or "",
            "publisher_country_code": (row.get("publisher_country_code") or "").strip() or None,
            "published_at": published.isoformat(), "collected_at": collected.isoformat(),
            "topic": tags["topic"], "event_type": tags["event_type"],
            "actor_type": tags["actor_type"], "russia_relation": tags["russia_relation"],
            "status": "needs_review", "countries": countries,
        }
        if len(week) < MAX_LEADS:
            week.append(lead)
        if published >= now-timedelta(hours=24) and len(day) < MAX_LEADS:
            day.append(lead)
    return {"day": day, "week": week}


CURRENT = """
 nt.model=:triage_model AND nt.version=:triage_version
 AND nt.source_title=ar.title
 AND nt.source_excerpt=LEFT(COALESCE(NULLIF(ar.body,''),ar.summary,''),2000)
"""
RECENT = """
 ar.published_at BETWEEN :week_start AND :as_of AND ar.collected_at<=:as_of
 AND ar.is_duplicate IS FALSE
 AND ar.geo_status IN ('source_verified','publisher_verified','publisher_reassigned')
"""
LEAD = """
 nt.classification->>'russia_relation' IN ('direct','indirect','uncertain')
 AND jsonb_typeof(nt.classification->'countries')='array'
 AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(CASE
     WHEN jsonb_typeof(nt.classification->'countries')='array'
     THEN nt.classification->'countries' ELSE '[]'::jsonb END) AS country_value
     WHERE jsonb_typeof(country_value)<>'string')
"""


def load_discovery(session, *, country: str, now: datetime, countries: list[dict],
                   collected: int) -> dict:
    """Use the caller's repeatable-read snapshot; never perform external I/O."""
    params = {"country": country, "week_start": now-timedelta(days=7),
              "day_start": now-timedelta(hours=24), "as_of": now,
              "triage_model": MODEL, "triage_version": VERSION}
    # Small cache first for all-country evidence; the local coverage query uses
    # the existing selective article geography index.
    projection_sql = f"""
        SELECT ar.id AS article_id,ar.title,ar.language,
          LEFT(COALESCE(NULLIF(ar.body,''),ar.summary,''),2000) AS body_excerpt,
          ar.published_at,ar.collected_at,nt.source_title,nt.source_excerpt,nt.classification,
          COALESCE(NULLIF(ar.resolved_url,''),ar.url) AS url,
          src.name AS publisher_name,TRIM(src.country_code) AS publisher_country_code,
          tr.title_ru
        FROM article_news_triage nt JOIN articles ar ON ar.id=nt.article_id
        JOIN article_country_facts src ON src.article_id=ar.id
        LEFT JOIN article_title_translations tr ON tr.article_id=ar.id AND tr.source_title=ar.title
        WHERE {CURRENT} AND {RECENT} AND {LEAD}
    """
    rows = session.execute(text(projection_sql + f"""
        AND {SCOPED_COUNTRIES} ? :country
        ORDER BY ar.published_at DESC,ar.id DESC LIMIT 24
    """), params).mappings().all()
    unassigned_rows = session.execute(text(projection_sql + f"""
        AND {SCOPED_COUNTRIES}='[]'::jsonb
        ORDER BY ar.published_at DESC,ar.id DESC LIMIT 8
    """), params).mappings().all()
    local = session.execute(text(f"""
        SELECT COUNT(*) AS classified,MAX(nt.classified_at) AS latest
        FROM articles ar JOIN article_country_facts src ON src.article_id=ar.id
        JOIN article_news_triage nt ON nt.article_id=ar.id
        WHERE ar.geo_country_code=:country AND TRIM(src.country_code)=:country
          AND {CURRENT} AND {RECENT}
    """), params).mappings().one()
    involved = session.execute(text(f"""
        SELECT COUNT(*) FROM article_news_triage nt JOIN articles ar ON ar.id=nt.article_id
        JOIN article_country_facts src ON src.article_id=ar.id
        WHERE {CURRENT} AND {RECENT} AND {LEAD}
          AND {SCOPED_COUNTRIES} ? :country
    """), params).scalar_one()
    attention_rows = session.execute(text(f"""
        SELECT involved.code,COUNT(DISTINCT ar.id) AS count_7d,
          COUNT(DISTINCT ar.id) FILTER (WHERE ar.published_at>=:day_start) AS count_24h,
          MAX(ar.published_at) AS latest_at,
          (ARRAY_AGG(nt.classification->>'topic' ORDER BY ar.published_at DESC,ar.id DESC))[1] AS topic
        FROM article_news_triage nt JOIN articles ar ON ar.id=nt.article_id
        JOIN article_country_facts src ON src.article_id=ar.id
        CROSS JOIN LATERAL jsonb_array_elements_text({SCOPED_COUNTRIES}) AS involved(code)
        WHERE {CURRENT} AND {RECENT} AND {LEAD}
        GROUP BY involved.code
        HAVING COUNT(DISTINCT ar.id) FILTER (WHERE ar.published_at>=:day_start)>0
        ORDER BY count_24h DESC,latest_at DESC
    """), params).mappings().all()
    names = {c["code"]: c["name"] for c in countries}
    attention = [{"code": r["code"], "name": names[r["code"]],
                  "count_24h": int(r["count_24h"]), "count_7d": int(r["count_7d"]),
                  "latest_at": r["latest_at"].isoformat(), "status": "needs_review",
                  "reason": f"{TOPICS.get(r['topic'], 'Другие темы')}: появились сообщения для проверки"}
                 for r in attention_rows if r["code"] in names and r["code"] != "RU"]
    account = session.execute(text("""SELECT limit_usd,charged_usd,halted
        FROM agenda_budget WHERE campaign=:campaign"""), {"campaign": CAMPAIGN}).mappings().one_or_none()
    remaining = (0 if account["halted"] else float(account["limit_usd"]-account["charged_usd"])) if account else None
    classified = int(local["classified"])
    unassigned = project_leads(unassigned_rows, country=None, now=now)
    discovery = {**project_leads(rows, country=country, now=now),
                 "unassigned_day": unassigned["day"], "unassigned_week": unassigned["week"]}
    return {"discovery": discovery, "attention": attention,
            "coverage": {"classified_from_country_7d": classified,
                         "pending_from_country_7d": max(0, collected-classified),
                         "discovered_to_country_7d": int(involved),
                         "last_classified_at": local["latest"].isoformat() if local["latest"] else None,
                         "triage_status": processing_status(collected, classified, remaining)}}
