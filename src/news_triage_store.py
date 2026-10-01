"""Source-snapshot selection and persistence for bounded news triage."""
from __future__ import annotations

import json
from collections import defaultdict, deque

from sqlalchemy import text

from src.db import get_session

_PUBLISHER_JOIN = """
 JOIN sources discovery ON discovery.id=ar.source_id
 JOIN sources publisher ON publisher.id=CASE
   WHEN COALESCE(discovery.config->>'feed_mode','publisher')='publisher_discovery'
   THEN ar.publisher_source_id ELSE COALESCE(ar.publisher_source_id,ar.source_id) END
"""
_ELIGIBLE = """
 ar.published_at>=now()-interval '7 days' AND ar.published_at<=now()
 AND ar.collected_at<=now() AND ar.is_duplicate=FALSE
 AND ar.geo_status IN ('source_verified','publisher_verified','publisher_reassigned')
 AND (COALESCE(discovery.config->>'feed_mode','publisher')<>'publisher_discovery'
      OR ar.publisher_source_id IS NOT NULL)
 AND LENGTH(TRIM(COALESCE(ar.title,'')))>0
 AND NOT EXISTS (
   SELECT 1 FROM article_news_triage cache
   WHERE cache.article_id=ar.id AND cache.source_title=ar.title
     AND cache.source_excerpt=LEFT(COALESCE(NULLIF(ar.body,''),ar.summary,''),2000)
     AND cache.model=:model AND cache.version=:version)
"""
# The partial (geo_country_code,published_at,id) index makes each country a
# bounded probe. The country is only a sampling stratum, never an event claim.
_CANDIDATES = text(f"""
 SELECT candidate.id FROM countries country CROSS JOIN LATERAL (
   SELECT ar.id FROM articles ar {_PUBLISHER_JOIN}
   WHERE ar.geo_country_code=country.code AND {_ELIGIBLE}
   ORDER BY ar.published_at DESC,ar.id DESC LIMIT 40
 ) candidate
""")
_BOOTSTRAP = text(f"""SELECT ar.id FROM articles ar {_PUBLISHER_JOIN}
 WHERE ar.id=ANY(CAST(:article_ids AS bigint[])) AND {_ELIGIBLE}""")
_DETAIL = text(f"""
 SELECT ar.id,ar.title,LEFT(COALESCE(NULLIF(ar.body,''),ar.summary,''),2000) AS excerpt,
 ar.published_at,TRIM(ar.geo_country_code) AS country_code,an.is_relevant
 FROM articles ar LEFT JOIN analysis an ON an.article_id=ar.id
 WHERE ar.id=ANY(CAST(:ids AS bigint[]))
""")


def fair_candidates(rows):
    result = []
    for relevant in (True, False, None):
        groups = defaultdict(deque)
        for row in rows:
            if row.get("is_relevant") is relevant:
                groups[row["country_code"]].append(row)
        while any(groups.values()):
            for queue in groups.values():
                if queue:
                    result.append(queue.popleft())
    return result


def load_candidates(*, model, version, article_ids=None):
    if article_ids is not None and (not isinstance(article_ids, list) or
        not 1 <= len(article_ids) <= 20 or any(type(i) is not int or i <= 0 for i in article_ids) or
        len(set(article_ids)) != len(article_ids)):
        raise ValueError("invalid article IDs")
    with get_session() as session:
        session.execute(text("SET LOCAL statement_timeout='15s'"))
        params = {"model": model, "version": version, "article_ids": article_ids}
        ids = session.execute(_BOOTSTRAP if article_ids is not None else _CANDIDATES, params).scalars().all()
        rows = [dict(row) for row in session.execute(_DETAIL, {"ids": ids}).mappings()] if ids else []
        rows.sort(key=lambda row: (row["published_at"], row["id"]), reverse=True)
        codes = {str(c).strip() for c in session.execute(text("SELECT code FROM countries WHERE active=TRUE")).scalars()}
    return fair_candidates(rows), codes


def save_if_current(records, *, model, version):
    saved = 0
    with get_session() as session:
        for record in records:
            article = record["article"]
            current = session.execute(text("""
              SELECT title,LEFT(COALESCE(NULLIF(body,''),summary,''),2000) AS excerpt
              FROM articles WHERE id=:id FOR UPDATE
            """), {"id": article["id"]}).mappings().one_or_none()
            if current is None or current["title"] != article["title"] or current["excerpt"] != article["excerpt"]:
                continue
            session.execute(text("""
              INSERT INTO article_news_triage
               (article_id,source_title,source_excerpt,classification,model,version)
              VALUES (:id,:title,:excerpt,CAST(:classification AS jsonb),:model,:version)
              ON CONFLICT(article_id) DO UPDATE SET
               source_title=EXCLUDED.source_title,source_excerpt=EXCLUDED.source_excerpt,
               classification=EXCLUDED.classification,model=EXCLUDED.model,
               version=EXCLUDED.version,classified_at=now()
            """), {"id": article["id"], "title": article["title"], "excerpt": article["excerpt"],
                   "classification": json.dumps(record["classification"], ensure_ascii=False),
                   "model": model, "version": version})
            saved += 1
    return saved
