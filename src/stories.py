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
MIN_MEANINGFUL_OVERLAP = 0.20
TITLE_FALLBACK_EVENT_CEILING = 0.85
STORY_COMPONENT_WEIGHTS = {
    "event_key": 0.35,
    "entities": 0.25,
    "topics": 0.15,
    "time": 0.10,
    "source_diversity": 0.05,
    "country_diversity": 0.05,
    "title": 0.05,
}


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
    left_activity = [
        _as_utc(article.published_at)
        for article in left.articles
        if article.published_at is not None
    ]
    right_activity = [
        _as_utc(article.published_at)
        for article in right.articles
        if article.published_at is not None
    ]
    if left_activity and right_activity:
        return min(
            abs((left_date - right_date).total_seconds()) / 86400.0
            for left_date in left_activity
            for right_date in right_activity
        )
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
    raw_entity_overlap = _jaccard(left.entities, right.entities)
    raw_topic_overlap = _jaccard(left.topics, right.topics)
    components = {
        "event_key": trigram_similarity(left.event_key, right.event_key),
        "entities": raw_entity_overlap if raw_entity_overlap >= MIN_MEANINGFUL_OVERLAP else 0.0,
        "topics": raw_topic_overlap if raw_topic_overlap >= MIN_MEANINGFUL_OVERLAP else 0.0,
        "time": max(0.0, 1.0 - gap_days / MAX_MERGE_GAP_DAYS),
        "source_diversity": 1.0 if left.sources and right.sources and left.sources != right.sources else 0.0,
        "country_diversity": 1.0 if left.country_code != right.country_code else 0.0,
        "title": trigram_similarity(left.title, right.title),
    }
    effective_components = dict(components)
    if components["event_key"] >= TITLE_FALLBACK_EVENT_CEILING:
        effective_components["title"] = 0.0
    total = round(
        sum(
            effective_components[name] * weight
            for name, weight in STORY_COMPONENT_WEIGHTS.items()
        ),
        6,
    )

    matched_features: set[str] = set()
    if components["event_key"] >= 0.45:
        matched_features.add("event_key")
    if components["entities"] >= MIN_MEANINGFUL_OVERLAP:
        matched_features.add("entities")
    if components["topics"] >= MIN_MEANINGFUL_OVERLAP:
        matched_features.add("topics")
    if (
        components["title"] >= 0.65
        and components["event_key"] < TITLE_FALLBACK_EVENT_CEILING
    ):
        matched_features.add("title")

    evidence = {
        "countries": sorted({left.country_code, right.country_code}),
        "shared_entities": sorted(left.entities & right.entities),
        "shared_topics": sorted(left.topics & right.topics),
        "event_keys": [left.event_key, right.event_key],
        "raw_entity_overlap": raw_entity_overlap,
        "raw_topic_overlap": raw_topic_overlap,
        "title_used_as_fallback": effective_components["title"] > 0,
        "effective_components": effective_components,
        "weights": STORY_COMPONENT_WEIGHTS,
        "score": total,
        "non_merge_reasons": [
            reason
            for condition, reason in (
                (total < MERGE_THRESHOLD, "score_below_threshold"),
                (len(matched_features) < 2, "insufficient_independent_features"),
                (gap_days > MAX_MERGE_GAP_DAYS, "time_window_exceeded"),
            )
            if condition
        ],
    }
    return StorySimilarity(
        total=total,
        components=components,
        matched_features=frozenset(matched_features),
        gap_days=gap_days,
        evidence=evidence,
    )


def merge_rejection_reasons(
    similarity: StorySimilarity,
    *,
    explicit_reactivation: bool = False,
) -> tuple[str, ...]:
    """Return stable, explainable reasons a pair cannot merge."""

    reasons = []
    if similarity.total < MERGE_THRESHOLD:
        reasons.append("score_below_threshold")
    if len(similarity.matched_features) < 2:
        reasons.append("insufficient_independent_features")
    if similarity.gap_days > MAX_MERGE_GAP_DAYS:
        if not explicit_reactivation:
            reasons.append("time_window_exceeded")
        elif not {"event_key", "entities"}.issubset(similarity.matched_features):
            reasons.append("reactivation_requires_event_and_entity")
    return tuple(reasons)


def should_merge(
    similarity: StorySimilarity,
    *,
    explicit_reactivation: bool = False,
) -> bool:
    """Apply threshold, independent-evidence, and time-window merge gates."""

    return not merge_rejection_reasons(
        similarity, explicit_reactivation=explicit_reactivation
    )


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
    grouped: list[list[StoryCandidate]] = []
    for candidate in ordered:
        for cluster in grouped:
            if all(
                should_merge(
                    score_story_match(candidate, member),
                    explicit_reactivation=tuple(sorted(
                        (candidate.thread_id, member.thread_id)
                    )) in reactivation_pairs,
                )
                for member in cluster
            ):
                cluster.append(candidate)
                break
        else:
            grouped.append([candidate])

    clusters = [
        tuple(items) for items in grouped
        if len({item.country_code for item in items}) >= 2
    ]
    return sorted(
        clusters,
        key=lambda group: (
            min(item.first_seen or datetime.max.replace(tzinfo=timezone.utc) for item in group),
            tuple(item.thread_id for item in group),
        ),
    )


def _copy_input_payload(
    candidates: Sequence[StoryCandidate],
    *,
    member_article_ids: Sequence[int] | None = None,
    member_evidence: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the exact deterministic and LLM copy inputs in stable form."""

    def stable_value(value: Any) -> Any:
        if isinstance(value, datetime):
            return _as_utc(value).isoformat()
        if isinstance(value, dict):
            return {
                str(key): stable_value(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        if isinstance(value, (set, frozenset, tuple, list)):
            converted = [stable_value(item) for item in value]
            try:
                return sorted(converted)
            except TypeError:
                return converted
        return value

    candidate_payload = []
    for item in sorted(
        candidates, key=lambda candidate: (candidate.country_code, candidate.thread_id)
    ):
        candidate_payload.append({
            "thread_id": item.thread_id,
            "country": item.country_code,
            "event_key": item.event_key,
            "normalized_event_key": _normalized_text(item.event_key),
            "title": item.title,
            "normalized_title": _normalized_text(item.title),
            "article_ids": sorted(item.article_ids),
            "entities": sorted(item.entities),
            "topics": sorted(item.topics),
            "sources": sorted(item.sources),
            "first_seen": _as_utc(item.first_seen).isoformat() if item.first_seen else None,
            "last_seen": _as_utc(item.last_seen).isoformat() if item.last_seen else None,
            "highest_action_level": item.highest_action_level,
        })
    final_article_ids = sorted({
        int(article_id)
        for article_id in (
            member_article_ids
            if member_article_ids is not None
            else [
                article_id
                for candidate in candidates
                for article_id in candidate.article_ids
            ]
        )
    })
    stable_evidence = [stable_value(item) for item in (member_evidence or ())]
    stable_evidence.sort(
        key=lambda item: (
            int(item.get("article_id", 0)) if isinstance(item, dict) else 0,
            json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
    )
    return {
        "candidates": candidate_payload,
        "member_article_ids": final_article_ids,
        "member_evidence": stable_evidence,
    }


def compute_source_hash(
    candidates: Sequence[StoryCandidate],
    *,
    member_article_ids: Sequence[int] | None = None,
    member_evidence: Sequence[dict[str, Any]] | None = None,
) -> str:
    """Hash every deterministic story input that can affect persisted copy."""

    payload = _copy_input_payload(
        candidates,
        member_article_ids=member_article_ids,
        member_evidence=member_evidence,
    )
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def deterministic_story_copy(
    candidates: Sequence[StoryCandidate],
    *,
    member_article_ids: Sequence[int] | None = None,
    member_evidence: Sequence[dict[str, Any]] | None = None,
) -> StoryCopy:
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
    countries = sorted(
        {item.country_code for item in candidates}
        | {
            str(item.get("country_code"))
            for item in (member_evidence or ())
            if isinstance(item, dict) and item.get("country_code")
        }
    )
    article_count = len({
        int(article_id)
        for article_id in (
            member_article_ids
            if member_article_ids is not None
            else [
                article_id
                for item in candidates
                for article_id in item.article_ids
            ]
        )
    })
    summary = (
        f"Сюжет объединяет {article_count} публикаций из стран "
        f"{', '.join(countries)} по событию «{title.rstrip('.')}»."
    )
    return StoryCopy(title_ru=title, title_en=None, summary=summary)


def _summary_payload(
    candidates: Sequence[StoryCandidate],
    fallback: StoryCopy,
    *,
    member_article_ids: Sequence[int] | None = None,
    member_evidence: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    copy_inputs = _copy_input_payload(
        candidates,
        member_article_ids=member_article_ids,
        member_evidence=member_evidence,
    )
    final_evidence = [
        item for item in copy_inputs["member_evidence"] if isinstance(item, dict)
    ]
    return {
        "fallback": {
            "title_ru": fallback.title_ru,
            "summary": fallback.summary,
        },
        "countries": sorted(
            {item.country_code for item in candidates}
            | {
                str(item.get("country_code"))
                for item in final_evidence if item.get("country_code")
            }
        ),
        "event_keys": sorted(
            {item.event_key for item in candidates if item.event_key}
            | {
                str(item.get("event_key"))
                for item in final_evidence if item.get("event_key")
            }
        ),
        "titles": sorted(
            {item.title for item in candidates if item.title}
            | {
                str(item.get("title"))
                for item in final_evidence if item.get("title")
            }
        ),
        "topics": sorted(
            {topic for item in candidates for topic in item.topics}
            | {
                str(topic)
                for item in final_evidence
                for topic in (item.get("topics") or [])
                if topic
            }
        ),
        "article_count": len(copy_inputs["member_article_ids"]),
        "article_ids": copy_inputs["member_article_ids"],
        "member_evidence": copy_inputs["member_evidence"],
        "candidate_inputs": copy_inputs["candidates"],
    }


def resolve_story_copy(
    candidates: Sequence[StoryCandidate],
    *,
    source_hash: str,
    previous_source_hash: str | None,
    previous_copy: StoryCopy | None,
    summarizer: Callable[[dict[str, Any]], dict[str, Any] | None] | None,
    member_article_ids: Sequence[int] | None = None,
    member_evidence: Sequence[dict[str, Any]] | None = None,
) -> StoryCopy:
    """Use generated copy only on source changes; otherwise preserve stored copy."""

    fallback = deterministic_story_copy(
        candidates,
        member_article_ids=member_article_ids,
        member_evidence=member_evidence,
    )
    if source_hash == previous_source_hash and previous_copy is not None:
        return previous_copy
    if summarizer is None:
        return fallback
    try:
        generated = summarizer(_summary_payload(
            candidates,
            fallback,
            member_article_ids=member_article_ids,
            member_evidence=member_evidence,
        ))
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


def derive_reactivation_pairs(
    session: Any,
    candidates: Sequence[StoryCandidate],
) -> frozenset[tuple[int, int]]:
    """Derive explicit long-gap pairs anchored in a persisted resolved story."""

    if not hasattr(session, "execute"):
        return frozenset()
    rows = session.execute(text("""
        SELECT st.id, st.lifecycle, st.last_seen, st.meta
        FROM stories st
        WHERE st.lifecycle = 'resolved'
          AND NOT (COALESCE(st.meta, '{}'::jsonb) ? 'merged_into_story_id')
        ORDER BY st.id
    """)).fetchall()
    by_thread_id = {candidate.thread_id: candidate for candidate in candidates}
    pairs: set[tuple[int, int]] = set()
    ordered = sorted(candidates, key=lambda item: (item.country_code, item.thread_id))
    for row in rows:
        if _value(row, "lifecycle") != "resolved":
            continue
        persisted_last_seen = _value(row, "last_seen")
        if persisted_last_seen is None:
            continue
        persisted_last_seen = _as_utc(persisted_last_seen)
        meta = _value(row, "meta", {}) or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except json.JSONDecodeError:
                meta = {}
        if not isinstance(meta, dict):
            continue
        stored_thread_ids = {
            thread_id for thread_id in (meta.get("thread_ids") or [])
            if isinstance(thread_id, int) and not isinstance(thread_id, bool)
        }
        anchors = [
            by_thread_id[thread_id]
            for thread_id in sorted(stored_thread_ids)
            if thread_id in by_thread_id
        ]
        for anchor in anchors:
            for candidate in ordered:
                if (
                    candidate.thread_id == anchor.thread_id
                    or candidate.country_code == anchor.country_code
                ):
                    continue
                candidate_activity = [
                    _as_utc(article.published_at)
                    for article in candidate.articles
                    if article.published_at is not None
                ] or [
                    _as_utc(date)
                    for date in (candidate.first_seen, candidate.last_seen)
                    if date is not None
                ]
                if not any(date > persisted_last_seen for date in candidate_activity):
                    continue
                pair = tuple(sorted((anchor.thread_id, candidate.thread_id)))
                similarity = score_story_match(anchor, candidate)
                if (
                    similarity.gap_days > MAX_MERGE_GAP_DAYS
                    and should_merge(similarity, explicit_reactivation=True)
                ):
                    pairs.add(pair)
    return frozenset(pairs)


def fetch_story_member_evidence(
    session: Any,
    article_ids: Sequence[int],
) -> list[dict[str, Any]]:
    """Load deterministic evidence for the complete persisted membership union."""

    if not article_ids:
        return []
    rows = session.execute(text("""
        SELECT ar.id AS article_id, ar.title, ar.published_at,
               TRIM(s.country_code) AS country_code, s.name AS source_name,
               COALESCE(NULLIF(an.event_key, ''), ar.title, '') AS event_key,
               COALESCE(an.topics, ARRAY[]::text[]) AS topics,
               COALESCE(an.action_level, 1) AS action_level,
               COALESCE((
                   SELECT array_agg(DISTINCT aem.entity_id::text ORDER BY aem.entity_id::text)
                   FROM article_entity_mentions aem
                   WHERE aem.article_id = ar.id
               ), ARRAY[]::text[]) AS entity_ids
        FROM articles ar
        JOIN sources s ON s.id = ar.source_id
        LEFT JOIN analysis an ON an.article_id = ar.id
        WHERE ar.id = ANY(:article_ids)
        ORDER BY ar.id
    """), {"article_ids": list(article_ids)}).fetchall()
    return [{
        "article_id": int(_value(row, "article_id")),
        "title": _value(row, "title"),
        "published_at": _iso_datetime(_value(row, "published_at")),
        "country_code": str(_value(row, "country_code", "")).strip(),
        "source_name": _value(row, "source_name"),
        "event_key": _value(row, "event_key"),
        "topics": sorted(str(topic) for topic in (_value(row, "topics") or [])),
        "action_level": int(_value(row, "action_level", 1) or 1),
        "entity_ids": sorted(str(entity_id) for entity_id in (_value(row, "entity_ids") or [])),
    } for row in rows]


def _iso_datetime(value: datetime | str | None) -> str | None:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat()
    return value


def _story_slug(candidates: Sequence[StoryCandidate], first_seen: datetime) -> str:
    identity = "|".join(sorted(_normalized_text(item.event_key) for item in candidates))
    event_digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
    anchor_thread_id = min(item.thread_id for item in candidates)
    anchor_digest = hashlib.sha256(str(anchor_thread_id).encode("ascii")).hexdigest()[:10]
    return f"story-{first_seen.date().isoformat()}-{event_digest}-{anchor_digest}"


def _membership_evidence(
    candidate: StoryCandidate,
    cluster: Sequence[StoryCandidate],
    reactivation_pairs: frozenset[tuple[int, int]],
    *,
    story_reactivation: bool = False,
) -> tuple[float, dict[str, Any]]:
    matches = [
        (other, score_story_match(candidate, other))
        for other in cluster
        if other.thread_id != candidate.thread_id and other.country_code != candidate.country_code
    ]
    if not matches:
        return 1.0, {
            "thread_id": candidate.thread_id,
            "country": candidate.country_code,
            "action_level_snapshot": candidate.highest_action_level,
            "explicit_reactivation": False,
        }
    best_other, best = max(
        matches,
        key=lambda match: (match[1].total, -match[0].thread_id),
    )
    pair = tuple(sorted((candidate.thread_id, best_other.thread_id)))
    explicit_reactivation = story_reactivation or (
        pair in reactivation_pairs and best.gap_days > MAX_MERGE_GAP_DAYS
    )
    return max(MERGE_THRESHOLD, best.total), {
        "thread_id": candidate.thread_id,
        "country": candidate.country_code,
        "action_level_snapshot": candidate.highest_action_level,
        "peer_thread_id": best_other.thread_id,
        "score": best.total,
        "components": best.components,
        "effective_components": best.evidence.get(
            "effective_components", best.components
        ),
        "weights": best.evidence.get("weights", STORY_COMPONENT_WEIGHTS),
        "matched_features": sorted(best.matched_features),
        "gap_days": round(best.gap_days, 3),
        "explicit_reactivation": explicit_reactivation,
        "non_merge_reasons": list(merge_rejection_reasons(
            best, explicit_reactivation=explicit_reactivation
        )),
    }


def persist_story_cluster(
    session: Any,
    candidates: Sequence[StoryCandidate],
    *,
    summarizer: Callable[[dict[str, Any]], dict[str, Any] | None] | None = None,
    now: datetime | None = None,
    reactivation_pairs: frozenset[tuple[int, int]] = frozenset(),
) -> tuple[int, int]:
    """Idempotently persist one cross-country cluster and all derived slices."""

    if len({item.country_code for item in candidates}) < 2:
        raise ValueError("A story must contain at least two countries")
    pairwise_matches = sorted(
        [
            (
                left,
                right,
                score_story_match(left, right),
                tuple(sorted((left.thread_id, right.thread_id)))
                in reactivation_pairs,
            )
            for index, left in enumerate(candidates)
            for right in candidates[index + 1:]
        ],
        key=lambda match: tuple(sorted((match[0].thread_id, match[1].thread_id))),
    )
    if any(
        not should_merge(similarity, explicit_reactivation=explicit_reactivation)
        for _, _, similarity, explicit_reactivation in pairwise_matches
    ):
        raise ValueError("Story cluster failed complete-link cohesion")
    now = _as_utc(now or datetime.now(timezone.utc))
    articles = {
        article.article_id: article
        for candidate in candidates
        for article in candidate.articles
    }
    current_article_ids = sorted({
        article_id for item in candidates for article_id in item.article_ids
    })
    activity_dates = [
        _as_utc(article.published_at)
        for article in articles.values()
        if article.published_at is not None
    ]
    if not activity_dates:
        activity_dates = [
            _as_utc(date)
            for item in candidates
            for date in (item.first_seen, item.last_seen)
            if date is not None
        ]
    if not current_article_ids or not activity_dates:
        raise ValueError("A story requires article memberships and timestamps")
    current_first_seen, current_last_seen = min(activity_dates), max(activity_dates)
    proposed_slug = _story_slug(candidates, current_first_seen)
    thread_ids = sorted(item.thread_id for item in candidates)

    matched_rows = session.execute(text("""
        SELECT st.id, st.slug, st.title_ru, st.title_en, st.summary,
               st.summary_model, st.source_hash, st.article_count,
               st.highest_action_level, st.generated_at, st.meta,
               st.lifecycle, st.first_seen, st.last_seen,
               (SELECT COALESCE(
                    array_agg(member_sa.article_id ORDER BY member_sa.article_id),
                    ARRAY[]::integer[]
                )
                FROM story_articles member_sa
                WHERE member_sa.story_id = st.id) AS member_article_ids,
               (SELECT COUNT(*) FROM story_articles overlap_sa
                WHERE overlap_sa.story_id = st.id
                  AND overlap_sa.article_id = ANY(:article_ids)) AS article_overlap,
                (SELECT COUNT(*)
                FROM jsonb_array_elements_text(COALESCE(st.meta->'thread_ids', '[]'::jsonb))
                     AS stored_thread(value)
                WHERE stored_thread.value::bigint = ANY(:thread_ids)) AS thread_overlap
        FROM stories st
        WHERE EXISTS (
               SELECT 1 FROM story_articles matching_sa
               WHERE matching_sa.story_id = st.id
                 AND matching_sa.article_id = ANY(:article_ids)
           )
           OR EXISTS (
               SELECT 1
               FROM jsonb_array_elements_text(COALESCE(st.meta->'thread_ids', '[]'::jsonb))
                    AS matching_thread(value)
               WHERE matching_thread.value::bigint = ANY(:thread_ids)
           )
        ORDER BY
            CASE WHEN COALESCE(st.meta, '{}'::jsonb) ? 'merged_into_story_id'
                 THEN 1 ELSE 0 END,
            article_overlap DESC, thread_overlap DESC, st.id
        FOR UPDATE OF st
    """), {"article_ids": current_article_ids, "thread_ids": thread_ids}).fetchall()

    reactivation_gate_matches = [
        similarity
        for left, right, similarity, _ in pairwise_matches
        if left.country_code != right.country_code
    ]
    reactivation_gate_passed = bool(reactivation_gate_matches) and all(
        {"event_key", "entities"}.issubset(similarity.matched_features)
        for similarity in reactivation_gate_matches
    )
    existing_matches: list[Any] = []
    reactivated_matches: list[tuple[Any, datetime, datetime]] = []
    denied_reactivation_starts: list[datetime] = []
    for matched_row in matched_rows:
        if _value(matched_row, "lifecycle") != "resolved":
            existing_matches.append(matched_row)
            continue
        prior_last_seen = _value(matched_row, "last_seen")
        if prior_last_seen is None:
            # A resolved identity cannot be reopened without a persisted temporal anchor.
            continue
        prior_last_seen = _as_utc(prior_last_seen)
        new_activity = sorted(date for date in activity_dates if date > prior_last_seen)
        if not new_activity:
            existing_matches.append(matched_row)
            continue
        if not reactivation_gate_passed:
            # Keep the resolved story closed; this cluster receives a new identity.
            denied_reactivation_starts.append(new_activity[0])
            continue
        existing_matches.append(matched_row)
        reactivated_matches.append((matched_row, prior_last_seen, new_activity[0]))

    existing = existing_matches[0] if existing_matches else None
    if existing is None and denied_reactivation_starts:
        proposed_slug = _story_slug(candidates, min(denied_reactivation_starts))
    duplicate_story_ids = [_value(row, "id") for row in existing_matches[1:]]
    article_ids = sorted({
        int(article_id)
        for article_id in current_article_ids
    } | {
        int(article_id)
        for row in existing_matches
        for article_id in (_value(row, "member_article_ids", []) or [])
        if article_id is not None
    })
    persisted_dates = [
        _as_utc(value)
        for row in existing_matches
        for value in (_value(row, "first_seen"), _value(row, "last_seen"))
        if value is not None
    ]
    first_seen = min([current_first_seen, *persisted_dates])
    last_seen = max([current_last_seen, *persisted_dates])
    member_evidence = fetch_story_member_evidence(session, article_ids)
    source_hash = compute_source_hash(
        candidates,
        member_article_ids=article_ids,
        member_evidence=member_evidence,
    )
    previous_copy = None
    if existing:
        previous_copy = StoryCopy(
            title_ru=_value(existing, "title_ru"),
            title_en=_value(existing, "title_en"),
            summary=_value(existing, "summary") or "",
            summary_model=_value(existing, "summary_model"),
        )
    copy_regenerated = (
        previous_copy is None
        or source_hash != (_value(existing, "source_hash") if existing else None)
    )
    copy = resolve_story_copy(
        candidates,
        source_hash=source_hash,
        previous_source_hash=_value(existing, "source_hash") if existing else None,
        previous_copy=previous_copy,
        summarizer=summarizer,
        member_article_ids=article_ids,
        member_evidence=member_evidence,
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
    story_reactivation = bool(reactivated_matches)
    if (
        existing
        and _value(existing, "lifecycle") == "resolved"
        and not story_reactivation
    ):
        lifecycle = "resolved"
    pair_scores = [similarity.total for _, _, similarity, _ in pairwise_matches]
    confidence = round(min(pair_scores), 3) if pair_scores else MERGE_THRESHOLD
    existing_meta: dict[str, Any] = {}
    merge_audit: list[dict[str, Any]] = []
    reactivation_history: list[dict[str, Any]] = []
    merged_story_ids: set[int] = set(duplicate_story_ids)
    persisted_thread_ids: set[int] = set(thread_ids)
    persisted_topics: set[str] = {
        topic for candidate in candidates for topic in candidate.topics
    }
    for matched_story in existing_matches:
        matched_meta = _value(matched_story, "meta", {}) or {}
        if isinstance(matched_meta, str):
            try:
                matched_meta = json.loads(matched_meta)
            except json.JSONDecodeError:
                matched_meta = {}
        if not isinstance(matched_meta, dict):
            continue
        if not existing_meta:
            existing_meta = dict(matched_meta)
        persisted_thread_ids.update(
            thread_id for thread_id in (matched_meta.get("thread_ids") or [])
            if isinstance(thread_id, int) and not isinstance(thread_id, bool)
        )
        persisted_topics.update(
            str(topic) for topic in (matched_meta.get("topics") or []) if topic
        )
        merge_audit.extend(
            item for item in (matched_meta.get("merge_audit") or [])
            if isinstance(item, dict)
        )
        reactivation_history.extend(
            item for item in (matched_meta.get("reactivations") or [])
            if isinstance(item, dict)
        )
        merged_story_ids.update(
            story_id for story_id in (matched_meta.get("merged_story_ids") or [])
            if isinstance(story_id, int) and not isinstance(story_id, bool)
        )

    def preserve_audit_order(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduplicated: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            decision_id = item.get("decision_id")
            identity = str(decision_id) if decision_id else hashlib.sha256(
                json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            if identity not in seen:
                deduplicated.append(item)
                seen.add(identity)
        return deduplicated

    merge_audit = preserve_audit_order(merge_audit)
    reactivation_history = preserve_audit_order(reactivation_history)
    known_audit_ids = {
        item.get("decision_id") for item in merge_audit if isinstance(item, dict)
    }
    known_reactivation_ids = {
        item.get("decision_id")
        for item in reactivation_history if isinstance(item, dict)
    }
    for left, right, similarity, explicit_reactivation in pairwise_matches:
        pair = sorted((left.thread_id, right.thread_id))
        pair_reactivation = (
            explicit_reactivation
            and similarity.gap_days > MAX_MERGE_GAP_DAYS
        )
        decision_id = hashlib.sha256(
            f"{pair[0]}:{pair[1]}:{source_hash}:{int(pair_reactivation)}".encode("ascii")
        ).hexdigest()
        audit_entry = {
            "decision_id": decision_id,
            "decision": "reactivation" if pair_reactivation else "merge",
            "thread_ids": pair,
            "score": similarity.total,
            "gap_days": round(similarity.gap_days, 3),
            "matched_features": sorted(similarity.matched_features),
            "components": similarity.components,
            "effective_components": similarity.evidence.get(
                "effective_components", similarity.components
            ),
            "weights": similarity.evidence.get("weights", STORY_COMPONENT_WEIGHTS),
            "decided_at": now.isoformat(),
        }
        if decision_id not in known_audit_ids:
            merge_audit.append(audit_entry)
            known_audit_ids.add(decision_id)
        if pair_reactivation:
            if decision_id not in known_reactivation_ids:
                reactivation_history.append(audit_entry)
                known_reactivation_ids.add(decision_id)
    for reactivated_story, prior_last_seen, new_activity_at in reactivated_matches:
        story_id_value = int(_value(reactivated_story, "id"))
        decision_id = hashlib.sha256(
            (
                f"story:{story_id_value}:{prior_last_seen.isoformat()}:"
                f"{new_activity_at.isoformat()}:{source_hash}"
            ).encode("utf-8")
        ).hexdigest()
        story_audit_entry = {
            "decision_id": decision_id,
            "decision": "reactivation",
            "story_id": story_id_value,
            "thread_ids": thread_ids,
            "score": confidence,
            "gap_days": round(
                (new_activity_at - prior_last_seen).total_seconds() / 86400.0,
                3,
            ),
            "prior_last_seen": prior_last_seen.isoformat(),
            "new_activity_at": new_activity_at.isoformat(),
            "matched_features": ["entities", "event_key"],
            "gate": {
                "required_features": ["event_key", "entities"],
                "passed": True,
            },
            "pair_evidence": [
                {
                    "thread_ids": sorted((left.thread_id, right.thread_id)),
                    "score": similarity.total,
                    "matched_features": sorted(similarity.matched_features),
                    "components": similarity.components,
                    "effective_components": similarity.evidence.get(
                        "effective_components", similarity.components
                    ),
                    "weights": similarity.evidence.get(
                        "weights", STORY_COMPONENT_WEIGHTS
                    ),
                }
                for left, right, similarity, _ in pairwise_matches
                if left.country_code != right.country_code
            ],
            "decided_at": now.isoformat(),
        }
        if decision_id not in known_audit_ids:
            merge_audit.append(story_audit_entry)
            known_audit_ids.add(decision_id)
        if decision_id not in known_reactivation_ids:
            reactivation_history.append(story_audit_entry)
            known_reactivation_ids.add(decision_id)
    meta_payload = dict(existing_meta)
    meta_payload.update({
        "thread_ids": sorted(persisted_thread_ids),
        "topics": sorted(persisted_topics),
        "merge_audit": merge_audit,
        "reactivations": reactivation_history,
        "merged_story_ids": sorted(merged_story_ids),
    })
    meta = json.dumps(meta_payload, ensure_ascii=False)
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
        "generated_at": now if copy_regenerated else _value(existing, "generated_at"),
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
                generated_at = :generated_at, updated_at = :now
            WHERE id = :story_id
            RETURNING id
        """), params).fetchone()[0]
    else:
        inserted = session.execute(text("""
            INSERT INTO stories (
                slug, title_ru, title_en, summary, lifecycle, first_seen, last_seen,
                article_count, source_count, country_count, highest_action_level,
                clustering_confidence, summary_model, source_hash, meta,
                generated_at, updated_at
            ) VALUES (
                :slug, :title_ru, :title_en, :summary, :lifecycle, :first_seen, :last_seen,
                :article_count, :source_count, :country_count, :highest_action_level,
                :confidence, :summary_model, :source_hash, CAST(:meta AS jsonb),
                :generated_at, :now
            )
            ON CONFLICT (slug) DO NOTHING
            RETURNING id
        """), params).fetchone()
        if not inserted:
            raise RuntimeError("Story slug collision without article or thread identity overlap")
        story_id = inserted[0]

    if duplicate_story_ids:
        reconciliation_params = {
            "primary_story_id": story_id,
            "duplicate_story_ids": duplicate_story_ids,
        }
        session.execute(text("""
            INSERT INTO story_articles (
                story_id, article_id, membership_confidence, evidence, added_at
            )
            SELECT :primary_story_id, article_id, membership_confidence, evidence, added_at
            FROM story_articles
            WHERE story_id = ANY(:duplicate_story_ids)
            ON CONFLICT (story_id, article_id) DO UPDATE SET
                membership_confidence = GREATEST(
                    story_articles.membership_confidence,
                    EXCLUDED.membership_confidence
                ),
                evidence = story_articles.evidence || EXCLUDED.evidence
                    || jsonb_build_object(
                        'action_level_snapshot',
                        COALESCE(
                            story_articles.evidence->'action_level_snapshot',
                            EXCLUDED.evidence->'action_level_snapshot'
                        )
                    )
        """), reconciliation_params)
        session.execute(text("""
            UPDATE stories SET
                lifecycle = 'resolved',
                meta = COALESCE(meta, '{}'::jsonb) || jsonb_build_object(
                    'merged_into_story_id', :primary_story_id,
                    'canonical_slug', :canonical_slug,
                    'superseded_at', :now
                ),
                updated_at = :now
            WHERE id = ANY(:duplicate_story_ids)
        """), {
            **reconciliation_params,
            "canonical_slug": params["slug"],
            "now": now,
        })

    for candidate in candidates:
        membership_confidence, evidence = _membership_evidence(
            candidate,
            candidates,
            reactivation_pairs,
            story_reactivation=story_reactivation,
        )
        for article_id in candidate.article_ids:
            session.execute(text("""
                INSERT INTO story_articles (
                    story_id, article_id, membership_confidence, evidence
                ) VALUES (:story_id, :article_id, :confidence, CAST(:evidence AS jsonb))
                ON CONFLICT (story_id, article_id) DO UPDATE SET
                    membership_confidence = EXCLUDED.membership_confidence,
                    evidence = EXCLUDED.evidence || jsonb_build_object(
                        'action_level_snapshot',
                        COALESCE(
                            story_articles.evidence->'action_level_snapshot',
                            EXCLUDED.evidence->'action_level_snapshot'
                        )
                    )
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
        SELECT :story_id, aem.entity_id, COUNT(*), AVG(aem.confidence),
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
        SELECT :story_id, representative.entity_id, representative.event_key,
               representative.published_at, representative.action_level,
               jsonb_build_object(
                   'article_ids', jsonb_build_array(representative.article_id),
                   'representative_article_id', representative.article_id
               )
        FROM (
            SELECT DISTINCT ON (aem.entity_id)
                   aem.entity_id, ar.id AS article_id,
                   COALESCE(NULLIF(an.event_key, ''), ar.title, 'story event') AS event_key,
                   ar.published_at, COALESCE(an.action_level, 1) AS action_level
            FROM story_articles sa
            JOIN article_entity_mentions aem ON aem.article_id = sa.article_id
            JOIN articles ar ON ar.id = sa.article_id
            LEFT JOIN analysis an ON an.article_id = ar.id
            WHERE sa.story_id = :story_id
            ORDER BY aem.entity_id, ar.published_at DESC NULLS LAST, ar.id DESC
        ) representative
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
    reactivation_pairs: frozenset[tuple[int, int]] = frozenset(),
) -> StoryBuildResult:
    """Run the global story build inside the existing background cycle."""

    now = _as_utc(now or datetime.now(timezone.utc))
    candidates = fetch_story_candidates(session)
    effective_reactivation_pairs = frozenset(
        set(reactivation_pairs) | set(derive_reactivation_pairs(session, candidates))
    )
    clusters = cluster_story_candidates(
        candidates, reactivation_pairs=effective_reactivation_pairs
    )
    stories_upserted = 0
    memberships = 0
    for cluster in clusters:
        _, count = persist_story_cluster(
            session,
            cluster,
            summarizer=summarizer,
            now=now,
            reactivation_pairs=effective_reactivation_pairs,
        )
        stories_upserted += 1
        memberships += count
    refresh_story_lifecycles(session, now=now)
    return StoryBuildResult(
        candidates=len(candidates),
        clusters=len(clusters),
        stories_upserted=stories_upserted,
        article_memberships=memberships,
    )
