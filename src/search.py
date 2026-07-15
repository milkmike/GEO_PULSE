"""Deterministic, explainable article-search domain helpers."""

from __future__ import annotations

import base64
import binascii
import json
import re
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError


LEXICAL_WEIGHT = 0.35
ENTITY_WEIGHT = 0.25
TOPIC_WEIGHT = 0.15
FRESHNESS_WEIGHT = 0.10
TRUST_WEIGHT = 0.10
STORY_WEIGHT = 0.05

_STRUCTURED_FILTERS = {
    "country",
    "topic",
    "entity_id",
    "from",
    "to",
    "tier",
    "language",
}
_NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)


ARTICLE_SEARCH_SQL = """
WITH search_query AS (
    SELECT CASE
        WHEN :q <> '' THEN websearch_to_tsquery('simple', :q)
        ELSE NULL
    END AS tsq
),
base_articles AS (
    SELECT a.id, a.search_vector, a.title_normalized, a.published_at,
           GREATEST(0.0, LEAST(1.0,
               COALESCE(s.weight, 0.5)::DOUBLE PRECISION
           )) AS trust_score,
           COALESCE(an.topics, ARRAY[]::TEXT[]) AS topics
    FROM articles a
    JOIN sources s ON s.id = a.source_id
    LEFT JOIN analysis an ON an.article_id = a.id
    WHERE a.is_duplicate = FALSE
      AND (:country IS NULL OR s.country_code = :country)
      AND (:topic IS NULL OR an.topics @> ARRAY[CAST(:topic AS TEXT)])
      AND (:entity_id IS NULL OR EXISTS (
          SELECT 1
          FROM article_entity_mentions aem_filter
          JOIN canonical_entities ce_filter ON ce_filter.id = aem_filter.entity_id
          WHERE aem_filter.article_id = a.id
            AND ce_filter.id = CAST(:entity_id AS UUID)
      ))
      AND (:date_from IS NULL OR a.published_at >= CAST(:date_from AS DATE))
      AND (:date_to IS NULL OR a.published_at < CAST(:date_to AS DATE) + INTERVAL '1 day')
      AND (:tier IS NULL OR s.tier = :tier)
      AND (:language IS NULL OR a.language = :language)
),
full_text_candidates AS (
    SELECT b.id, b.published_at,
           ts_rank_cd(b.search_vector, sq.tsq, 32) AS lexical_score,
           'full_text'::TEXT AS match_kind
    FROM base_articles b
    CROSS JOIN search_query sq
    WHERE :q <> '' AND b.search_vector @@ sq.tsq
),
full_text_count AS (
    SELECT COUNT(*) AS candidate_count FROM full_text_candidates
),
trigram_candidates AS (
    SELECT b.id, b.published_at,
           similarity(COALESCE(b.title_normalized, ''), :q) AS lexical_score,
           'trigram'::TEXT AS match_kind
    FROM base_articles b
    WHERE :q <> ''
      AND (SELECT candidate_count FROM full_text_count) < 10
      AND similarity(COALESCE(b.title_normalized, ''), :q) > 0.1
      AND NOT EXISTS (
          SELECT 1 FROM full_text_candidates ft WHERE ft.id = b.id
      )
    ORDER BY lexical_score DESC, b.id DESC
    LIMIT :candidate_limit
),
entity_candidates AS (
    SELECT DISTINCT b.id, b.published_at, 0.0::REAL AS lexical_score,
           'entity'::TEXT AS match_kind
    FROM base_articles b
    JOIN article_entity_mentions aem ON aem.article_id = b.id
    JOIN canonical_entities ce ON ce.id = aem.entity_id
    WHERE :q <> ''
      AND (
          ce.normalized_name = :q
          OR EXISTS (
              SELECT 1 FROM entity_aliases ea
              WHERE ea.entity_id = ce.id
                AND ea.ambiguous = FALSE
                AND ea.normalized_alias = :q
          )
      )
),
topic_candidates AS (
    SELECT b.id, b.published_at, 0.0::REAL AS lexical_score,
           'topic'::TEXT AS match_kind
    FROM base_articles b
    WHERE :q <> '' AND b.topics @> ARRAY[CAST(:q AS TEXT)]
),
story_candidates AS (
    SELECT DISTINCT b.id, b.published_at, 0.0::REAL AS lexical_score,
           'story'::TEXT AS match_kind
    FROM base_articles b
    JOIN story_articles sa_match ON sa_match.article_id = b.id
    JOIN stories st_match ON st_match.id = sa_match.story_id
    CROSS JOIN search_query sq
    WHERE :q <> ''
      AND to_tsvector(
          'simple', COALESCE(st_match.title_ru, '') || ' ' ||
                    COALESCE(st_match.summary, '')
      ) @@ sq.tsq
),
structured_candidates AS (
    SELECT b.id, b.published_at, 0.0::REAL AS lexical_score,
           'structured'::TEXT AS match_kind
    FROM base_articles b
    WHERE :q = ''
),
candidate_sources AS (
    SELECT id, published_at, lexical_score, match_kind FROM full_text_candidates
    UNION ALL
    SELECT id, published_at, lexical_score, match_kind FROM trigram_candidates
    UNION ALL
    SELECT id, published_at, lexical_score, match_kind FROM entity_candidates
    UNION ALL
    SELECT id, published_at, lexical_score, match_kind FROM topic_candidates
    UNION ALL
    SELECT id, published_at, lexical_score, match_kind FROM story_candidates
    UNION ALL
    SELECT id, published_at, lexical_score, match_kind FROM structured_candidates
),
candidate_ids AS (
    SELECT id, published_at, MAX(lexical_score) AS lexical_score,
           CASE
               WHEN BOOL_OR(match_kind = 'full_text') THEN 'full_text'
               WHEN BOOL_OR(match_kind = 'trigram') THEN 'trigram'
               WHEN BOOL_OR(match_kind = 'entity') THEN 'entity'
               WHEN BOOL_OR(match_kind = 'topic') THEN 'topic'
               WHEN BOOL_OR(match_kind = 'story') THEN 'story'
               ELSE 'structured'
           END AS match_kind
    FROM candidate_sources
    GROUP BY id, published_at
),
raw_candidate_features AS (
    SELECT candidate_ids.id, candidate_ids.published_at,
           candidate_ids.lexical_score, candidate_ids.match_kind,
           CASE WHEN EXISTS (
               SELECT 1
               FROM article_entity_mentions aem_rank
               JOIN canonical_entities ce_rank ON ce_rank.id = aem_rank.entity_id
               WHERE aem_rank.article_id = candidate_ids.id
                 AND (
                     (:entity_id IS NOT NULL AND
                      ce_rank.id = CAST(:entity_id AS UUID))
                     OR (:q <> '' AND (
                         ce_rank.normalized_name = :q
                         OR EXISTS (
                             SELECT 1 FROM entity_aliases ea_rank
                             WHERE ea_rank.entity_id = ce_rank.id
                               AND ea_rank.ambiguous = FALSE
                               AND ea_rank.normalized_alias = :q
                         )
                     ))
                 )
           ) THEN 1.0 ELSE 0.0 END AS entity_score,
           CASE WHEN
               (:topic IS NOT NULL AND
                b.topics @> ARRAY[CAST(:topic AS TEXT)])
               OR (:q <> '' AND b.topics @> ARRAY[CAST(:q AS TEXT)])
           THEN 1.0 ELSE 0.0 END AS topic_score,
           GREATEST(0.0, LEAST(
               1.0,
               1.0 - EXTRACT(EPOCH FROM (
                   CAST(:ranking_at AS TIMESTAMPTZ) - b.published_at
               )) / 7776000.0
           )) AS freshness_score,
           b.trust_score,
           GREATEST(0.0, LEAST(1.0,
               COALESCE(story_rank.story_score, 0.0)
           )) AS story_score
    FROM candidate_ids
    JOIN base_articles b ON b.id = candidate_ids.id
    LEFT JOIN LATERAL (
        SELECT MAX(sa_rank.membership_confidence)::DOUBLE PRECISION AS story_score
        FROM story_articles sa_rank
        WHERE sa_rank.article_id = candidate_ids.id
    ) story_rank ON TRUE
),
candidate_features AS (
    SELECT raw_candidate_features.*,
           lexical_score * 0.35 + entity_score * 0.25 +
           topic_score * 0.15 + freshness_score * 0.10 +
           trust_score * 0.10 + story_score * 0.05
               AS candidate_hybrid_score
    FROM raw_candidate_features
),
limited_candidates AS (
    SELECT id, published_at, lexical_score, match_kind,
           candidate_hybrid_score
    FROM candidate_features
    ORDER BY CASE WHEN :sort = 'newest' THEN published_at END DESC,
             CASE WHEN :sort = 'relevance'
                  THEN candidate_hybrid_score END DESC,
             published_at DESC, id DESC
    LIMIT :candidate_limit
)
SELECT a.id, a.title, a.summary, a.url, a.published_at, a.language,
       s.name AS source_name, s.country_code, s.tier,
       COALESCE(s.weight, 0.5) AS source_weight,
       COALESCE(an.topics, ARRAY[]::TEXT[]) AS topics,
       an.sentiment, an.action_level,
       COALESCE(entity_data.matched_entities, '[]'::JSONB) AS matched_entities,
       COALESCE(entity_data.exact_entity_match, FALSE) AS exact_entity_match,
       story_data.story_id, story_data.story_slug, story_data.story_title,
       story_data.story_confidence,
       candidates.lexical_score,
       CASE
           WHEN :q = '' THEN NULL
           WHEN candidates.match_kind = 'trigram' THEN 'title'
           WHEN candidates.match_kind IN ('entity', 'topic', 'story') THEN NULL
           WHEN to_tsvector('simple', COALESCE(a.title, '')) @@ sq.tsq THEN 'title'
           WHEN to_tsvector('simple', COALESCE(a.summary, '')) @@ sq.tsq THEN 'summary'
           ELSE 'body'
       END AS lexical_field,
       CASE
           WHEN candidates.match_kind = 'full_text' THEN ts_headline(
               'simple', COALESCE(a.title, a.summary, a.body, ''), sq.tsq,
               'StartSel=«, StopSel=», MaxWords=35, MinWords=12'
           )
           WHEN candidates.match_kind = 'trigram' THEN a.title
           WHEN :q = '' THEN LEFT(COALESCE(a.summary, a.body, a.title, ''), 320)
           ELSE NULL
       END AS match_snippet
FROM limited_candidates candidates
JOIN articles a ON a.id = candidates.id
JOIN sources s ON s.id = a.source_id
LEFT JOIN analysis an ON an.article_id = a.id
CROSS JOIN search_query sq
LEFT JOIN LATERAL (
    SELECT jsonb_agg(DISTINCT jsonb_build_object(
               'id', ce.id,
               'name', ce.canonical_name,
               'kind', ce.kind,
               'mention_text', aem.mention_text,
               'confidence', aem.confidence,
               'exact_match', (
                   (:entity_id IS NOT NULL AND ce.id = CAST(:entity_id AS UUID))
                   OR (:q <> '' AND (
                       ce.normalized_name = :q
                       OR EXISTS (
                           SELECT 1 FROM entity_aliases ea
                           WHERE ea.entity_id = ce.id
                             AND ea.ambiguous = FALSE
                             AND ea.normalized_alias = :q
                       )
                   ))
               )
           )) AS matched_entities,
           bool_or(
               (:entity_id IS NOT NULL AND ce.id = CAST(:entity_id AS UUID))
               OR (:q <> '' AND (
                   ce.normalized_name = :q
                   OR EXISTS (
                       SELECT 1 FROM entity_aliases ea
                       WHERE ea.entity_id = ce.id
                         AND ea.ambiguous = FALSE
                         AND ea.normalized_alias = :q
                   )
               ))
           ) AS exact_entity_match
    FROM article_entity_mentions aem
    JOIN canonical_entities ce ON ce.id = aem.entity_id
    WHERE aem.article_id = a.id
) entity_data ON TRUE
LEFT JOIN LATERAL (
    SELECT st.id AS story_id, st.slug AS story_slug, st.title_ru AS story_title,
           sa.membership_confidence AS story_confidence
    FROM story_articles sa
    JOIN stories st ON st.id = sa.story_id
    WHERE sa.article_id = a.id
    ORDER BY sa.membership_confidence DESC, st.last_seen DESC, st.id DESC
    LIMIT 1
) story_data ON TRUE
"""


class SearchTimeoutError(TimeoutError):
    """The indexed database search exceeded its server-side time budget."""


@dataclass(frozen=True)
class SearchQuery:
    """Validated native GEO_PULSE search request."""

    q: str = ""
    country: str | None = None
    topic: str | None = None
    entity_id: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    tier: str | None = None
    language: str | None = None
    sort: str = "relevance"
    cursor: str | None = None
    limit: int = 25


@dataclass(frozen=True)
class SearchScore:
    """Every v1 ranking component and the deterministic combined score."""

    lexical: float
    entity: float
    topic: float
    freshness: float
    trust: float
    story: float
    vector: float | None
    final: float


def normalize_query(value: str) -> str:
    """Normalize multilingual search text without language-specific stemming."""

    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    normalized = normalized.replace("ё", "е")
    return " ".join(_NON_WORD_RE.sub(" ", normalized).split())


def validate_search_query(query: str, filters: dict[str, object]) -> str:
    """Validate query length and require text or a meaningful structured filter."""

    normalized = normalize_query(query)
    has_filter = any(filters.get(name) not in (None, "", [], ()) for name in _STRUCTURED_FILTERS)
    if not normalized and not has_filter:
        raise ValueError("query or filter is required")
    if normalized and not 2 <= len(normalized) <= 200:
        raise ValueError("query must contain between 2 and 200 characters")
    return normalized


def _unit_interval(value: float) -> float:
    return min(1.0, max(0.0, float(value)))


def combine_scores(
    *,
    lexical: float,
    entity: float,
    topic: float,
    freshness: float,
    trust: float,
    story: float,
    vector: float | None = None,
) -> SearchScore:
    """Combine v1 component scores using the approved non-semantic weights."""

    components = {
        "lexical": _unit_interval(lexical),
        "entity": _unit_interval(entity),
        "topic": _unit_interval(topic),
        "freshness": _unit_interval(freshness),
        "trust": _unit_interval(trust),
        "story": _unit_interval(story),
    }
    final = (
        components["lexical"] * LEXICAL_WEIGHT
        + components["entity"] * ENTITY_WEIGHT
        + components["topic"] * TOPIC_WEIGHT
        + components["freshness"] * FRESHNESS_WEIGHT
        + components["trust"] * TRUST_WEIGHT
        + components["story"] * STORY_WEIGHT
    )
    return SearchScore(
        **components,
        vector=None if vector is None else _unit_interval(vector),
        final=round(_unit_interval(final), 6),
    )


def explain_match(
    score: SearchScore,
    *,
    matched_entity: str | None = None,
    matched_topic: str | None = None,
    lexical_field: str | None = None,
    story_title: str | None = None,
) -> str:
    """Describe the strongest observed v1 match without semantic claims."""

    observed: list[tuple[float, str]] = []
    if matched_entity and score.entity > 0:
        observed.append(
            (
                score.entity * ENTITY_WEIGHT,
                f"Точное упоминание сущности «{matched_entity}»",
            )
        )
    if matched_topic and score.topic > 0:
        observed.append(
            (score.topic * TOPIC_WEIGHT, f"Совпадение по теме «{matched_topic}»")
        )
    if lexical_field and score.lexical > 0:
        labels = {
            "title": "заголовке",
            "summary": "аннотации",
            "body": "тексте",
        }
        observed.append(
            (
                score.lexical * LEXICAL_WEIGHT,
                "Текстовое совпадение в "
                f"{labels.get(lexical_field, 'материале')}",
            )
        )
    if story_title and score.story > 0:
        observed.append(
            (score.story * STORY_WEIGHT, f"Связано с сюжетом «{story_title}»")
        )
    if not observed:
        return "Соответствует выбранным фильтрам"
    return max(observed, key=lambda item: item[0])[1]


def encode_cursor(
    relevance_score: float,
    published_at: datetime,
    article_id: int,
    ranking_at: datetime,
) -> str:
    """Serialize stable page ordering plus its ranking-time snapshot."""

    payload = {
        "article_id": int(article_id),
        "published_at": published_at.isoformat(),
        "ranking_at": ranking_at.isoformat(),
        "relevance_score": round(_unit_interval(relevance_score), 6),
    }
    raw = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("ascii")).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[float, datetime, int, datetime]:
    """Decode and validate an opaque search cursor."""

    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        if set(payload) != {
            "article_id",
            "published_at",
            "ranking_at",
            "relevance_score",
        }:
            raise ValueError
        relevance_score = float(payload["relevance_score"])
        published_at = datetime.fromisoformat(payload["published_at"])
        article_id = int(payload["article_id"])
        ranking_at = datetime.fromisoformat(payload["ranking_at"])
        if (
            not 0 <= relevance_score <= 1
            or published_at.tzinfo is None
            or ranking_at.tzinfo is None
            or article_id < 1
        ):
            raise ValueError
    except (binascii.Error, TypeError, ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid search cursor") from exc
    return relevance_score, published_at, article_id, ranking_at


def _row_value(row: object, name: str, default=None):
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _freshness_score(published_at: datetime, now: datetime) -> float:
    age = max(0.0, (now - published_at).total_seconds())
    return max(0.0, 1.0 - age / timedelta(days=90).total_seconds())


def _serialize_candidate(row: object, query: SearchQuery, now: datetime) -> tuple[dict, datetime]:
    article_id = int(_row_value(row, "id"))
    published_at = _aware_datetime(_row_value(row, "published_at"))
    entities = sorted(
        list(_row_value(row, "matched_entities", []) or []),
        key=lambda entity: (
            not bool(entity.get("exact_match")),
            normalize_query(entity.get("name") or ""),
            str(entity.get("id") or ""),
            entity.get("mention_text") or "",
        ),
    )
    exact_entity = next((entity for entity in entities if entity.get("exact_match")), None)
    topics = list(_row_value(row, "topics", []) or [])
    story_id = _row_value(row, "story_id")
    story_confidence = float(_row_value(row, "story_confidence", 0) or 0)
    lexical_field = _row_value(row, "lexical_field")
    source_weight = _row_value(row, "source_weight")
    matched_topic = (
        query.topic
        if query.topic and query.topic in topics
        else query.q if query.q and query.q in topics else None
    )

    score = combine_scores(
        lexical=float(_row_value(row, "lexical_score", 0) or 0),
        entity=1.0 if _row_value(row, "exact_entity_match", False) else 0.0,
        topic=1.0 if matched_topic else 0.0,
        freshness=_freshness_score(published_at, now),
        trust=0.5 if source_weight is None else float(source_weight),
        story=story_confidence if story_id is not None else 0.0,
    )
    matched_entity_name = exact_entity.get("name") if exact_entity else None
    why_included = explain_match(
        score,
        matched_entity=matched_entity_name,
        matched_topic=matched_topic,
        lexical_field=lexical_field,
        story_title=_row_value(row, "story_title"),
    )

    evidence: list[dict] = []
    snippet = _row_value(row, "match_snippet")
    if query.q and snippet:
        evidence.append({"type": "text_span", "article_id": article_id, "text": snippet})
    if exact_entity:
        evidence.append({
            "type": "entity_mention",
            "article_id": article_id,
            "entity_id": str(exact_entity.get("id")),
            "text": exact_entity.get("mention_text") or exact_entity.get("name"),
            "confidence": float(exact_entity.get("confidence") or 0),
        })
    if matched_topic:
        evidence.append({
            "type": "topic",
            "article_id": article_id,
            "topic": matched_topic,
        })
    if story_id is not None:
        evidence.append({
            "type": "story_membership",
            "article_id": article_id,
            "story_id": int(story_id),
            "confidence": story_confidence,
        })
    if not evidence:
        evidence.append({"type": "structured_filter", "article_id": article_id})

    match_confidences = [score.lexical]
    if exact_entity:
        match_confidences.append(float(exact_entity.get("confidence") or 0))
    if score.topic:
        match_confidences.append(score.topic)
    if story_id is not None:
        match_confidences.append(story_confidence)

    public_entities = [
        {
            "id": str(entity.get("id")),
            "name": entity.get("name"),
            "kind": entity.get("kind"),
            "mention_text": entity.get("mention_text"),
            "confidence": float(entity.get("confidence") or 0),
        }
        for entity in entities
    ]
    story = None
    if story_id is not None:
        story = {
            "id": int(story_id),
            "slug": _row_value(row, "story_slug"),
            "title": _row_value(row, "story_title"),
        }

    item = {
        "article_id": article_id,
        "title": _row_value(row, "title") or "",
        "summary": _row_value(row, "summary"),
        "url": _row_value(row, "url"),
        "published_at": published_at.isoformat(),
        "language": _row_value(row, "language"),
        "source": {
            "name": _row_value(row, "source_name"),
            "country": (_row_value(row, "country_code") or "").strip(),
            "tier": _row_value(row, "tier"),
        },
        "topics": topics,
        "matched_entities": public_entities,
        "sentiment": (
            float(_row_value(row, "sentiment"))
            if _row_value(row, "sentiment") is not None
            else None
        ),
        "action_level": _row_value(row, "action_level"),
        "story": story,
        "why_included": why_included,
        "relevance_score": score.final,
        "confidence": round(max(match_confidences), 3),
        "evidence": evidence,
        "scores": {
            "lexical": score.lexical,
            "entity": score.entity,
            "topic": score.topic,
            "freshness": score.freshness,
            "trust": score.trust,
            "story": score.story,
            "vector": score.vector,
        },
    }
    return item, published_at


def search_articles(
    query: SearchQuery,
    *,
    session_factory: Callable | None = None,
    now: datetime | None = None,
) -> dict:
    """Retrieve, rank, explain, and paginate locally indexed article rows."""

    if session_factory is None:
        from src.db import get_session

        session_factory = get_session
    cursor_values = decode_cursor(query.cursor) if query.cursor else None
    ranking_at = (
        _aware_datetime(cursor_values[3])
        if cursor_values
        else _aware_datetime(now or datetime.now(timezone.utc))
    )
    params = {
        "q": query.q,
        "country": query.country,
        "topic": query.topic,
        "entity_id": query.entity_id,
        "date_from": query.date_from,
        "date_to": query.date_to,
        "tier": query.tier,
        "language": query.language,
        "sort": query.sort,
        "ranking_at": ranking_at,
        "candidate_limit": 500,
    }
    try:
        with session_factory() as session:
            session.execute(text("SET LOCAL statement_timeout = '2s'"))
            rows = session.execute(text(ARTICLE_SEARCH_SQL), params).fetchall()
    except DBAPIError as exc:
        pgcode = getattr(exc.orig, "pgcode", None) or getattr(exc.orig, "sqlstate", None)
        if pgcode == "57014" or "statement timeout" in str(exc).casefold():
            raise SearchTimeoutError("article search timed out") from exc
        raise

    ranked = [_serialize_candidate(row, query, ranking_at) for row in rows]
    if query.sort == "newest":
        ranked.sort(
            key=lambda pair: (
                pair[1],
                pair[0]["article_id"],
                pair[0]["relevance_score"],
            ),
            reverse=True,
        )
    else:
        ranked.sort(
            key=lambda pair: (
                pair[0]["relevance_score"],
                pair[1],
                pair[0]["article_id"],
            ),
            reverse=True,
        )

    if cursor_values:
        cursor_score, cursor_published_at, cursor_id, _ = cursor_values
        cursor_published_at = _aware_datetime(cursor_published_at)
        if query.sort == "newest":
            ranked = [
                pair for pair in ranked
                if (pair[1], pair[0]["article_id"]) < (cursor_published_at, cursor_id)
            ]
        else:
            ranked = [
                pair for pair in ranked
                if (pair[0]["relevance_score"], pair[1], pair[0]["article_id"])
                < (cursor_score, cursor_published_at, cursor_id)
            ]

    has_more = len(ranked) > query.limit
    page_rows = ranked[:query.limit]
    next_cursor = None
    if has_more and page_rows:
        last_item, last_published_at = page_rows[-1]
        next_cursor = {
            "relevance_score": last_item["relevance_score"],
            "published_at": last_published_at.isoformat(),
            "article_id": last_item["article_id"],
            "ranking_at": ranking_at.isoformat(),
        }
    return {
        "items": [item for item, _ in page_rows],
        "candidate_count": len(rows),
        "next_cursor": next_cursor,
    }
