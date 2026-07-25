"""Deterministic normalization of verified media coverage into Radar points."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timezone
import re
from typing import Any
from uuid import UUID

from sqlalchemy import text

from src.engine.health import _publisher_families

from .repository import make_observation
from .types import Contour, Observation, ObservationWindow


_MEDIA_ROWS = text("""
    SELECT a.id AS article_id, a.published_at, a.is_duplicate,
           source.country_code, source.id AS publisher_id,
           source.name AS publisher_name, source.url AS publisher_url,
           publisher.config AS publisher_config,
           story_link.story_id, analysis.event_key, analysis.sentiment,
           analysis.is_relevant, COALESCE(analysis.topics, ARRAY[]::text[]) AS topics,
           ARRAY(SELECT DISTINCT event.event_key FROM story_events event
                 WHERE event.story_id = story_link.story_id) AS story_event_keys,
           ARRAY(SELECT DISTINCT entity.entity_id FROM story_entities entity
                 WHERE entity.story_id = story_link.story_id) AS entity_ids,
           ARRAY(SELECT DISTINCT evidence.signal_id FROM signal_evidence evidence
                 WHERE a.id = ANY(evidence.article_ids)
                    OR story_link.story_id = ANY(evidence.story_ids)) AS signal_ids
    FROM articles a
    JOIN article_country_facts source ON source.article_id = a.id
    JOIN sources publisher ON publisher.id = source.id
    LEFT JOIN analysis ON analysis.article_id = a.id
    LEFT JOIN story_articles story_link ON story_link.article_id = a.id
    WHERE a.is_duplicate = FALSE
      AND a.published_at >= :window_start
      AND a.published_at < :window_end
      AND a.collected_at < :window_end
""")


def _value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping.get(name, default)
    return getattr(row, name, default)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _day_start(value: datetime) -> datetime:
    day = _utc(value).date()
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def _direction(sentiment: Any) -> str:
    value = float(sentiment or 0)
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "neutral"


def _alignment_direction(direction: str) -> str:
    return {
        "positive": "warming",
        "negative": "hardening",
        "neutral": "neutral",
    }[direction]


def _alignment_subject(subject: str, rows: list[Any]) -> str:
    """Align only an event key that names Russia and the structured dataset domain.

    Topic labels are intentionally insufficient: generic energy or diplomacy
    coverage must not corroborate a fossil-import or UN-vote action.
    """

    del rows  # Reserved for future explicit, verified event metadata.
    tokens = set(re.findall(r"[a-z0-9]+", subject.casefold()))
    if not tokens.intersection({"russia", "russian", "ru"}):
        return "russia:general"
    if tokens.intersection({"sanction", "sanctions", "embargo"}):
        return "policy:sanctions:russia"
    flow_terms = {"import", "imports", "export", "exports"}
    if (
        tokens.intersection({"energy", "fossil", "oil", "gas"})
        and tokens.intersection(flow_terms)
    ):
        return "energy:imports:russia"
    if tokens.intersection({"trade", "commerce"}) or tokens.intersection(flow_terms):
        return "economy:trade:russia"
    if (
        tokens.intersection({"un", "united", "nations"})
        and tokens.intersection({"vote", "votes", "voting", "alignment"})
    ):
        return "diplomacy:un_alignment:russia"
    return "russia:general"


def _coverage_confidence(*, article_count: int, source_count: int, family_count: int) -> float:
    """Estimate evidence breadth without treating a lone article as full coverage."""

    article_breadth = min(article_count / 5, 1.0)
    source_breadth = min(source_count / 3, 1.0)
    family_breadth = min(family_count / 2, 1.0)
    confidence = round(
        0.25 * article_breadth
        + 0.25 * source_breadth
        + 0.5 * family_breadth,
        4,
    )
    if source_count <= 1 or family_count <= 1:
        return min(confidence, 0.5)
    return confidence


def _canonical_entities(entity_ids: tuple[str, ...]) -> tuple[UUID, ...]:
    parsed: set[UUID] = set()
    for entity_id in entity_ids:
        try:
            parsed.add(UUID(entity_id))
        except (TypeError, ValueError, AttributeError):
            continue
    return tuple(sorted(parsed, key=str))


def _subjects(row: Any) -> tuple[str, ...]:
    """All persisted event associations, sorted before grouping for replayability."""

    events = sorted({str(value) for value in (_value(row, "story_event_keys", ()) or ()) if value})
    if events:
        return tuple(f"event:{event}" for event in events)
    event_key = _value(row, "event_key")
    if event_key:
        return (f"event:{str(event_key)}",)
    return ()


def _publisher_family_labels(rows: list[Any]) -> tuple[str, ...]:
    sources: dict[int, dict[str, Any]] = {}
    for row in rows:
        source_id = int(_value(row, "publisher_id"))
        sources.setdefault(source_id, {
            "id": source_id,
            "url": _value(row, "publisher_url"),
            "config": _value(row, "publisher_config", {}) or {},
        })
    families = _publisher_families(list(sources.values()))
    assigned = {
        int(source["id"])
        for members in families.values()
        for source in members
        if source.get("id") is not None
    }
    labels = set(families)
    labels.update(f"publisher:{source_id}" for source_id in sources if source_id not in assigned)
    return tuple(sorted(labels))


def build_media_observations(session, window: ObservationWindow) -> list[Observation]:
    """Build daily, country-scoped media observations from verified publisher facts.

    ``article_country_facts`` intentionally filters quarantined discovery rows;
    the additional article predicates make deduplication and future exclusion
    explicit for replayable callers and test doubles.
    """

    rows = session.execute(_MEDIA_ROWS, {
        "window_start": window.start,
        "window_end": window.end,
    }).fetchall()
    qualified: list[Any] = []
    seen_memberships: set[tuple[int, int | None]] = set()
    for row in rows:
        article_id = _value(row, "article_id")
        published_at = _value(row, "published_at")
        country_code = _value(row, "country_code")
        if (
            article_id is None or published_at is None or country_code is None
            or bool(_value(row, "is_duplicate", False))
        ):
            continue
        published_at = _utc(published_at)
        if not window.start <= published_at < window.end:
            continue
        membership = (int(article_id), _value(row, "story_id"))
        if membership in seen_memberships:
            continue
        seen_memberships.add(membership)
        qualified.append(row)

    national_coverage: dict[tuple[str, datetime], set[int]] = defaultdict(set)
    grouped: dict[tuple[str, str, str, str, datetime], list[Any]] = defaultdict(list)
    for row in qualified:
        country = str(_value(row, "country_code")).upper()
        day = _day_start(_value(row, "published_at"))
        article_id = int(_value(row, "article_id"))
        national_coverage[country, day].add(article_id)
        if _value(row, "is_relevant") is False:
            continue
        for subject in _subjects(row):
            grouped[country, subject, _direction(_value(row, "sentiment")), "attention_share", day].append(row)

    observations: list[Observation] = []
    for (country, subject, direction, metric, day), members in sorted(grouped.items()):
        article_ids = tuple(sorted({int(_value(row, "article_id")) for row in members}))
        story_ids = tuple(sorted({int(_value(row, "story_id")) for row in members if _value(row, "story_id") is not None}))
        signal_ids = tuple(sorted({int(item) for row in members for item in (_value(row, "signal_ids", ()) or ())}))
        entity_ids = tuple(sorted({str(item) for row in members for item in (_value(row, "entity_ids", ()) or ())}))
        canonical_entities = _canonical_entities(entity_ids)
        denominator = len(national_coverage[country, day])
        families = _publisher_family_labels(members)
        source_count = len({int(_value(row, "publisher_id")) for row in members})
        coverage_confidence = _coverage_confidence(
            article_count=len(article_ids),
            source_count=source_count,
            family_count=len(families),
        )
        alignment_subject = _alignment_subject(subject, members)
        evidence_ids = tuple(f"article:{article_id}" for article_id in article_ids) + tuple(
            f"signal:{signal_id}" for signal_id in signal_ids
        ) + tuple(f"story:{story_id}" for story_id in story_ids) + tuple(
            f"entity:{entity_id}" for entity_id in entity_ids
        )
        observations.append(make_observation(
            country_code=country,
            contour=Contour.MEDIA,
            subject_key=subject,
            direction=direction,
            metric=metric,
            observed_at=day,
            window=window,
            value=len(article_ids) / denominator if denominator else None,
            publisher_family_count=len(families),
            source_count=source_count,
            coverage_confidence=coverage_confidence if denominator else 0.0,
            article_id=article_ids[0] if article_ids else None,
            story_id=story_ids[0] if story_ids else None,
            signal_id=signal_ids[0] if signal_ids else None,
            canonical_entity_id=canonical_entities[0] if canonical_entities else None,
            baseline={
                "national_indexed_article_count": denominator,
                "coverage_components": {
                    "article_count": len(article_ids),
                    "source_count": source_count,
                    "publisher_family_count": len(families),
                },
            },
            evidence={
                "article_ids": article_ids,
                "story_ids": story_ids,
                "signal_ids": signal_ids,
                "entity_ids": entity_ids,
                "publisher_families": families,
                "alignment_subject": alignment_subject,
                "alignment_direction": _alignment_direction(direction),
            },
            evidence_ids=evidence_ids,
        ))
    return observations
