"""Bounded, read-only evidence projection for the analyst decision workspace."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Mapping

from sqlalchemy import text

from src.api.public_urls import safe_public_url
from src.collectors.publisher_attribution import normalize_publisher_domain, expected_site_domain
from src.db import get_session
from src.news_discovery import load_discovery


MAX_DETAIL = 1000
# Keep this read-only contract independent of the background HTTP client.
# The PostgreSQL regression test checks parity with decision_extraction.
CURRENT_ANNOTATION_MODEL = "deepseek/deepseek-v4-flash"
CURRENT_ANNOTATION_VERSION = "decision-annotation-v3-reviewed"
_KINDS = {
    "decision": ("Решения", "Каков статус решения и кого оно затрагивает?"),
    "conflict": ("Разногласия", "Какие утверждения сторон требуют проверки?"),
    "cooperation": ("Сотрудничество", "Какие действия подтверждены публикациями?"),
    "position": ("Позиции", "Кому принадлежит опубликованная позиция?"),
    "incident": ("Происшествия", "Что известно из первичных сообщений?"),
    "other": ("Другие сообщения", "Что ещё следует проверить?"),
}
_ACTOR_TYPES = {"government", "business", "media", "ngo", "other"}
_CHANGE_CATEGORIES = {"travel", "work", "education", "culture", "restrictions", "safety", "other"}


def _get(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _valid_text(value: Any, *, max_length: int = 1000) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= max_length


def _valid_optional_prose(value: Any) -> bool:
    if not isinstance(value, str) or len(value) > 1000:
        return False
    if value == "":
        return True
    return (_valid_text(value) and value == value.strip()
            and not re.search(r"[<>`]|https?://|www\.|javascript:", value, re.I)
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _quote(value: Any, source: str) -> bool:
    return _valid_text(value, max_length=1000) and value in source


def _evidence(row: Any, country_code: str, now: datetime) -> tuple[dict[str, Any], dict] | None:
    published = _get(row, "published_at")
    collected = _get(row, "collected_at")
    if not isinstance(published, datetime) or not isinstance(collected, datetime):
        return None
    if published.tzinfo is None or collected.tzinfo is None:
        return None
    if published > now or published < now - timedelta(days=7) or collected > now:
        return None
    title = _get(row, "title") or ""
    excerpt = _get(row, "body_excerpt") or ""
    if title != _get(row, "source_title") or excerpt != _get(row, "source_excerpt"):
        return None
    annotation = _get(row, "annotation")
    if not isinstance(annotation, dict) or annotation.get("relevant") is not True:
        return None
    source = f"{title}\n{excerpt}"
    if not _quote(annotation.get("russia_evidence_quote"), source):
        return None
    countries = annotation.get("countries")
    if not isinstance(countries, list):
        return None
    country_items = [c for c in countries if isinstance(c, dict) and c.get("code") == country_code
                     and _quote(c.get("evidence_quote"), source)]
    if not country_items:
        return None
    if (not _valid_text(annotation.get("headline_ru"))
            or not all(_valid_optional_prose(annotation.get(key))
                       for key in ("summary_ru", "russia_explanation_ru"))):
        return None
    kind = annotation.get("kind")
    if not isinstance(kind, str) or kind not in _KINDS:
        return None
    from src.source_segments import public_quotes
    try:
        if 'evidence_review' in annotation and annotation['evidence_review'] is None:
            return None
        quotes = public_quotes({'id':int(_get(row, 'article_id')), 'title':title, 'excerpt':excerpt},
                               annotation, country_code)
    except (ValueError, KeyError, TypeError):
        return None
    return ({
        "article_id": int(_get(row, "article_id")),
        "title_ru": annotation["headline_ru"],
        "title_original": title,
        "url": safe_public_url(_get(row, "url")),
        "publisher_name": _get(row, "publisher_name") or "",
        "publisher_country_code": (_get(row, "publisher_country_code") or "").strip() or None,
        "published_at": _iso(published),
        "collected_at": _iso(collected),
        "russia_explanation_ru": annotation["russia_explanation_ru"],
        "russia_evidence_quote": annotation["russia_evidence_quote"],
        "country_evidence_quote": country_items[0]["evidence_quote"],
        "summary_ru": annotation["summary_ru"],
        "kind": kind,
        **({'supporting_quotes':quotes} if quotes else {}),
    }, annotation)


def project_decision_workspace(*, country: dict, countries: list[dict], rows: list[Any],
                               now: datetime, attention: list[dict], coverage: dict,
                               truncated: bool) -> dict:
    """Project exact-source annotations; source geography never implies involvement."""
    code = country["code"]
    day: list[dict] = []
    week: list[dict] = []
    positions: list[dict] = []
    changes: list[dict] = []
    topics: list[dict] = []
    for row in rows[:MAX_DETAIL]:
        result = _evidence(row, code, now)
        if result is None:
            continue
        evidence, annotation = result
        if len(week) < 8:
            week.append(evidence)
        published = _get(row, "published_at")
        if published >= now - timedelta(hours=24) and len(day) < 8:
            day.append(evidence)
        if len(topics) < 6 and evidence["title_ru"] not in {t["title"] for t in topics}:
            topics.append({"id": f"article:{evidence['article_id']}",
                           "title": evidence["title_ru"],
                           "question": _KINDS[evidence["kind"]][1],
                           "evidence": [evidence]})
        source = f"{_get(row, 'title') or ''}\n{_get(row, 'body_excerpt') or ''}"
        for index, item in enumerate(annotation.get("positions") if isinstance(annotation.get("positions"), list) else []):
            if len(positions) >= 8:
                break
            if (isinstance(item, dict) and _valid_text(item.get("actor"), max_length=200)
                    and isinstance(item.get("actor_type"), str) and item.get("actor_type") in _ACTOR_TYPES
                    and _valid_text(item.get("position_ru"), max_length=1000)
                    and _quote(item.get("evidence_quote"), source)):
                positions.append({"id": f"{evidence['article_id']}:p:{index}",
                                  "actor": item["actor"], "actor_type": item["actor_type"],
                                  "position_ru": item["position_ru"],
                                  "evidence_quote": item["evidence_quote"], "evidence": evidence})
        for index, item in enumerate(annotation.get("changes") if isinstance(annotation.get("changes"), list) else []):
            if len(changes) >= 8:
                break
            if (isinstance(item, dict) and isinstance(item.get("category"), str)
                    and item.get("category") in _CHANGE_CATEGORIES
                    and _valid_text(item.get("change_ru"), max_length=1000)
                    and _quote(item.get("evidence_quote"), source)):
                changes.append({"id": f"{evidence['article_id']}:c:{index}",
                                "category": item["category"], "change_ru": item["change_ru"],
                                "evidence_quote": item["evidence_quote"], "evidence": evidence})
    limitations = list(coverage.get("limitations") or [])
    if truncated and "Показана только часть аннотированных публикаций за 7 суток." not in limitations:
        limitations.append("Показана только часть аннотированных публикаций за 7 суток.")
    return {
        "as_of": _iso(now), "country": country, "countries": countries,
        "attention": attention[:20], "brief": {"day": day, "week": week},
        "positions": positions, "changes": changes, "topics": topics,
        "coverage": {
            "collected_from_country_7d": int(coverage.get("collected_from_country_7d") or 0),
            "reviewed_from_country_7d": int(coverage.get("reviewed_from_country_7d") or 0),
            "relevant_to_country_7d": int(coverage.get("relevant_to_country_7d") or 0),
            "publisher_families": int(coverage.get("publisher_families") or 0),
            "local_publisher_families": int(coverage.get("local_publisher_families") or 0),
            "last_collected_at": _iso(coverage.get("last_collected_at")),
            "last_published_at": _iso(coverage.get("last_published_at")),
            "last_analyzed_at": _iso(coverage.get("last_analyzed_at")),
            "independent_confirmation": "not_assessed", "truncated": truncated,
            "limitations": limitations,
        },
    }


_VALID_ANNOTATION = """
  ada.model = :annotation_model AND ada.version = :annotation_version
  AND ada.source_title = ar.title
  AND ada.source_excerpt = LEFT(COALESCE(NULLIF(ar.body,''),ar.summary,''),4000)
"""
_RUSSIA_QUOTE = """
  NULLIF(ada.annotation->>'russia_evidence_quote','') IS NOT NULL
  AND STRPOS(ar.title || E'\\n' || LEFT(COALESCE(NULLIF(ar.body,''),ar.summary,''),4000),
             ada.annotation->>'russia_evidence_quote') > 0
"""
_COUNTRY_INVOLVEMENT = """
  EXISTS (SELECT 1 FROM jsonb_array_elements(
    CASE WHEN jsonb_typeof(ada.annotation->'countries')='array'
      THEN ada.annotation->'countries' ELSE '[]'::jsonb END) AS involved(item)
    WHERE involved.item->>'code' = :country
      AND NULLIF(involved.item->>'evidence_quote','') IS NOT NULL
      AND STRPOS(ar.title || E'\\n' || LEFT(COALESCE(NULLIF(ar.body,''),ar.summary,''),4000),
                 involved.item->>'evidence_quote') > 0)
"""


def _family(value: str | None) -> str | None:
    return expected_site_domain(value) or normalize_publisher_domain(value)


def load_decision_workspace(country_code: str | None = None, *, now: datetime | None = None) -> dict:
    """Read one bounded snapshot. Annotation countries, not source country, drive facts."""
    now = now or datetime.now(timezone.utc)
    with get_session() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        session.execute(text("SET LOCAL statement_timeout='15s'"))
        country_rows = session.execute(text("""
            SELECT TRIM(code) AS code, name_ru AS name, region FROM countries
            WHERE active IS TRUE AND TRIM(code) <> 'RU' ORDER BY name_ru
        """)).mappings().all()
        countries = [dict(row) for row in country_rows]
        valid_codes = {c["code"] for c in countries}
        attention_rows = session.execute(text(f"""
            SELECT involved.item->>'code' AS code,
                   COUNT(DISTINCT ar.id) FILTER (WHERE ar.published_at >= :day_start) AS count_24h,
                   COUNT(DISTINCT ar.id) AS count_7d, MAX(ar.published_at) AS latest_at,
                   (ARRAY_AGG(ada.annotation->>'headline_ru' ORDER BY ar.published_at DESC, ar.id DESC))[1] AS latest_headline,
                   (ARRAY_AGG(ada.annotation->>'kind' ORDER BY ar.published_at DESC, ar.id DESC))[1] AS latest_kind
            FROM articles ar
            JOIN article_decision_annotations ada ON ada.article_id=ar.id
            JOIN article_country_facts src ON src.article_id=ar.id
            CROSS JOIN LATERAL jsonb_array_elements(
              CASE WHEN jsonb_typeof(ada.annotation->'countries')='array'
                THEN ada.annotation->'countries' ELSE '[]'::jsonb END) involved(item)
            WHERE ar.published_at BETWEEN :week_start AND :as_of
              AND ar.collected_at <= :as_of
              AND ar.is_duplicate IS FALSE AND ada.annotation->>'relevant'='true'
              AND {_VALID_ANNOTATION} AND {_RUSSIA_QUOTE}
              AND NULLIF(involved.item->>'evidence_quote','') IS NOT NULL
              AND STRPOS(ar.title || E'\\n' || LEFT(COALESCE(NULLIF(ar.body,''),ar.summary,''),4000),
                         involved.item->>'evidence_quote') > 0
            GROUP BY involved.item->>'code'
            HAVING COUNT(DISTINCT ar.id) FILTER (WHERE ar.published_at >= :day_start) > 0
            ORDER BY count_24h DESC, count_7d DESC, latest_at DESC
        """), {"day_start": now-timedelta(hours=24), "week_start": now-timedelta(days=7),
                "as_of": now, "annotation_model": CURRENT_ANNOTATION_MODEL,
                "annotation_version": CURRENT_ANNOTATION_VERSION}).mappings().all()
        attention = [
            {"code": r["code"], "name": next(c["name"] for c in countries if c["code"] == r["code"]),
             "count_24h": int(r["count_24h"]), "count_7d": int(r["count_7d"]),
             "reason": (f"{_KINDS.get(r['latest_kind'], _KINDS['other'])[0]}: {r['latest_headline']}"
                        if _valid_text(r["latest_headline"]) else "Новая публикация о связи страны с Россией"),
             "latest_at": _iso(r["latest_at"])}
            for r in attention_rows if r["code"] in valid_codes and r["code"] != "RU"
        ]
        selected = (country_code or (attention[0]["code"] if attention else ("RS" if "RS" in valid_codes else (countries[0]["code"] if countries else None))))
        if selected not in valid_codes:
            raise ValueError("Unknown country code")
        country = next(c for c in countries if c["code"] == selected)
        params = {"country": selected, "week_start": now-timedelta(days=7), "as_of": now,
                  "annotation_model": CURRENT_ANNOTATION_MODEL,
                  "annotation_version": CURRENT_ANNOTATION_VERSION}
        detail_rows = session.execute(text(f"""
            SELECT ar.id AS article_id, ar.title,
                   LEFT(COALESCE(NULLIF(ar.body,''),ar.summary,''),4000) AS body_excerpt,
                   ar.published_at, ar.collected_at,
                   COALESCE(NULLIF(ar.resolved_url,''),ar.url) AS url,
                   src.name AS publisher_name, TRIM(src.country_code) AS publisher_country_code,
                   src.url AS publisher_url, ada.source_title, ada.source_excerpt,
                   ada.annotation, ada.analyzed_at
            FROM articles ar JOIN article_decision_annotations ada ON ada.article_id=ar.id
            JOIN article_country_facts src ON src.article_id=ar.id
            WHERE ar.published_at BETWEEN :week_start AND :as_of
              AND ar.collected_at <= :as_of
              AND ar.is_duplicate IS FALSE AND ada.annotation->>'relevant'='true'
              AND {_VALID_ANNOTATION} AND {_RUSSIA_QUOTE} AND {_COUNTRY_INVOLVEMENT}
            ORDER BY ar.published_at DESC, ar.id DESC LIMIT 1001
        """), params).mappings().all()
        # Use the selective article geography index for local collection, then
        # independently count explicitly annotated country involvement. An OR
        # between the two defeats that index and confuses provenance with subject.
        local = session.execute(text(f"""
            SELECT COUNT(*) AS collected_from_country_7d,
              COUNT(*) FILTER (WHERE ada.article_id IS NOT NULL
                AND {_VALID_ANNOTATION}) AS reviewed_from_country_7d,
              MAX(ar.collected_at) AS last_collected_at,
              MAX(ar.published_at) AS last_published_at,
              COALESCE(ARRAY_AGG(DISTINCT src.url) FILTER (WHERE src.url IS NOT NULL),
                '{{}}'::text[]) AS publisher_urls
            FROM articles ar
            JOIN article_country_facts src ON src.article_id=ar.id
            LEFT JOIN article_decision_annotations ada ON ada.article_id=ar.id
            WHERE ar.geo_country_code=:country
              AND ar.geo_status IN ('source_verified','publisher_verified','publisher_reassigned')
              AND TRIM(src.country_code)=:country
              AND ar.published_at BETWEEN :week_start AND :as_of
              AND ar.collected_at<=:as_of AND ar.is_duplicate IS FALSE
        """), params).mappings().one()
        relevant = session.execute(text(f"""
            SELECT COUNT(*) AS relevant_to_country_7d,
              MAX(ada.analyzed_at) AS last_analyzed_at,
              COALESCE(ARRAY_AGG(DISTINCT src.url) FILTER (WHERE src.url IS NOT NULL),
                '{{}}'::text[]) AS publisher_urls
            FROM article_decision_annotations ada
            JOIN articles ar ON ar.id=ada.article_id
            JOIN article_country_facts src ON src.article_id=ar.id
            WHERE ada.annotation->>'relevant'='true'
              AND {_VALID_ANNOTATION} AND {_RUSSIA_QUOTE} AND {_COUNTRY_INVOLVEMENT}
              AND ar.published_at BETWEEN :week_start AND :as_of
              AND ar.collected_at<=:as_of AND ar.is_duplicate IS FALSE
        """), params).mappings().one()
        discovery = load_discovery(session, country=selected, now=now, countries=countries,
                                   collected=int(local["collected_from_country_7d"]))
    local_families = {_family(url) for url in local["publisher_urls"]}
    all_families = {_family(url) for url in relevant["publisher_urls"]}
    coverage = {**dict(local), **dict(relevant)}
    coverage["local_publisher_families"] = len(local_families - {None})
    coverage["publisher_families"] = len(all_families - {None})
    coverage["limitations"] = [
        "Машинная разметка охватывает только обработанные публикации; отсутствие сообщения не означает отсутствие события.",
        "География издателя отражает покрытие, а не страну события.",
        "Независимое подтверждение конкретных утверждений не оценивалось.",
    ]
    result = project_decision_workspace(country=country, countries=countries,
                                      rows=detail_rows, now=now, attention=attention,
                                      coverage=coverage, truncated=len(detail_rows)>MAX_DETAIL)
    result["discovery"] = discovery["discovery"]
    result["coverage"].update(discovery["coverage"])
    reviewed_countries = {item["code"] for item in attention}
    merged_attention = attention + [item for item in discovery["attention"]
                                    if item["code"] not in reviewed_countries]
    result["attention"] = sorted(merged_attention,
                                  key=lambda item: item["latest_at"], reverse=True)[:20]
    return result
