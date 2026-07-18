"""Deterministic normalization of verified media coverage into Radar points."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timezone
from typing import Any

from sqlalchemy import text

from src.engine.health import _publisher_family_identities

from .repository import make_observation
from .types import Contour, Observation, ObservationWindow


_MEDIA_ROWS = text("""
    SELECT a.id AS article_id, a.published_at, a.is_duplicate,
           source.country_code, source.id AS publisher_id,
           source.name AS publisher_name, source.url AS publisher_url,
           publisher.config AS publisher_config,
           story_link.story_id, analysis.event_key, analysis.sentiment,
           ARRAY(SELECT DISTINCT event.event_key FROM story_events event
                 WHERE event.story_id = story_link.story_id) AS story_event_keys,
           ARRAY(SELECT DISTINCT entity.entity_id FROM story_entities entity
                 WHERE entity.story_id = story_link.story_id) AS entity_ids,
           ARRAY(SELECT DISTINCT evidence.id FROM signal_evidence evidence
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
      AND (analysis.is_relevant IS DISTINCT FROM FALSE)
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


def _publisher_family(row: Any) -> str:
    source = {
        "url": _value(row, "publisher_url"),
        "config": _value(row, "publisher_config", {}) or {},
    }
    identities = _publisher_family_identities(source)
    if identities:
        return min(identities)
    return f"publisher:{_value(row, 'publisher_id')}"


def _subject(row: Any) -> str:
    story_id = _value(row, "story_id")
    if story_id is not None:
        return f"story:{int(story_id)}"
    events = tuple(value for value in (_value(row, "story_event_keys", ()) or ()) if value)
    event_key = events[0] if events else _value(row, "event_key")
    if event_key:
        return f"event:{str(event_key)}"
    return "media:coverage"


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
        grouped[country, _subject(row), _direction(_value(row, "sentiment")), "attention_share", day].append(row)

    observations: list[Observation] = []
    for (country, subject, direction, metric, day), members in sorted(grouped.items()):
        article_ids = tuple(sorted({int(_value(row, "article_id")) for row in members}))
        story_ids = tuple(sorted({int(_value(row, "story_id")) for row in members if _value(row, "story_id") is not None}))
        signal_ids = tuple(sorted({int(item) for row in members for item in (_value(row, "signal_ids", ()) or ())}))
        entity_ids = tuple(sorted({str(item) for row in members for item in (_value(row, "entity_ids", ()) or ())}))
        denominator = len(national_coverage[country, day])
        families = {_publisher_family(row) for row in members}
        evidence_ids = tuple(f"article:{article_id}" for article_id in article_ids) + tuple(
            f"signal:{signal_id}" for signal_id in signal_ids
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
            source_count=len({int(_value(row, "publisher_id")) for row in members}),
            coverage_confidence=1.0 if denominator else 0.0,
            article_id=article_ids[0] if article_ids else None,
            story_id=story_ids[0] if story_ids else None,
            signal_id=signal_ids[0] if signal_ids else None,
            baseline={"national_indexed_article_count": denominator},
            evidence={
                "article_ids": article_ids,
                "story_ids": story_ids,
                "signal_ids": signal_ids,
                "entity_ids": entity_ids,
                "publisher_families": tuple(sorted(families)),
            },
            evidence_ids=evidence_ids,
        ))
    return observations
