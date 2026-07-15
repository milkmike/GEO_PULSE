"""Cross-country story scoring and lifecycle primitives.

The public API reads persisted stories only.  All clustering and optional copy
generation belongs to the background worker and is implemented below the pure
domain functions in this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from sqlalchemy import text


logger = logging.getLogger(__name__)


MERGE_THRESHOLD = 0.65
MAX_MERGE_GAP_DAYS = 14.0


@dataclass(frozen=True, slots=True)
class StoryArticle:
    article_id: int
    country_code: str
    title: str | None
    url: str | None
    published_at: datetime | None
    source_name: str
    sentiment: float | None = None
    action_level: int = 1
    event_key: str | None = None
    entity_ids: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class StoryCandidate:
    """Country-thread projection used by the global story clusterer."""

    thread_id: int
    country_code: str
    event_key: str
    title: str
    article_ids: tuple[int, ...]
    entities: frozenset[str] = field(default_factory=frozenset)
    topics: frozenset[str] = field(default_factory=frozenset)
    sources: frozenset[str] = field(default_factory=frozenset)
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    highest_action_level: int = 1
    articles: tuple[StoryArticle, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class StorySimilarity:
    """Normalized match components plus the evidence used by merge gates."""

    total: float
    components: dict[str, float]
    matched_features: frozenset[str]
    gap_days: float
    evidence: dict[str, object] = field(default_factory=dict)

    @property
    def score(self) -> float:
        """Compatibility alias for callers that describe ``total`` as score."""

        return self.total


@dataclass(frozen=True, slots=True)
class StoryCopy:
    title_ru: str
    title_en: str | None
    summary: str
    summary_model: str | None = None


@dataclass(frozen=True, slots=True)
class StoryBuildResult:
    candidates: int
    clusters: int
    stories_upserted: int
    article_memberships: int


def _normalized_text(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return " ".join(re.sub(r"[^\w]+", " ", normalized, flags=re.UNICODE).split())


def _trigrams(value: str | None) -> set[str]:
    normalized = _normalized_text(value)
    if not normalized:
        return set()
    padded = f"  {normalized}  "
    return {padded[index:index + 3] for index in range(len(padded) - 2)}


def trigram_similarity(left: str | None, right: str | None) -> float:
    """Return a deterministic Dice similarity over normalized trigrams."""

    left_trigrams = _trigrams(left)
    right_trigrams = _trigrams(right)
    if not left_trigrams or not right_trigrams:
        return 0.0
    return 2.0 * len(left_trigrams & right_trigrams) / (
        len(left_trigrams) + len(right_trigrams)
    )


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _gap_days(left: StoryCandidate, right: StoryCandidate) -> float:
    if not left.first_seen or not left.last_seen or not right.first_seen or not right.last_seen:
        return 0.0
    left_first, left_last = _as_utc(left.first_seen), _as_utc(left.last_seen)
    right_first, right_last = _as_utc(right.first_seen), _as_utc(right.last_seen)
    if left_last < right_first:
        return (right_first - left_last).total_seconds() / 86400.0
    if right_last < left_first:
        return (left_first - right_last).total_seconds() / 86400.0
    return 0.0


def score_story_match(left: StoryCandidate, right: StoryCandidate) -> StorySimilarity:
    """Score whether two country threads describe one concrete global story.

    The weighted total is deliberately insufficient on its own: ``should_merge``
    also requires two independent semantic features and the fourteen-day gate.
    """

    gap_days = _gap_days(left, right)
    components = {
        "event_key": trigram_similarity(left.event_key, right.event_key),
        "entities": _jaccard(left.entities, right.entities),
        "topics": _jaccard(left.topics, right.topics),
        "time": max(0.0, 1.0 - gap_days / MAX_MERGE_GAP_DAYS),
        "source_diversity": 1.0 if left.sources and right.sources and left.sources != right.sources else 0.0,
        "country_diversity": 1.0 if left.country_code != right.country_code else 0.0,
        "title": trigram_similarity(left.title, right.title),
    }
    weights = {
        "event_key": 0.35,
        "entities": 0.25,
        "topics": 0.15,
        "time": 0.10,
        "source_diversity": 0.05,
        "country_diversity": 0.05,
        "title": 0.05,
    }
    total = round(sum(components[name] * weight for name, weight in weights.items()), 6)

    matched_features: set[str] = set()
    if components["event_key"] >= 0.45:
        matched_features.add("event_key")
    if components["entities"] > 0:
        matched_features.add("entities")
    if components["topics"] > 0:
        matched_features.add("topics")
    if components["title"] >= 0.65:
        matched_features.add("title")

    evidence = {
        "countries": sorted({left.country_code, right.country_code}),
        "shared_entities": sorted(left.entities & right.entities),
        "shared_topics": sorted(left.topics & right.topics),
        "event_keys": [left.event_key, right.event_key],
    }
    return StorySimilarity(
        total=total,
        components=components,
        matched_features=frozenset(matched_features),
        gap_days=gap_days,
        evidence=evidence,
    )


def should_merge(
    similarity: StorySimilarity,
    *,
    explicit_reactivation: bool = False,
) -> bool:
    """Apply threshold, independent-evidence, and time-window merge gates."""

    if similarity.total < MERGE_THRESHOLD:
        return False
    if len(similarity.matched_features) < 2:
        return False
    if similarity.gap_days > MAX_MERGE_GAP_DAYS and not explicit_reactivation:
        return False
    return True


def transition_lifecycle(
    *,
    now: datetime,
    first_seen: datetime,
    last_seen: datetime,
    article_count: int,
    recent_article_count: int,
    previous_article_count: int,
    highest_action_level: int,
    previous_action_level: int,
) -> str:
    """Derive a story lifecycle from fixed inputs without wall-clock reads."""

    now_utc = _as_utc(now)
    first_utc = _as_utc(first_seen)
    last_utc = _as_utc(last_seen)
    silence_days = max(0.0, (now_utc - last_utc).total_seconds() / 86400.0)
    age_hours = max(0.0, (now_utc - first_utc).total_seconds() / 3600.0)

    if silence_days > MAX_MERGE_GAP_DAYS:
        return "resolved"
    if silence_days >= 3:
        return "cooling"
    accelerating = previous_article_count > 0 and recent_article_count >= max(
        3, previous_article_count * 1.5
    )
    action_escalation = highest_action_level > max(previous_action_level, 1)
    if accelerating or action_escalation:
        return "escalating"
    if article_count < 3 and age_hours <= 24:
        return "emerging"
    return "developing"


def cluster_story_candidates(
    candidates: Sequence[StoryCandidate],
    *,
    reactivation_pairs: frozenset[tuple[int, int]] = frozenset(),
) -> list[tuple[StoryCandidate, ...]]:
    """Cluster country threads globally, returning cross-country groups only."""

    ordered = sorted(candidates, key=lambda item: (item.country_code, item.thread_id))
    parent = list(range(len(ordered)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left_index: int, right_index: int) -> None:
        left_root, right_root = find(left_index), find(right_index)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_index, left in enumerate(ordered):
        for right_index in range(left_index + 1, len(ordered)):
            right = ordered[right_index]
            if left.country_code == right.country_code:
                continue
            pair = tuple(sorted((left.thread_id, right.thread_id)))
            if should_merge(
                score_story_match(left, right),
                explicit_reactivation=pair in reactivation_pairs,
            ):
                union(left_index, right_index)

    grouped: dict[int, list[StoryCandidate]] = {}
    for index, item in enumerate(ordered):
        grouped.setdefault(find(index), []).append(item)

    clusters = [
        tuple(items)
        for items in grouped.values()
        if len({item.country_code for item in items}) >= 2
    ]
    return sorted(
        clusters,
        key=lambda group: (
            min(item.first_seen or datetime.max.replace(tzinfo=timezone.utc) for item in group),
            tuple(item.thread_id for item in group),
        ),
    )


def compute_source_hash(candidates: Sequence[StoryCandidate]) -> str:
    """Hash every deterministic story input that can affect persisted copy."""

    payload = []
    for item in sorted(candidates, key=lambda candidate: (candidate.country_code, candidate.thread_id)):
        payload.append({
            "thread_id": item.thread_id,
            "country": item.country_code,
            "event_key": _normalized_text(item.event_key),
            "title": _normalized_text(item.title),
            "article_ids": sorted(item.article_ids),
            "entities": sorted(item.entities),
            "topics": sorted(item.topics),
            "sources": sorted(item.sources),
            "last_seen": item.last_seen.isoformat() if item.last_seen else None,
        })
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def deterministic_story_copy(candidates: Sequence[StoryCandidate]) -> StoryCopy:
    """Build stable user-facing copy even when no LLM is configured."""

    if not candidates:
        return StoryCopy("Международный сюжет", None, "Международный сюжет без доступного описания.")
    canonical = sorted(
        candidates,
        key=lambda item: (
            -item.highest_action_level,
            -len(item.article_ids),
            _normalized_text(item.title or item.event_key),
            item.country_code,
            item.thread_id,
        ),
    )[0]
    title = (canonical.title or canonical.event_key or "Международный сюжет").strip()[:500]
    countries = sorted({item.country_code for item in candidates})
    article_count = len({article_id for item in candidates for article_id in item.article_ids})
    summary = (
        f"Сюжет объединяет {article_count} публикаций из стран "
        f"{', '.join(countries)} по событию «{title.rstrip('.')}»."
    )
    return StoryCopy(title_ru=title, title_en=None, summary=summary)


def _summary_payload(candidates: Sequence[StoryCandidate], fallback: StoryCopy) -> dict[str, Any]:
    return {
        "fallback": {
            "title_ru": fallback.title_ru,
            "summary": fallback.summary,
        },
        "countries": sorted({item.country_code for item in candidates}),
        "event_keys": sorted({item.event_key for item in candidates if item.event_key}),
        "titles": sorted({item.title for item in candidates if item.title}),
        "topics": sorted({topic for item in candidates for topic in item.topics}),
        "article_count": len({article_id for item in candidates for article_id in item.article_ids}),
    }


def resolve_story_copy(
    candidates: Sequence[StoryCandidate],
    *,
    source_hash: str,
    previous_source_hash: str | None,
    previous_copy: StoryCopy | None,
    summarizer: Callable[[dict[str, Any]], dict[str, Any] | None] | None,
) -> StoryCopy:
    """Use generated copy only on source changes; otherwise preserve stored copy."""

    fallback = deterministic_story_copy(candidates)
    if source_hash == previous_source_hash and previous_copy is not None:
        return previous_copy
    if summarizer is None:
        return fallback
    try:
        generated = summarizer(_summary_payload(candidates, fallback))
    except Exception as exc:
        logger.warning("Story summarizer failed; deterministic copy retained: %s", exc)
        return fallback
    if not isinstance(generated, dict):
        return fallback
    title_ru = str(generated.get("title_ru") or generated.get("title") or fallback.title_ru).strip()
    summary = str(generated.get("summary") or fallback.summary).strip()
    title_en_value = generated.get("title_en")
    title_en = str(title_en_value).strip() if title_en_value else None
    if not title_ru or not summary:
        return fallback
    return StoryCopy(
        title_ru=title_ru[:500],
        title_en=title_en[:500] if title_en else None,
        summary=summary,
        summary_model=str(generated.get("model") or "background-llm")[:120],
    )


def _value(row: Any, name: str, default: Any = None) -> Any:
    if hasattr(row, name):
        return getattr(row, name)
    mapping = getattr(row, "_mapping", row if isinstance(row, dict) else {})
    return mapping.get(name, default)


def fetch_story_candidates(session: Any) -> list[StoryCandidate]:
    """Project existing country threads and analyzed articles into candidates."""

    rows = session.execute(text("""
        SELECT t.id AS thread_id, TRIM(t.country_code) AS country_code,
               t.thread_key, t.title AS thread_title, t.first_seen, t.last_seen,
               ar.id AS article_id, ar.title AS article_title, ar.url,
               ar.published_at, s.name AS source_name,
               an.sentiment, COALESCE(an.action_level, 1) AS action_level,
               COALESCE(NULLIF(an.event_key, ''), t.thread_key) AS article_event_key,
               COALESCE(an.topics, ARRAY[]::text[]) AS topics
        FROM threads t
        JOIN thread_articles ta ON ta.thread_id = t.id
        JOIN articles ar ON ar.id = ta.article_id
        JOIN sources s ON s.id = ar.source_id
        LEFT JOIN analysis an ON an.article_id = ar.id
        WHERE t.article_count > 0
        ORDER BY t.id, ar.published_at, ar.id
    """)).fetchall()
    if not rows:
        return []

    article_ids = sorted({_value(row, "article_id") for row in rows})
    mention_rows = session.execute(text("""
        SELECT article_id, entity_id::text AS entity_id
        FROM article_entity_mentions
        WHERE article_id = ANY(:article_ids)
    """), {"article_ids": article_ids}).fetchall()
    entities_by_article: dict[int, set[str]] = {}
    for mention in mention_rows:
        entities_by_article.setdefault(_value(mention, "article_id"), set()).add(
            _value(mention, "entity_id")
        )

    grouped: dict[int, list[Any]] = {}
    for row in rows:
        grouped.setdefault(_value(row, "thread_id"), []).append(row)

    candidates: list[StoryCandidate] = []
    for thread_id, thread_rows in grouped.items():
        articles = []
        topics: set[str] = set()
        entities: set[str] = set()
        sources: set[str] = set()
        event_keys: list[str] = []
        for row in thread_rows:
            article_id = _value(row, "article_id")
            article_entities = frozenset(entities_by_article.get(article_id, set()))
            row_topics = _value(row, "topics") or []
            topics.update(str(topic) for topic in row_topics if topic)
            entities.update(article_entities)
            sources.add(str(_value(row, "source_name", "unknown")))
            article_event_key = _value(row, "article_event_key")
            if article_event_key:
                event_keys.append(str(article_event_key))
            sentiment = _value(row, "sentiment")
            articles.append(StoryArticle(
                article_id=article_id,
                country_code=str(_value(row, "country_code")).strip(),
                title=_value(row, "article_title"),
                url=_value(row, "url"),
                published_at=_value(row, "published_at"),
                source_name=str(_value(row, "source_name", "unknown")),
                sentiment=float(sentiment) if sentiment is not None else None,
                action_level=int(_value(row, "action_level", 1) or 1),
                event_key=article_event_key,
                entity_ids=article_entities,
            ))
        first_row = thread_rows[0]
        dates = [article.published_at for article in articles if article.published_at]
        event_key = str(_value(first_row, "thread_key") or (event_keys[0] if event_keys else ""))
        candidates.append(StoryCandidate(
            thread_id=thread_id,
            country_code=str(_value(first_row, "country_code")).strip(),
            event_key=event_key,
            title=str(_value(first_row, "thread_title") or event_key),
            article_ids=tuple(sorted({article.article_id for article in articles})),
            entities=frozenset(entities),
            topics=frozenset(topics),
            sources=frozenset(sources),
            first_seen=_value(first_row, "first_seen") or (min(dates) if dates else None),
            last_seen=_value(first_row, "last_seen") or (max(dates) if dates else None),
            highest_action_level=max((article.action_level for article in articles), default=1),
            articles=tuple(articles),
        ))
    return candidates


def _story_slug(candidates: Sequence[StoryCandidate], first_seen: datetime) -> str:
    identity = "|".join(sorted(_normalized_text(item.event_key) for item in candidates))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"story-{first_seen.date().isoformat()}-{digest}"


def _membership_evidence(
    candidate: StoryCandidate,
    cluster: Sequence[StoryCandidate],
) -> tuple[float, dict[str, Any]]:
    matches = [
        score_story_match(candidate, other)
        for other in cluster
        if other.thread_id != candidate.thread_id and other.country_code != candidate.country_code
    ]
    if not matches:
        return 1.0, {"thread_id": candidate.thread_id, "country": candidate.country_code}
    best = max(matches, key=lambda match: match.total)
    return max(MERGE_THRESHOLD, best.total), {
        "thread_id": candidate.thread_id,
        "country": candidate.country_code,
        "components": best.components,
        "matched_features": sorted(best.matched_features),
        "gap_days": round(best.gap_days, 3),
    }


def persist_story_cluster(
    session: Any,
    candidates: Sequence[StoryCandidate],
    *,
    summarizer: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Idempotently persist one cross-country cluster and all derived slices."""

    if len({item.country_code for item in candidates}) < 2:
        raise ValueError("A story must contain at least two countries")
    now = _as_utc(now or datetime.now(timezone.utc))
    articles = {
        article.article_id: article
        for candidate in candidates
        for article in candidate.articles
    }
    article_ids = sorted({article_id for item in candidates for article_id in item.article_ids})
    dates = [
        date
        for item in candidates
        for date in (item.first_seen, item.last_seen)
        if date is not None
    ]
    if not article_ids or not dates:
        raise ValueError("A story requires article memberships and timestamps")
    first_seen, last_seen = min(dates), max(dates)
    source_hash = compute_source_hash(candidates)
    proposed_slug = _story_slug(candidates, first_seen)

    existing = session.execute(text("""
        SELECT st.id, st.slug, st.title_ru, st.title_en, st.summary,
               st.summary_model, st.source_hash, st.article_count,
               st.highest_action_level,
               (SELECT COUNT(*) FROM story_articles overlap_sa
                WHERE overlap_sa.story_id = st.id
                  AND overlap_sa.article_id = ANY(:article_ids)) AS article_overlap
        FROM stories st
        WHERE st.slug = :slug
           OR EXISTS (
               SELECT 1 FROM story_articles matching_sa
               WHERE matching_sa.story_id = st.id
                 AND matching_sa.article_id = ANY(:article_ids)
           )
        ORDER BY article_overlap DESC, (st.slug = :slug) DESC, st.id
        LIMIT 1
    """), {"article_ids": article_ids, "slug": proposed_slug}).fetchone()
    previous_copy = None
    if existing:
        previous_copy = StoryCopy(
            title_ru=_value(existing, "title_ru"),
            title_en=_value(existing, "title_en"),
            summary=_value(existing, "summary") or "",
            summary_model=_value(existing, "summary_model"),
        )
    copy = resolve_story_copy(
        candidates,
        source_hash=source_hash,
        previous_source_hash=_value(existing, "source_hash") if existing else None,
        previous_copy=previous_copy,
        summarizer=summarizer,
    )

    recent_count = sum(
        1 for article in articles.values()
        if article.published_at and _as_utc(article.published_at) >= now - timedelta(days=1)
    )
    previous_count = int(_value(existing, "article_count", 0) or 0) if existing else 0
    highest_action = max((item.highest_action_level for item in candidates), default=1)
    lifecycle = transition_lifecycle(
        now=now,
        first_seen=first_seen,
        last_seen=last_seen,
        article_count=len(article_ids),
        recent_article_count=recent_count,
        previous_article_count=previous_count,
        highest_action_level=highest_action,
        previous_action_level=int(_value(existing, "highest_action_level", 1) or 1) if existing else 1,
    )
    pair_scores = [
        score_story_match(left, right).total
        for index, left in enumerate(candidates)
        for right in candidates[index + 1:]
        if left.country_code != right.country_code
    ]
    confidence = round(sum(pair_scores) / len(pair_scores), 3) if pair_scores else MERGE_THRESHOLD
    meta = json.dumps({
        "thread_ids": sorted(item.thread_id for item in candidates),
        "topics": sorted({topic for item in candidates for topic in item.topics}),
    }, ensure_ascii=False)
    params = {
        "slug": _value(existing, "slug") if existing else proposed_slug,
        "title_ru": copy.title_ru,
        "title_en": copy.title_en,
        "summary": copy.summary,
        "lifecycle": lifecycle,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "article_count": len(article_ids),
        "source_count": len({item for candidate in candidates for item in candidate.sources}),
        "country_count": len({item.country_code for item in candidates}),
        "highest_action_level": highest_action,
        "confidence": confidence,
        "summary_model": copy.summary_model,
        "source_hash": source_hash,
        "meta": meta,
        "now": now,
    }
    if existing:
        params["story_id"] = _value(existing, "id")
        story_id = session.execute(text("""
            UPDATE stories SET
                title_ru = :title_ru, title_en = :title_en, summary = :summary,
                lifecycle = :lifecycle, first_seen = :first_seen, last_seen = :last_seen,
                article_count = :article_count, source_count = :source_count,
                country_count = :country_count, highest_action_level = :highest_action_level,
                clustering_confidence = :confidence, summary_model = :summary_model,
                source_hash = :source_hash, meta = CAST(:meta AS jsonb),
                generated_at = :now, updated_at = :now
            WHERE id = :story_id
            RETURNING id
        """), params).fetchone()[0]
    else:
        story_id = session.execute(text("""
            INSERT INTO stories (
                slug, title_ru, title_en, summary, lifecycle, first_seen, last_seen,
                article_count, source_count, country_count, highest_action_level,
                clustering_confidence, summary_model, source_hash, meta,
                generated_at, updated_at
            ) VALUES (
                :slug, :title_ru, :title_en, :summary, :lifecycle, :first_seen, :last_seen,
                :article_count, :source_count, :country_count, :highest_action_level,
                :confidence, :summary_model, :source_hash, CAST(:meta AS jsonb),
                :now, :now
            )
            ON CONFLICT (slug) DO UPDATE SET
                title_ru = EXCLUDED.title_ru,
                title_en = EXCLUDED.title_en,
                summary = EXCLUDED.summary,
                lifecycle = EXCLUDED.lifecycle,
                first_seen = LEAST(stories.first_seen, EXCLUDED.first_seen),
                last_seen = GREATEST(stories.last_seen, EXCLUDED.last_seen),
                article_count = EXCLUDED.article_count,
                source_count = EXCLUDED.source_count,
                country_count = EXCLUDED.country_count,
                highest_action_level = EXCLUDED.highest_action_level,
                clustering_confidence = EXCLUDED.clustering_confidence,
                summary_model = EXCLUDED.summary_model,
                source_hash = EXCLUDED.source_hash,
                meta = EXCLUDED.meta,
                generated_at = EXCLUDED.generated_at,
                updated_at = EXCLUDED.updated_at
            RETURNING id
        """), params).fetchone()[0]

    session.execute(text("""
        DELETE FROM story_articles
        WHERE story_id = :story_id AND NOT (article_id = ANY(:article_ids))
    """), {"story_id": story_id, "article_ids": article_ids})
    for candidate in candidates:
        membership_confidence, evidence = _membership_evidence(candidate, candidates)
        for article_id in candidate.article_ids:
            session.execute(text("""
                INSERT INTO story_articles (
                    story_id, article_id, membership_confidence, evidence
                ) VALUES (:story_id, :article_id, :confidence, CAST(:evidence AS jsonb))
                ON CONFLICT (story_id, article_id) DO UPDATE SET
                    membership_confidence = EXCLUDED.membership_confidence,
                    evidence = EXCLUDED.evidence
            """), {
                "story_id": story_id,
                "article_id": article_id,
                "confidence": round(membership_confidence, 3),
                "evidence": json.dumps(evidence, ensure_ascii=False),
            })

    session.execute(text("""
        UPDATE stories st SET
            article_count = stats.article_count,
            source_count = stats.source_count,
            country_count = stats.country_count,
            highest_action_level = stats.highest_action_level,
            first_seen = stats.first_seen,
            last_seen = stats.last_seen,
            updated_at = :now
        FROM (
            SELECT sa.story_id, COUNT(DISTINCT ar.id) AS article_count,
                   COUNT(DISTINCT ar.source_id) AS source_count,
                   COUNT(DISTINCT s.country_code) AS country_count,
                   MAX(COALESCE(an.action_level, 1)) AS highest_action_level,
                   MIN(ar.published_at) AS first_seen,
                   MAX(ar.published_at) AS last_seen
            FROM story_articles sa
            JOIN articles ar ON ar.id = sa.article_id
            JOIN sources s ON s.id = ar.source_id
            LEFT JOIN analysis an ON an.article_id = ar.id
            WHERE sa.story_id = :story_id
            GROUP BY sa.story_id
        ) stats
        WHERE st.id = stats.story_id
    """), {"story_id": story_id, "now": now})

    session.execute(text("DELETE FROM story_countries WHERE story_id = :story_id"), {"story_id": story_id})
    session.execute(text("""
        INSERT INTO story_countries (
            story_id, country_code, article_count, source_count,
            media_tone, first_seen, last_seen
        )
        SELECT :story_id, s.country_code, COUNT(DISTINCT ar.id),
               COUNT(DISTINCT ar.source_id), AVG(an.sentiment),
               MIN(ar.published_at), MAX(ar.published_at)
        FROM story_articles sa
        JOIN articles ar ON ar.id = sa.article_id
        JOIN sources s ON s.id = ar.source_id
        LEFT JOIN analysis an ON an.article_id = ar.id
        WHERE sa.story_id = :story_id
        GROUP BY s.country_code
    """), {"story_id": story_id})

    session.execute(text("DELETE FROM story_entities WHERE story_id = :story_id"), {"story_id": story_id})
    session.execute(text("""
        INSERT INTO story_entities (
            story_id, entity_id, mentions, confidence, evidence
        )
        SELECT :story_id, aem.entity_id, COUNT(*), 1.0,
               jsonb_build_object('article_ids', jsonb_agg(DISTINCT aem.article_id))
        FROM story_articles sa
        JOIN article_entity_mentions aem ON aem.article_id = sa.article_id
        WHERE sa.story_id = :story_id
        GROUP BY aem.entity_id
    """), {"story_id": story_id})

    session.execute(text("DELETE FROM story_events WHERE story_id = :story_id"), {"story_id": story_id})
    session.execute(text("""
        INSERT INTO story_events (
            story_id, entity_id, event_key, event_at, action_level, evidence
        )
        SELECT :story_id, aem.entity_id,
               (array_agg(COALESCE(NULLIF(an.event_key, ''), ar.title, 'story event')
                          ORDER BY ar.published_at DESC NULLS LAST))[1],
               MAX(ar.published_at), MAX(COALESCE(an.action_level, 1)),
               jsonb_build_object('article_ids', jsonb_agg(DISTINCT ar.id))
        FROM story_articles sa
        JOIN article_entity_mentions aem ON aem.article_id = sa.article_id
        JOIN articles ar ON ar.id = sa.article_id
        LEFT JOIN analysis an ON an.article_id = ar.id
        WHERE sa.story_id = :story_id
        GROUP BY aem.entity_id
    """), {"story_id": story_id})
    return story_id, len(article_ids)


def refresh_story_lifecycles(session: Any, *, now: datetime) -> None:
    session.execute(text("""
        UPDATE stories SET lifecycle = CASE
            WHEN last_seen < :now - INTERVAL '14 days' THEN 'resolved'
            WHEN last_seen <= :now - INTERVAL '3 days' THEN 'cooling'
            ELSE lifecycle
        END,
        updated_at = :now
        WHERE last_seen <= :now - INTERVAL '3 days'
    """), {"now": now})


def build_stories(
    session: Any,
    *,
    summarizer: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    now: datetime | None = None,
) -> StoryBuildResult:
    """Run the global story build inside the existing background cycle."""

    now = _as_utc(now or datetime.now(timezone.utc))
    candidates = fetch_story_candidates(session)
    clusters = cluster_story_candidates(candidates)
    stories_upserted = 0
    memberships = 0
    for cluster in clusters:
        _, count = persist_story_cluster(session, cluster, summarizer=summarizer, now=now)
        stories_upserted += 1
        memberships += count
    refresh_story_lifecycles(session, now=now)
    return StoryBuildResult(
        candidates=len(candidates),
        clusters=len(clusters),
        stories_upserted=stories_upserted,
        article_memberships=memberships,
    )
