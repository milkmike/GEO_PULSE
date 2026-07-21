"""Replay-safe Radar orchestration and auditable trend persistence."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from statistics import fmean
from typing import Any, Iterable
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text

from .actions import build_action_observations
from .baseline import calculate_baseline, clamp01, refine_t0
from .episodes import EpisodeInterval, episode_distance, match_episode_intervals
from .grouping import CountryWave, MetaTrend, assign_country_waves, assign_meta_trends
from .lifecycle import decide_state
from .media import _publisher_family_labels, build_media_observations
from .repository import upsert_observations
from .types import (
    BaselineResult,
    Contour,
    DailyPoint,
    Observation,
    ObservationWindow,
    TrendDecision,
    TrendMetrics,
    TrendState,
    TrendTimeline,
)


DETECTOR_VERSION = "radar-wave-1"

_PRIOR_WAVES = text("""
/* radar_prior_waves */
SELECT trend.id, trend.wave_key, trend.country_code, trend.contour, trend.subject_key,
       trend.direction, trend.state, trend.first_observed_at, trend.detected_at,
       trend.confirmed_at,
       trend.t0_auto, trend.t0_effective,
       EXISTS (
         SELECT 1 FROM radar_t0_revisions revision
         WHERE revision.trend_id = trend.id
           AND revision.revision_kind = 'analyst'
       ) AS has_analyst_t0_override,
       (SELECT max(observation.observed_at) FROM radar_trend_evidence evidence
        JOIN radar_observations observation ON observation.id = evidence.observation_id
        WHERE evidence.trend_id = trend.id) AS last_observed_at
FROM radar_trends trend
WHERE trend.scope = 'country' AND trend.detector_version = :detector_version
  AND trend.first_observed_at < :as_of
""")

_PRIOR_WAVES_FOR_IDENTITIES = text("""
/* radar_prior_waves_incremental */
WITH active_identity AS (
  SELECT *
  FROM UNNEST(
    CAST(:identity_countries AS text[]),
    CAST(:identity_contours AS text[]),
    CAST(:identity_subjects AS text[]),
    CAST(:identity_directions AS text[])
  ) AS identity(country_code, contour, subject_key, direction)
)
SELECT trend.id, trend.wave_key, trend.country_code, trend.contour, trend.subject_key,
       trend.direction, trend.state, trend.first_observed_at, trend.detected_at,
       trend.confirmed_at,
       trend.t0_auto, trend.t0_effective,
       EXISTS (
         SELECT 1 FROM radar_t0_revisions revision
         WHERE revision.trend_id = trend.id
           AND revision.revision_kind = 'analyst'
       ) AS has_analyst_t0_override,
       (SELECT max(observation.observed_at) FROM radar_trend_evidence evidence
        JOIN radar_observations observation ON observation.id = evidence.observation_id
        WHERE evidence.trend_id = trend.id) AS last_observed_at
FROM radar_trends trend
JOIN active_identity identity
  ON identity.country_code = trend.country_code
 AND identity.contour = trend.contour::text
 AND identity.subject_key = trend.subject_key
 AND identity.direction = trend.direction
WHERE trend.scope = 'country' AND trend.detector_version = :detector_version
  AND trend.first_observed_at < :as_of
""")

_STORY_ANCHORS = text("""
/* radar_story_anchors */
SELECT story.id, event.event_key, story.meta->>'subject_key' AS subject_key
FROM stories story
LEFT JOIN story_events event ON event.story_id = story.id
WHERE story.last_seen < :as_of
""")

_HISTORY = text("""
/* radar_observation_history */
SELECT public_id, input_hash, country_code, contour, subject_key, direction, metric,
       observed_at, value, publisher_family_count, source_count,
       coverage_confidence, authority, article_id, story_id, signal_id,
       canonical_entity_id, baseline, evidence
FROM radar_observations
WHERE observed_at >= :history_start AND observed_at < :as_of
""")

_HISTORY_FOR_IDENTITIES = text("""
/* radar_observation_history_incremental */
WITH active_identity AS (
  SELECT *
  FROM UNNEST(
    CAST(:identity_countries AS text[]),
    CAST(:identity_contours AS text[]),
    CAST(:identity_subjects AS text[]),
    CAST(:identity_directions AS text[])
  ) AS identity(country_code, contour, subject_key, direction)
)
SELECT observation.public_id, observation.input_hash,
       observation.country_code, observation.contour,
       observation.subject_key, observation.direction, observation.metric,
       observation.observed_at, observation.value,
       observation.publisher_family_count, observation.source_count,
       observation.coverage_confidence, observation.authority,
       observation.article_id, observation.story_id, observation.signal_id,
       observation.canonical_entity_id, observation.baseline,
       observation.evidence
FROM radar_observations observation
JOIN active_identity identity
  ON identity.country_code = observation.country_code
 AND identity.contour = observation.contour::text
 AND identity.subject_key = observation.subject_key
 AND identity.direction = observation.direction
WHERE observation.observed_at >= :history_start
  AND observation.observed_at < :as_of
""")

_MEDIA_COLLECTION_HEALTH = text("""
/* radar_media_collection_health */
SELECT article.id AS article_id,
       fact.country_code,
       (date_trunc('day', article.published_at AT TIME ZONE 'UTC')
         AT TIME ZONE 'UTC') AS healthy_day,
       publisher.id AS publisher_id,
       publisher.url AS publisher_url,
       publisher.config AS publisher_config
FROM articles article
JOIN article_country_facts fact ON fact.article_id = article.id
JOIN sources publisher ON publisher.id = fact.id
WHERE article.is_duplicate = FALSE
  AND article.is_backfill = FALSE
  AND article.collected_at <= article.published_at + INTERVAL '24 hours'
  AND article.collected_at < :window_end
  AND article.published_at >= :window_start
  AND article.published_at < :window_end
""")

_PROTECTED_COUNTS = text("""
/* radar_protected_counts */
SELECT
 (SELECT count(*) FROM articles) AS articles,
 (SELECT count(*) FROM stories) AS stories,
 (SELECT count(*) FROM signals) AS signals,
 (SELECT count(*) FROM temperature) AS temperature,
 (SELECT count(*) FROM radar_observations) AS radar_observations,
 (SELECT count(*) FROM radar_trends) AS radar_trends,
 (SELECT count(*) FROM radar_trend_members) AS radar_trend_members,
 (SELECT count(*) FROM radar_trend_evidence) AS radar_trend_evidence,
 (SELECT count(*) FROM radar_state_events) AS radar_state_events,
 (SELECT count(*) FROM radar_t0_revisions) AS radar_t0_revisions,
 (SELECT count(*) FROM radar_contour_links) AS radar_contour_links
""")


@dataclass(frozen=True, slots=True)
class RadarCycleReport:
    as_of: datetime
    shadow: bool
    inserted_observations: int
    updated_trends: int
    country_waves: tuple[CountryWave, ...]
    meta_trends: tuple[MetaTrend, ...]
    trend_id: int | None
    state_counts: dict[str, int]
    collector_suppressed: int
    country_coverage: dict[str, float]
    t0_distribution: dict[str, int]
    evidence_completeness: dict[str, int]
    protected_row_counts: dict[str, dict[str, int]]
    lookback_days: int
    observation_count: int
    country_state_counts: dict[str, int]
    meta_state_counts: dict[str, int]
    evidence_validity: dict[str, int | float]
    confirmed_below_coverage_gate: int
    t0_sanity: dict[str, int]
    contour_completeness: dict[str, int | float | None]

    def json_report(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "shadow": self.shadow,
            "detector_version": DETECTOR_VERSION,
            "lookback_days": self.lookback_days,
            "observation_count": self.observation_count,
            "country_trend_count": len(self.country_waves),
            "meta_trend_count": len(self.meta_trends),
            "country_state_counts": self.country_state_counts,
            "meta_state_counts": self.meta_state_counts,
            "evidence_validity": self.evidence_validity,
            "confirmed_below_coverage_gate": self.confirmed_below_coverage_gate,
            "t0_sanity": self.t0_sanity,
            "contour_completeness": self.contour_completeness,
            "inserted_observations": self.inserted_observations,
            "updated_trends": self.updated_trends,
            "candidates": self.state_counts.get("candidate", 0),
            "emerging": self.state_counts.get("emerging", 0),
            "confirmed": self.state_counts.get("confirmed", 0),
            "rejected": self.state_counts.get("rejected", 0),
            "collector_suppressed": self.collector_suppressed,
            "country_coverage": self.country_coverage,
            "t0_distribution": self.t0_distribution,
            "evidence_completeness": self.evidence_completeness,
            "protected_row_counts": self.protected_row_counts,
            "country_wave_count": len(self.country_waves),
            "meta_trend_count": len(self.meta_trends),
        }


@dataclass(frozen=True, slots=True)
class _PreviousMetaContext:
    state: TrendState
    t0_auto: datetime | None
    t0_effective: datetime | None
    has_analyst_t0_override: bool


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    return value.astimezone(timezone.utc)


def _row_value(row: Any, name: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(name, default)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping.get(name, default)
    return getattr(row, name, default)


def _store(session) -> dict[str, list[dict[str, Any]]] | None:
    store = getattr(session, "radar_store", None)
    return store if isinstance(store, dict) else None


def _waves_from_rows(rows: Iterable[Any]) -> tuple[CountryWave, ...]:
    waves: list[CountryWave] = []
    for row in rows:
        first = _row_value(row, "first_observed_at")
        if first is None:
            continue
        waves.append(CountryWave(
            country_code=_row_value(row, "country_code"), contour=_row_value(row, "contour"),
            subject_key=_row_value(row, "subject_key"), direction=_row_value(row, "direction"),
            observations=(), first_observed_at=first, t0_auto=_row_value(row, "t0_auto"),
            t0_effective=_row_value(row, "t0_effective"), wave_key=_row_value(row, "wave_key", ""),
            last_observed_at=_row_value(row, "last_observed_at") or first,
            state=_row_value(row, "state", TrendState.CANDIDATE),
            detected_at=_row_value(row, "detected_at"),
            confirmed_at=_row_value(row, "confirmed_at"),
            has_analyst_t0_override=bool(
                _row_value(row, "has_analyst_t0_override", False)
            ),
        ))
    return tuple(waves)


def _previous_waves(session, as_of: datetime) -> tuple[CountryWave, ...]:
    if _store(session) is not None:
        return ()
    rows = session.execute(_PRIOR_WAVES, {
        "detector_version": DETECTOR_VERSION,
        "as_of": as_of,
    }).fetchall()
    return _waves_from_rows(rows)


def _identity_params(
    observations: Iterable[Observation],
) -> dict[str, list[str]]:
    identities = sorted({
        (
            observation.country_code,
            observation.contour.value,
            observation.subject_key,
            observation.direction,
        )
        for observation in observations
    })
    return {
        "identity_countries": [identity[0] for identity in identities],
        "identity_contours": [identity[1] for identity in identities],
        "identity_subjects": [identity[2] for identity in identities],
        "identity_directions": [identity[3] for identity in identities],
    }


def _previous_waves_for_identities(
    session: Any,
    as_of: datetime,
    observations: Iterable[Observation],
) -> tuple[CountryWave, ...]:
    if _store(session) is not None:
        return ()
    params = _identity_params(observations)
    if not params["identity_countries"]:
        return ()
    rows = session.execute(_PRIOR_WAVES_FOR_IDENTITIES, {
        **params,
        "detector_version": DETECTOR_VERSION,
        "as_of": as_of,
    }).fetchall()
    return _waves_from_rows(rows)


def _story_anchors(session, as_of: datetime) -> tuple[object, ...]:
    if _store(session) is not None:
        return ()
    return tuple(session.execute(_STORY_ANCHORS, {"as_of": as_of}).fetchall())


def _observations_from_rows(rows: Iterable[Any]) -> tuple[Observation, ...]:
    points: list[Observation] = []
    for row in rows:
        try:
            points.append(Observation(
                public_id=UUID(str(_row_value(row, "public_id"))), input_hash=str(_row_value(row, "input_hash")),
                country_code=str(_row_value(row, "country_code")), contour=Contour(_row_value(row, "contour")),
                subject_key=str(_row_value(row, "subject_key")), direction=str(_row_value(row, "direction")),
                metric=str(_row_value(row, "metric")), observed_at=_row_value(row, "observed_at"), window=None,
                value=_row_value(row, "value"), publisher_family_count=int(_row_value(row, "publisher_family_count", 0)),
                source_count=int(_row_value(row, "source_count", 0)), coverage_confidence=float(_row_value(row, "coverage_confidence", 0)),
                authority=_row_value(row, "authority"), article_id=_row_value(row, "article_id"), story_id=_row_value(row, "story_id"),
                signal_id=_row_value(row, "signal_id"), canonical_entity_id=_row_value(row, "canonical_entity_id"),
                baseline=_row_value(row, "baseline", {}) or {}, evidence=_row_value(row, "evidence", {}) or {},
            ))
        except (TypeError, ValueError):
            continue
    return tuple(points)


def _history(session, as_of: datetime, days: int) -> tuple[Observation, ...]:
    if _store(session) is not None:
        return ()
    rows = session.execute(_HISTORY, {
        "history_start": as_of - timedelta(days=days + 14),
        "as_of": as_of,
    }).fetchall()
    return _observations_from_rows(rows)


def _history_for_identities(
    session: Any,
    as_of: datetime,
    days: int,
    observations: Iterable[Observation],
) -> tuple[Observation, ...]:
    if _store(session) is not None:
        return ()
    params = _identity_params(observations)
    if not params["identity_countries"]:
        return ()
    rows = session.execute(_HISTORY_FOR_IDENTITIES, {
        **params,
        "history_start": as_of - timedelta(days=days + 14),
        "as_of": as_of,
    }).fetchall()
    return _observations_from_rows(rows)


def _media_collection_health(
    session, window: ObservationWindow,
) -> dict[str, set[int]]:
    """Replay-safe national indexing health, independent of Russia relevance.

    A non-empty day is not automatically healthy: it must retain at least 75%
    of the country's first-60-day upper-quartile source and publisher-family
    breadth.  This deliberately freezes lifecycle cooling when collection is
    partial.  Fetch status is not used because only the latest status is stored
    today and applying it retrospectively would leak future state into replay.
    """

    if _store(session) is not None:
        return {}
    rows = session.execute(_MEDIA_COLLECTION_HEALTH, {
        "window_start": window.start,
        "window_end": window.end,
    }).fetchall()
    grouped: dict[tuple[str, int], list[Any]] = defaultdict(list)
    for row in rows:
        country = str(_row_value(row, "country_code", "")).upper()
        healthy_day = _row_value(row, "healthy_day")
        if len(country) != 2 or not isinstance(healthy_day, datetime):
            continue
        index = _bucket_index(healthy_day, window.end)
        publisher_id = _row_value(row, "publisher_id")
        if index is not None and _positive_int(publisher_id):
            grouped[country, index].append(row)

    breadth: dict[tuple[str, int], tuple[int, int]] = {}
    for key, members in grouped.items():
        source_count = len({int(_row_value(row, "publisher_id")) for row in members})
        family_count = len(_publisher_family_labels(members))
        breadth[key] = source_count, family_count

    def upper_quartile(values: list[int]) -> int | None:
        if not values:
            return None
        ordered = sorted(values)
        return ordered[max(0, (3 * len(ordered) + 3) // 4 - 1)]

    countries = {country for country, _index in breadth}
    health: dict[str, set[int]] = defaultdict(set)
    for country in countries:
        reference = [
            counts for (candidate, index), counts in breadth.items()
            if candidate == country and index < 60
        ]
        reference_sources = upper_quartile([counts[0] for counts in reference])
        reference_families = upper_quartile([counts[1] for counts in reference])
        if reference_sources is None or reference_families is None:
            continue
        for (candidate, index), (source_count, family_count) in breadth.items():
            if candidate != country:
                continue
            if (
                source_count >= 2
                and family_count >= 2
                and source_count * 4 >= reference_sources * 3
                and family_count * 4 >= reference_families * 3
            ):
                health[country].add(index)
    return dict(health)


def _protected_counts(session) -> dict[str, int]:
    store = _store(session)
    if store is not None:
        return {
            "radar_observations": len(store["observations"]), "radar_trends": len(store["trends"]),
            "radar_state_events": len(store["events"]), "radar_t0_revisions": len(store["revisions"]),
        }
    row = session.execute(_PROTECTED_COUNTS).first()
    return {name: int(_row_value(row, name, 0) or 0) for name in (
        "articles", "stories", "signals", "temperature", "radar_observations", "radar_trends",
        "radar_trend_members", "radar_trend_evidence", "radar_state_events", "radar_t0_revisions", "radar_contour_links",
    )}


def _count_report(before: dict[str, int], after: dict[str, int]) -> dict[str, dict[str, int]]:
    return {name: {"before": before.get(name, 0), "after": after.get(name, 0), "delta": after.get(name, 0) - before.get(name, 0)} for name in sorted(set(before) | set(after))}


def _logical_observation_key(observation: Observation) -> tuple[object, ...]:
    """Identity stable across representative-root normalization corrections."""

    evidence = observation.evidence
    if observation.contour is Contour.MEDIA:
        source_identity: tuple[object, ...] = tuple(sorted(
            int(value) for value in evidence.get("article_ids", ())
        ))
        if not source_identity:
            source_identity = tuple(sorted(
                int(value) for value in evidence.get("story_ids", ())
            ))
    else:
        source_id = evidence.get("source_id") or evidence.get("source_record_id")
        source_identity = (str(source_id),) if source_id is not None else ()
    if not source_identity:
        # Without a stable source record, retaining the immutable hash avoids
        # weakening legitimate independent observations.
        source_identity = ("input_hash", observation.input_hash)
    return (
        observation.contour.value, observation.country_code,
        observation.subject_key, observation.direction, observation.metric,
        observation.observed_at, source_identity,
    )


def _observation_richness(observation: Observation) -> tuple[int, int]:
    roots = sum(value is not None for value in (
        observation.article_id, observation.story_id, observation.signal_id,
        observation.canonical_entity_id,
    ))
    evidence_roots = sum(len(tuple(observation.evidence.get(key, ()))) for key in (
        "article_ids", "story_ids", "signal_ids", "entity_ids",
    ))
    return roots, evidence_roots


def _prefer_logical_observations(
    history: Iterable[Observation],
    generated: Iterable[Observation],
) -> list[Observation]:
    selected: dict[tuple[object, ...], tuple[Observation, tuple[int, int, int]]] = {}
    for is_generated, observations in ((False, history), (True, generated)):
        for observation in observations:
            key = _logical_observation_key(observation)
            quality = (int(is_generated), *_observation_richness(observation))
            current = selected.get(key)
            if current is None or quality > current[1]:
                selected[key] = observation, quality
    return sorted((point for point, _quality in selected.values()), key=lambda point: (
        point.observed_at, point.country_code, point.contour.value,
        point.subject_key, point.direction, point.metric, point.input_hash,
    ))


def _state_for(wave: CountryWave, as_of: datetime):
    if wave.baseline is not None:
        return TrendDecision(
            state=wave.state,
            reason=wave.lifecycle_reason,
            confirmation_allowed=wave.baseline.confirmation_allowed,
            timeline=TrendTimeline(
                first_observed_at=wave.first_observed_at,
                detected_at=wave.detected_at,
                confirmed_at=wave.confirmed_at,
                t0_auto=wave.t0_auto,
                t0_effective=wave.t0_effective,
            ),
        )
    observations = wave.observations
    authority = any(point.authority in {"registry", "formal"} for point in observations)
    families = max((point.publisher_family_count for point in observations), default=0)
    coverage = min((point.coverage_confidence for point in observations), default=0.0)
    persistent = len({point.observed_at.date() for point in observations}) >= 2
    metrics = TrendMetrics(
        contour=wave.contour,
        persistent=persistent,
        publisher_family_count=families,
        coverage=coverage,
        signal_strength=min(1.0, max((abs(float(point.value or 0)) for point in observations), default=0.0)),
        authoritative=authority,
        authority="registry" if authority else None,
        authoritative_source_count=sum(point.authority in {"registry", "formal"} for point in observations),
        as_of=as_of,
        timeline=TrendTimeline(
            first_observed_at=wave.first_observed_at,
            detected_at=wave.detected_at,
            confirmed_at=wave.confirmed_at,
            t0_auto=wave.t0_auto,
            t0_effective=wave.t0_effective,
        ),
    )
    return decide_state(metrics, wave.state)


def _bucket_index(observed_at: datetime, as_of: datetime, days: int = 90) -> int | None:
    window_start = as_of - timedelta(days=days)
    normalized = _utc(observed_at)
    if not window_start <= normalized < as_of:
        return None
    return int((normalized - window_start) // timedelta(days=1))


def _healthy_collection_buckets(
    observations: Iterable[Observation], as_of: datetime,
) -> dict[tuple[str, Contour], set[int]]:
    """Collection evidence is scoped by country/contour, never inferred globally."""

    healthy: dict[tuple[str, Contour], set[int]] = defaultdict(set)
    for observation in observations:
        index = _bucket_index(observation.observed_at, as_of)
        if index is not None and observation.evidence.get("collection_healthy") is True:
            healthy[(observation.country_code, observation.contour)].add(index)
    return healthy


def _daily_points_for_wave(
    wave: CountryWave,
    as_of: datetime,
    healthy_buckets: set[int],
) -> tuple[DailyPoint, ...]:
    """Build 90 exact UTC buckets; healthy silence is zero, missing stays absent."""

    window_start = as_of - timedelta(days=90)
    grouped: dict[int, list[Observation]] = defaultdict(list)
    for observation in wave.observations:
        index = _bucket_index(observation.observed_at, as_of)
        if index is not None:
            grouped[index].append(observation)

    active_indices = sorted(grouped)
    points: list[DailyPoint] = []
    for index in sorted(healthy_buckets | set(grouped)):
        members = grouped.get(index, ())
        at = window_start + timedelta(days=index)
        if not members:
            points.append(DailyPoint(
                at=at,
                volume=0.0,
                attention=0.0,
                semantic_signal=0.0,
                source_independence=0.0,
                persistence=0.0,
            ))
            continue
        if wave.contour is Contour.MEDIA:
            volumes = [
                float(len(point.article_ids) or point.source_count or 1)
                for point in members
            ]
            independence = max(
                min(1.0, point.publisher_family_count / 2.0)
                for point in members
            )
        else:
            volumes = [abs(float(point.value or 0.0)) for point in members]
            independence = 1.0 if any(
                point.authority in {"registry", "formal"} for point in members
            ) else min(1.0, sum(point.source_count for point in members) / 2.0)
        recent_active = sum(index - 6 <= candidate <= index for candidate in active_indices)
        points.append(DailyPoint(
            at=at,
            volume=sum(volumes),
            attention=fmean(abs(float(point.value or 0.0)) for point in members),
            semantic_signal=fmean(
                min(1.0, abs(float(point.value or 0.0))) for point in members
            ),
            source_independence=independence,
            persistence=min(1.0, recent_active / 2.0),
        ))
    return tuple(points)


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _valid_evidence_root(observation: Observation) -> bool:
    """Require a typed, source-resolvable root rather than arbitrary JSON."""

    if any(_positive_int(value) for value in (
        observation.article_id,
        observation.story_id,
        observation.signal_id,
    )):
        return True
    if isinstance(observation.canonical_entity_id, UUID):
        return True
    for key in ("article_ids", "story_ids", "signal_ids"):
        values = observation.evidence.get(key)
        if isinstance(values, (list, tuple, set, frozenset)) and any(
            _positive_int(value) for value in values
        ):
            return True
    source_id = observation.evidence.get("source_id")
    return isinstance(source_id, str) and bool(source_id.strip())


def _evidence_validity(observations: Iterable[Observation]) -> dict[str, int | float]:
    points = tuple(observations)
    valid = sum(_valid_evidence_root(point) for point in points)
    total = len(points)
    return {
        "total": total,
        "valid": valid,
        "invalid": total - valid,
        "ratio": valid / total if total else 0.0,
    }


def _effective_coverage(observations: tuple[Observation, ...]) -> float:
    if not observations:
        return 0.0
    raw = fmean(point.coverage_confidence for point in observations)
    valid_ratio = sum(_valid_evidence_root(point) for point in observations) / len(observations)
    return min(raw, valid_ratio)


def _signal_strength(points: tuple[DailyPoint, ...], wave: CountryWave, as_of: datetime) -> float:
    recent_points = [
        point for point in points
        if point.at >= as_of - timedelta(days=7) and point.semantic_signal is not None
    ]
    return max(
        (point.semantic_signal or 0.0 for point in recent_points),
        default=max(
            (min(1.0, abs(float(point.value or 0.0))) for point in wave.observations),
            default=0.0,
        ),
    )


def _wave_metrics(
    wave: CountryWave,
    *,
    as_of: datetime,
    points: tuple[DailyPoint, ...],
    coverage: float,
    timeline: TrendTimeline,
    quiet_since: datetime | None = None,
    missing_collection_cycles: int = 0,
) -> TrendMetrics:
    observed_indices = {
        index for point in wave.observations
        if (index := _bucket_index(point.observed_at, as_of)) is not None
    }
    authority_count = sum(
        point.authority in {"registry", "formal"} for point in wave.observations
    )
    return TrendMetrics(
        contour=wave.contour,
        persistent=len(observed_indices) >= 2,
        publisher_family_count=max(
            (point.publisher_family_count for point in wave.observations),
            default=0,
        ),
        coverage=coverage,
        signal_strength=clamp01(_signal_strength(points, wave, as_of)),
        authoritative=authority_count > 0,
        authority="registry" if authority_count else None,
        authoritative_source_count=authority_count,
        as_of=as_of,
        quiet_since=quiet_since,
        missing_collection_cycles=missing_collection_cycles,
        timeline=timeline,
    )


def _historical_timeline(wave: CountryWave, as_of: datetime) -> tuple[datetime | None, datetime | None]:
    """Replay prefixes to recover the first detection without future leakage."""

    if wave.detected_at is not None:
        return wave.detected_at, wave.confirmed_at
    detected_at: datetime | None = None
    confirmed_at = wave.confirmed_at
    state = TrendState.CANDIDATE
    ordered_times = sorted({point.observed_at for point in wave.observations if point.observed_at < as_of})
    for observed_at in ordered_times:
        prefix = tuple(point for point in wave.observations if point.observed_at <= observed_at)
        prefix_wave = replace(
            wave,
            observations=prefix,
            last_observed_at=observed_at,
            state=state,
            detected_at=detected_at,
            confirmed_at=confirmed_at,
            baseline=None,
        )
        cutoff = min(as_of, observed_at + timedelta(microseconds=1))
        healthy = {
            index for point in prefix
            if (index := _bucket_index(point.observed_at, cutoff)) is not None
        }
        points = _daily_points_for_wave(prefix_wave, cutoff, healthy)
        coverage = _effective_coverage(prefix)
        decision = decide_state(
            _wave_metrics(
                prefix_wave,
                as_of=observed_at,
                points=points,
                coverage=coverage,
                timeline=TrendTimeline(
                    first_observed_at=wave.first_observed_at,
                    detected_at=detected_at,
                    confirmed_at=confirmed_at,
                    t0_auto=wave.t0_auto,
                    t0_effective=wave.t0_effective,
                ),
            ),
            state,
        )
        state = decision.state
        if state in (TrendState.EMERGING, TrendState.CONFIRMED) and detected_at is None:
            detected_at = observed_at
        if state is TrendState.CONFIRMED and confirmed_at is None:
            confirmed_at = observed_at
    return detected_at, confirmed_at


def _baseline_velocity(baseline: BaselineResult) -> float:
    deltas = []
    for recent, historical in (
        (baseline.acceleration_volume, baseline.baseline_volume),
        (baseline.acceleration_attention, baseline.baseline_attention),
    ):
        if recent is not None and historical is not None:
            deltas.append((recent - historical) / max(1.0, abs(historical)))
    return fmean(deltas) if deltas else 0.0


def _analyze_wave(
    wave: CountryWave,
    as_of: datetime,
    healthy_buckets: set[int],
) -> CountryWave:
    points = _daily_points_for_wave(wave, as_of, healthy_buckets)
    coverage = _effective_coverage(wave.observations)
    baseline = calculate_baseline(points, as_of, coverage)
    observed_indices = sorted({
        index for point in wave.observations
        if (index := _bucket_index(point.observed_at, as_of)) is not None
    })
    last_index = observed_indices[-1] if observed_indices else (
        _bucket_index(wave.last_observed_at, as_of)
        if wave.last_observed_at is not None else None
    )
    trailing_indices = set(range((last_index + 1) if last_index is not None else 0, 90))
    missing_cycles = len(trailing_indices - healthy_buckets)
    quiet_since = wave.last_observed_at if (
        wave.last_observed_at is not None
        and any(index > (last_index if last_index is not None else -1) for index in healthy_buckets)
    ) else None
    detected_at, historical_confirmed_at = _historical_timeline(wave, as_of)
    timeline = TrendTimeline(
        first_observed_at=wave.first_observed_at,
        detected_at=detected_at,
        confirmed_at=historical_confirmed_at,
        t0_auto=wave.t0_auto,
        t0_effective=wave.t0_effective,
    )
    decision = decide_state(
        _wave_metrics(
            wave,
            as_of=as_of,
            points=points,
            coverage=baseline.coverage_confidence,
            timeline=timeline,
            quiet_since=quiet_since,
            missing_collection_cycles=missing_cycles,
        ),
        wave.state,
    )

    t0_auto = wave.t0_auto
    t0_status = "not_confirmed"
    if decision.state is TrendState.CONFIRMED and detected_at is not None:
        t0_result = refine_t0(points, as_of)
        t0_status = t0_result.status
        if t0_result.t0_auto is not None and t0_result.t0_auto <= detected_at:
            t0_auto = t0_result.t0_auto
        elif wave.contour is Contour.ACTION and wave.observations:
            t0_auto = min(point.observed_at for point in wave.observations)
            t0_status = "authoritative_event_time"
    state = decision.state
    confirmed_at = decision.timeline.confirmed_at
    lifecycle_reason = decision.reason
    if (
        wave.contour is Contour.MEDIA
        and wave.state in (TrendState.CANDIDATE, TrendState.EMERGING)
        and state is TrendState.CONFIRMED
        and t0_auto is None
    ):
        state = TrendState.EMERGING
        confirmed_at = wave.confirmed_at
        lifecycle_reason = "automatic_t0_unresolved"
    analyst_override = wave.has_analyst_t0_override or (
        wave.t0_effective is not None
        and wave.t0_auto is not None
        and wave.t0_effective != wave.t0_auto
    )
    t0_effective = wave.t0_effective if analyst_override else t0_auto
    return replace(
        wave,
        state=state,
        detected_at=decision.timeline.detected_at,
        confirmed_at=confirmed_at,
        t0_auto=t0_auto,
        t0_effective=t0_effective,
        baseline=baseline,
        lifecycle_reason=lifecycle_reason,
        t0_status=t0_status,
        velocity=_baseline_velocity(baseline),
    )


def _observation_signature(observation: Observation) -> dict[str, Any]:
    return {
        "input_hash": observation.input_hash,
        "country_code": observation.country_code,
        "contour": observation.contour.value,
        "subject_key": observation.subject_key,
        "direction": observation.direction,
        "observed_at": observation.observed_at,
        "evidence": dict(observation.evidence),
    }


def _persist_memory(session, observations: Iterable[Observation], waves: Iterable[CountryWave], metas: Iterable[MetaTrend], as_of: datetime) -> tuple[int, int | None, int]:
    store = _store(session)
    assert store is not None
    existing_hashes = {row["input_hash"] for row in store["observations"]}
    inserted = 0
    updated = 0
    for observation in observations:
        if observation.input_hash not in existing_hashes:
            store["observations"].append(_observation_signature(observation))
            existing_hashes.add(observation.input_hash)
            inserted += 1
    first_id: int | None = None
    for wave in waves:
        identity = (wave.contour.value, wave.country_code, wave.subject_key, wave.direction, DETECTOR_VERSION)
        trend = next((row for row in store["trends"] if row["identity"] == identity), None)
        state = _state_for(wave, as_of).state.value
        if trend is None:
            trend = {
                "id": len(store["trends"]) + 1, "identity": identity,
                "state": state, "t0_auto": wave.t0_auto, "t0_effective": wave.t0_auto,
            }
            store["trends"].append(trend)
            store["events"].append({"trend_id": trend["id"], "from_state": None, "to_state": state})
        else:
            updated += 1
            prior_state = trend["state"]
            trend["state"] = state
            trend["t0_auto"] = wave.t0_auto
            has_analyst_override = any(
                row["trend_id"] == trend["id"] and row["revision_kind"] == "analyst"
                for row in store["revisions"]
            )
            if not has_analyst_override:
                trend["t0_effective"] = wave.t0_auto
            if prior_state != state:
                store["events"].append({"trend_id": trend["id"], "from_state": prior_state, "to_state": state})
        first_id = first_id or trend["id"]
    # Meta rows preserve member-local lifecycle fields: the member itself is
    # never mutated because it participates in a cross-country grouping.
    for meta in metas:
        identity = ("meta", meta.subject_key, meta.meta_key, meta.direction, DETECTOR_VERSION)
        if not any(row["identity"] == identity for row in store["trends"]):
            store["trends"].append({
                "id": len(store["trends"]) + 1, "identity": identity,
                "state": _meta_state(meta).value,
                "t0_auto": meta.t0_auto, "t0_effective": meta.t0_auto,
            })
    return inserted, first_id, updated


_TREND_BY_IDENTITY = text("""
SELECT id, state, first_observed_at, confirmed_at, t0_auto, t0_effective, wave_key FROM radar_trends
WHERE scope = 'country' AND contour = :contour AND country_code = :country_code
  AND subject_key = :subject_key AND direction = :direction
  AND wave_key = :wave_key
  AND detector_version = :detector_version
""")

_INSERT_TREND = text("""
INSERT INTO radar_trends (
  public_id, scope, contour, country_code, subject_key, wave_key, title_ru, direction,
  alignment_subject, alignment_direction,
  state, confidence, coverage_confidence, velocity, first_observed_at,
  detected_at, confirmed_at, t0_auto, t0_effective, detector_version,
  baseline, explanation
) VALUES (
  :public_id, 'country', :contour, :country_code, :subject_key, :wave_key, :title_ru, :direction,
  :alignment_subject, :alignment_direction,
  :state, :confidence, :coverage_confidence, :velocity, :first_observed_at,
  :detected_at, :confirmed_at, :t0_auto, :t0_effective, :detector_version,
  CAST(:baseline AS jsonb), CAST(:explanation AS jsonb)
) RETURNING id
""")

_UPDATE_TREND = text("""
UPDATE radar_trends trend SET
  state = :state, confidence = :confidence, coverage_confidence = :coverage_confidence,
  velocity = :velocity,
  detected_at = COALESCE(trend.detected_at, :detected_at),
  confirmed_at = COALESCE(trend.confirmed_at, :confirmed_at), t0_auto = :t0_auto,
  t0_effective = CASE WHEN EXISTS (
    SELECT 1 FROM radar_t0_revisions revision
    WHERE revision.trend_id = trend.id AND revision.revision_kind = 'analyst'
  ) THEN trend.t0_effective ELSE :t0_effective END,
  baseline = CAST(:baseline AS jsonb), explanation = CAST(:explanation AS jsonb),
  updated_at = NOW()
WHERE trend.id = :id
""")

_INSERT_STATE_EVENT = text("""
INSERT INTO radar_state_events (
 public_id, trend_id, from_state, to_state, transition_reason, metrics, evidence, occurred_at
) VALUES (
 :public_id, :trend_id, :from_state, :to_state, :reason, CAST(:metrics AS jsonb), CAST(:evidence AS jsonb), :occurred_at
)
""")

_INSERT_AUTOMATIC_T0_REVISION = text("""
INSERT INTO radar_t0_revisions (
  public_id, trend_id, previous_t0, revised_t0, revision_kind, reason, evidence
) VALUES (
  :public_id, :trend_id, :previous_t0, :revised_t0, 'automatic',
  'recalculated', CAST(:evidence AS jsonb)
)
""")

_INSERT_OBSERVATION_EVIDENCE = text("""
INSERT INTO radar_trend_evidence (
  public_id, trend_id, observation_id, article_id, story_id, signal_id,
  canonical_entity_id, role, contribution, evidence
)
SELECT COALESCE((
         SELECT prior.public_id FROM radar_trend_evidence prior
         WHERE :relation_kind = 'base'
           AND prior.trend_id = :trend_id
           AND prior.observation_id = observation.id
         ORDER BY prior.id LIMIT 1
       ), :public_id),
       :trend_id, observation.id, observation.article_id,
       :story_id, :signal_id, observation.canonical_entity_id,
       :role, :contribution, CAST(:evidence AS jsonb)
FROM radar_observations observation
WHERE observation.input_hash = :input_hash
ON CONFLICT (public_id) DO UPDATE SET
  article_id = COALESCE(radar_trend_evidence.article_id, EXCLUDED.article_id),
  story_id = COALESCE(radar_trend_evidence.story_id, EXCLUDED.story_id),
  signal_id = COALESCE(radar_trend_evidence.signal_id, EXCLUDED.signal_id),
  canonical_entity_id = COALESCE(radar_trend_evidence.canonical_entity_id, EXCLUDED.canonical_entity_id),
  evidence = CASE WHEN :relation_kind = 'base'
    THEN radar_trend_evidence.evidence
    ELSE radar_trend_evidence.evidence || jsonb_build_object('_relation_only', true)
  END
""")


def _valid_relation_ids(value: object) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(sorted({item for item in value if isinstance(item, int) and not isinstance(item, bool) and item > 0}))


def _persist_observation_evidence(
    session,
    *,
    trend_id: int,
    observation: Observation,
    role: str,
    contribution: float,
) -> None:
    """Materialize every exact story/signal root without multiplying observations."""

    base_name = f"geo-pulse:radar-evidence:{trend_id}:{observation.input_hash}"
    relations = [("base", observation.story_id, observation.signal_id, base_name)]
    relations.extend(
        ("story", story_id, observation.signal_id, f"{base_name}:story:{story_id}")
        for story_id in _valid_relation_ids(observation.evidence.get("story_ids"))
        if story_id != observation.story_id
    )
    relations.extend(
        ("signal", observation.story_id, signal_id, f"{base_name}:signal:{signal_id}")
        for signal_id in _valid_relation_ids(observation.evidence.get("signal_ids"))
        if signal_id != observation.signal_id
    )

    observation_evidence = dict(observation.evidence)
    for relation_kind, story_id, signal_id, public_name in relations:
        evidence = dict(observation_evidence)
        if relation_kind != "base":
            evidence["_relation_only"] = True
        session.execute(_INSERT_OBSERVATION_EVIDENCE, {
            "public_id": uuid5(NAMESPACE_URL, public_name),
            "trend_id": trend_id,
            "input_hash": observation.input_hash,
            "story_id": story_id,
            "signal_id": signal_id,
            "relation_kind": relation_kind,
            "role": role,
            "contribution": contribution,
            "evidence": json.dumps(evidence),
        })


_META_BY_IDENTITY = text("""
SELECT trend.id, trend.state, trend.confirmed_at, trend.t0_auto,
       trend.t0_effective, trend.meta_key,
       EXISTS (
         SELECT 1 FROM radar_t0_revisions revision
         WHERE revision.trend_id = trend.id
           AND revision.revision_kind = 'analyst'
           AND revision.created_at >= COALESCE((
             SELECT max(event.occurred_at)
             FROM radar_state_events event
             WHERE event.trend_id = trend.id
               AND event.from_state IN ('resolved', 'rejected')
               AND event.to_state IN ('emerging', 'confirmed')
           ), '-infinity'::timestamptz)
       ) AS has_analyst_t0_override
FROM radar_trends trend
WHERE scope = 'meta' AND subject_key = :subject_key AND direction = :direction
  AND meta_key = :meta_key
  AND detector_version = :detector_version
""")

_INSERT_META = text("""
INSERT INTO radar_trends (
  public_id, scope, subject_key, meta_key, title_ru, direction, state, confidence,
  coverage_confidence, velocity, first_observed_at, detected_at, confirmed_at,
  t0_auto, t0_effective, detector_version, baseline, explanation
) VALUES (
  :public_id, 'meta', :subject_key, :meta_key, :title_ru, :direction, :state, 1.0,
  :coverage_confidence, :velocity, :first_observed_at, :detected_at, :confirmed_at,
  :t0_auto, :t0_effective, :detector_version, CAST(:baseline AS jsonb), CAST(:explanation AS jsonb)
) RETURNING id
""")

_UPDATE_META = text("""
UPDATE radar_trends trend SET
  state = :state, confidence = :confidence, coverage_confidence = :coverage_confidence,
  velocity = :velocity,
  detected_at = CASE WHEN :reopening THEN :detected_at
                     ELSE COALESCE(trend.detected_at, :detected_at) END,
  confirmed_at = CASE WHEN :reopening THEN :confirmed_at
                      ELSE COALESCE(trend.confirmed_at, :confirmed_at) END,
  t0_auto = CASE WHEN :reopening THEN :t0_auto
                 ELSE COALESCE(:t0_auto, trend.t0_auto) END,
  t0_effective = CASE
    WHEN :reopening THEN :t0_effective
    WHEN EXISTS (
      SELECT 1 FROM radar_t0_revisions revision
      WHERE revision.trend_id = trend.id
        AND revision.revision_kind = 'analyst'
        AND revision.created_at >= COALESCE((
          SELECT max(event.occurred_at)
          FROM radar_state_events event
          WHERE event.trend_id = trend.id
            AND event.from_state IN ('resolved', 'rejected')
            AND event.to_state IN ('emerging', 'confirmed')
        ), '-infinity'::timestamptz)
    ) THEN trend.t0_effective
    ELSE COALESCE(:t0_effective, trend.t0_effective)
  END,
  baseline = CAST(:baseline AS jsonb), explanation = CAST(:explanation AS jsonb), updated_at = NOW()
WHERE trend.id = :id
""")

_INSERT_MEMBER = text("""
INSERT INTO radar_trend_members (public_id, meta_trend_id, country_trend_id, evidence)
VALUES (:public_id, :meta_trend_id, :country_trend_id, CAST(:evidence AS jsonb))
ON CONFLICT (meta_trend_id, country_trend_id) DO UPDATE SET
  left_at = NULL, evidence = EXCLUDED.evidence
""")

_CLOSE_ACTIVE_MEMBERS = text("""
UPDATE radar_trend_members member
SET left_at = :as_of
FROM radar_trends meta
WHERE meta.id = member.meta_trend_id
  AND meta.detector_version = :detector_version
  AND member.left_at IS NULL
""")

_CLOSE_STALE_META_MEMBERS = text("""
UPDATE radar_trend_members
SET left_at = :as_of
WHERE meta_trend_id = :meta_id
  AND left_at IS NULL
  AND NOT (country_trend_id = ANY(CAST(:active_country_ids AS bigint[])))
""")

_UPSERT_CONTOUR_LINK = text("""
INSERT INTO radar_contour_links (public_id, media_trend_id, action_trend_id, status, evidence, evaluated_at)
VALUES (:public_id, :media_trend_id, :action_trend_id, :status, CAST(:evidence AS jsonb), :evaluated_at)
ON CONFLICT (media_trend_id, action_trend_id) DO UPDATE SET
  status = EXCLUDED.status, evidence = EXCLUDED.evidence,
  evaluated_at = EXCLUDED.evaluated_at, updated_at = NOW()
""")


def _persist_sql(
    session,
    observations: list[Observation],
    waves: Iterable[CountryWave],
    metas: Iterable[MetaTrend],
    as_of: datetime,
    *,
    incremental: bool = False,
) -> tuple[int, int | None, int]:
    inserted = upsert_observations(session, observations)
    first_id: int | None = None
    updated = 0
    wave_ids: dict[tuple[str, Contour, str, str, str], int] = {}
    for wave in waves:
        alignment_subject, alignment_direction = _alignment_identity(wave)
        params = {
            "contour": wave.contour.value, "country_code": wave.country_code,
            "subject_key": wave.subject_key, "direction": wave.direction,
            "alignment_subject": alignment_subject,
            "alignment_direction": alignment_direction,
            "wave_key": wave.wave_key,
            "detector_version": DETECTOR_VERSION,
        }
        existing = session.execute(_TREND_BY_IDENTITY, params).first()
        decision = _state_for(wave, as_of)
        state = decision.state.value
        if existing is None:
            public_id = uuid5(NAMESPACE_URL, "geo-pulse:radar-trend:" + "|".join(map(str, params.values())))
            result = session.execute(_INSERT_TREND, {
                **params, "public_id": public_id, "title_ru": _trend_title(wave.subject_key),
                "state": state, "confidence": _confidence(wave),
                "coverage_confidence": _wave_coverage(wave),
                "velocity": _velocity(wave), "first_observed_at": wave.first_observed_at,
                "detected_at": wave.detected_at, "confirmed_at": decision.timeline.confirmed_at,
                "t0_auto": wave.t0_auto, "t0_effective": wave.t0_effective,
                "baseline": json.dumps(_baseline_payload(wave)),
                "explanation": json.dumps(_explanation_payload(wave)),
            })
            trend_id = int(result.scalar())
            _insert_state_event(
                session, trend_id, None, state, wave.lifecycle_reason, as_of,
                metrics=_baseline_payload(wave), evidence=_explanation_payload(wave),
            )
        else:
            trend_id = int(_row_value(existing, "id"))
            previous_state = str(_row_value(existing, "state"))
            previous_t0_auto = _row_value(existing, "t0_auto")
            result = session.execute(_UPDATE_TREND, {
                "id": trend_id, "state": state, "t0_auto": wave.t0_auto,
                "t0_effective": wave.t0_effective, "confidence": _confidence(wave),
                "coverage_confidence": _wave_coverage(wave),
                "velocity": _velocity(wave), "detected_at": wave.detected_at,
                "confirmed_at": decision.timeline.confirmed_at,
                "baseline": json.dumps(_baseline_payload(wave)),
                "explanation": json.dumps(_explanation_payload(wave)),
            })
            updated += max(0, int(getattr(result, "rowcount", 0) or 0))
            if previous_t0_auto != wave.t0_auto and wave.t0_auto is not None:
                session.execute(_INSERT_AUTOMATIC_T0_REVISION, {
                    "public_id": uuid5(NAMESPACE_URL, f"geo-pulse:radar-t0-auto:{trend_id}:{wave.t0_auto.isoformat()}"),
                    "trend_id": trend_id, "previous_t0": previous_t0_auto,
                    "revised_t0": wave.t0_auto,
                    "evidence": json.dumps({"detector_version": DETECTOR_VERSION}),
                })
            if previous_state != state:
                _insert_state_event(
                    session, trend_id, previous_state, state,
                    wave.lifecycle_reason, as_of,
                    metrics=_baseline_payload(wave),
                    evidence=_explanation_payload(wave),
                )
        for index, observation in enumerate(wave.observations):
            _persist_observation_evidence(
                session,
                trend_id=trend_id,
                observation=observation,
                role="trigger" if index == 0 else "support",
                contribution=1.0 if index == 0 else 0.5,
            )
        wave_ids[(wave.country_code, wave.contour, wave.subject_key, wave.direction, wave.wave_key)] = trend_id
        first_id = first_id or trend_id
    _persist_meta_and_contours(
        session,
        metas,
        wave_ids,
        as_of,
        incremental=incremental,
    )
    return inserted, first_id, updated


def _persist_meta_and_contours(
    session, metas: Iterable[MetaTrend],
    wave_ids: dict[tuple[str, Contour, str, str, str], int], as_of: datetime,
    *,
    incremental: bool = False,
) -> None:
    """Persist meta membership and cross-contour alignment without state edits."""

    metas = tuple(metas)
    if not incremental:
        session.execute(_CLOSE_ACTIVE_MEMBERS, {
            "as_of": as_of,
            "detector_version": DETECTOR_VERSION,
        })
    for meta in metas:
        params = {"subject_key": meta.subject_key, "meta_key": meta.meta_key, "direction": meta.direction, "detector_version": DETECTOR_VERSION}
        existing = session.execute(_META_BY_IDENTITY, params).first()
        confirmed = tuple(wave for wave in meta.waves if wave.state is TrendState.CONFIRMED)
        prior_state = _row_value(existing, "state") if existing is not None else None
        state = _meta_state(meta, prior_state).value
        reopening = existing is not None and _is_meta_reopening(
            prior_state, state
        )
        meta_effective_t0 = _meta_effective_t0(meta)
        meta_detected_at, meta_confirmed_at = _meta_timeline(
            meta, TrendState(state)
        )
        coverage = min((_wave_coverage(wave) for wave in meta.waves), default=0.0)
        velocity = fmean(_velocity(wave) for wave in meta.waves)
        explanation = {
            "member_countries": sorted({wave.country_code for wave in meta.waves}),
            "confirmed_members": sorted({wave.country_code for wave in confirmed}),
            "velocity": velocity,
        }
        baseline = {"member_count": len(meta.waves), "confirmed_member_count": len(confirmed)}
        if existing is None:
            result = session.execute(_INSERT_META, {
                **params,
                "public_id": uuid5(NAMESPACE_URL, f"geo-pulse:radar-meta:{meta.meta_key}:{DETECTOR_VERSION}"),
                "title_ru": meta.subject_key, "state": state,
                "coverage_confidence": coverage,
                "velocity": velocity,
                "first_observed_at": min(wave.first_observed_at for wave in meta.waves),
                "detected_at": meta_detected_at,
                "confirmed_at": meta_confirmed_at,
                "t0_auto": meta.t0_auto, "t0_effective": meta_effective_t0,
                "baseline": json.dumps(baseline), "explanation": json.dumps(explanation),
            })
            meta_id = int(result.scalar())
            _insert_state_event(session, meta_id, None, state, "meta_assignment", as_of, metrics=baseline, evidence=explanation)
        else:
            meta_id = int(_row_value(existing, "id"))
            prior_state = str(prior_state)
            prior_t0 = _row_value(existing, "t0_auto")
            session.execute(_UPDATE_META, {
                "id": meta_id, "state": state, "confidence": coverage,
                "coverage_confidence": coverage,
                "velocity": velocity,
                "detected_at": meta_detected_at,
                "confirmed_at": meta_confirmed_at,
                "reopening": reopening,
                "t0_auto": meta.t0_auto, "t0_effective": meta_effective_t0,
                "baseline": json.dumps(baseline), "explanation": json.dumps(explanation),
            })
            if prior_state != state:
                _insert_state_event(session, meta_id, prior_state, state, "meta_recalculated", as_of, metrics=baseline, evidence=explanation)
            if prior_t0 != meta.t0_auto and meta.t0_auto is not None:
                session.execute(_INSERT_AUTOMATIC_T0_REVISION, {
                    "public_id": uuid5(NAMESPACE_URL, f"geo-pulse:radar-t0-auto:{meta_id}:{meta.t0_auto.isoformat()}"),
                    "trend_id": meta_id, "previous_t0": prior_t0, "revised_t0": meta.t0_auto,
                    "evidence": json.dumps({"detector_version": DETECTOR_VERSION, "scope": "meta"}),
                })
        active_country_ids: list[int] = []
        for wave in meta.waves:
            if wave.state in (TrendState.RESOLVED, TrendState.REJECTED):
                continue
            country_id = wave_ids[(wave.country_code, wave.contour, wave.subject_key, wave.direction, wave.wave_key)]
            active_country_ids.append(country_id)
            session.execute(_INSERT_MEMBER, {
                "public_id": uuid5(NAMESPACE_URL, f"geo-pulse:radar-member:{meta_id}:{country_id}"),
                "meta_trend_id": meta_id, "country_trend_id": country_id,
                "evidence": json.dumps({"subject_key": meta.subject_key, "direction": meta.direction}),
            })
        if incremental and active_country_ids:
            session.execute(_CLOSE_STALE_META_MEMBERS, {
                "meta_id": meta_id,
                "active_country_ids": active_country_ids,
                "as_of": as_of,
            })
    grouped: dict[tuple[str, str, str], list[tuple[int, CountryWave]]] = {}
    wave_by_identity = {
        (wave.country_code, wave.contour, wave.subject_key, wave.direction, wave.wave_key): wave
        for meta in metas for wave in meta.waves
    }
    for (country, contour, subject, direction, wave_key), trend_id in wave_ids.items():
        wave = wave_by_identity[(country, contour, subject, direction, wave_key)]
        alignment_subject, alignment_direction = _alignment_identity(wave)
        grouped.setdefault((country, alignment_subject, alignment_direction), []).append((
            trend_id, wave,
        ))
    for (country, subject, direction), members in grouped.items():
        media_members = sorted(
            ((trend_id, wave) for trend_id, wave in members if wave.contour is Contour.MEDIA),
            key=lambda item: item[1].first_observed_at,
        )
        remaining_actions = sorted(
            ((trend_id, wave) for trend_id, wave in members if wave.contour is Contour.ACTION),
            key=lambda item: item[1].first_observed_at,
        )
        for media_id, media_wave, action_id, action_wave in match_contour_episodes(
            media_members, remaining_actions,
        ):
            _persist_contour_link(session, country, subject, direction, media_id, media_wave, action_id, action_wave, as_of)


def match_contour_episodes(
    media_members: list[tuple[int, CountryWave]],
    action_members: list[tuple[int, CountryWave]],
) -> tuple[tuple[int, CountryWave, int, CountryWave], ...]:
    """Find the best order-preserving bounded episode alignment.

    Dynamic programming optimizes pair count first, aggregate gap second.  A
    lexical identity signature supplies a deterministic final tie-break rather
    than depending on database row order.
    """

    media_by_id = dict(media_members)
    action_by_id = dict(action_members)
    pairs = match_episode_intervals(
        [
            EpisodeInterval(
                trend_id, wave.first_observed_at,
                wave.last_observed_at or wave.first_observed_at, wave.wave_key,
            )
            for trend_id, wave in media_members
        ],
        [
            EpisodeInterval(
                trend_id, wave.first_observed_at,
                wave.last_observed_at or wave.first_observed_at, wave.wave_key,
            )
            for trend_id, wave in action_members
        ],
    )
    return tuple(
        (media_id, media_by_id[media_id], action_id, action_by_id[action_id])
        for media_id, action_id in pairs
    )


def _persist_contour_link(
    session, country: str, subject: str, direction: str, media_id: int,
    media_wave: CountryWave, action_id: int, action_wave: CountryWave, as_of: datetime,
) -> None:
    if media_wave.t0_auto is None or action_wave.t0_auto is None:
        status = "insufficient"
    elif abs(media_wave.t0_auto - action_wave.t0_auto) <= timedelta(days=14):
        status = "aligned"
    else:
        status = "divergent"
    session.execute(_UPSERT_CONTOUR_LINK, {
        "public_id": uuid5(NAMESPACE_URL, f"geo-pulse:radar-contour:{media_id}:{action_id}"),
        "media_trend_id": media_id, "action_trend_id": action_id,
        "status": status, "evidence": json.dumps({"country_code": country, "subject_key": subject, "direction": direction}),
        "evaluated_at": as_of,
    })


def _insert_state_event(
    session, trend_id: int, from_state: str | None, to_state: str, reason: str,
    as_of: datetime, *, metrics: dict[str, object] | None = None,
    evidence: dict[str, object] | None = None,
) -> None:
    public_id = uuid5(NAMESPACE_URL, f"geo-pulse:radar-state:{trend_id}:{from_state}:{to_state}:{as_of.isoformat()}")
    session.execute(_INSERT_STATE_EVENT, {
        "public_id": public_id, "trend_id": trend_id, "from_state": from_state,
        "to_state": to_state, "reason": reason, "metrics": json.dumps(metrics or {}),
        "evidence": json.dumps(evidence or {}), "occurred_at": as_of,
    })


def _coverage(observations: Iterable[Observation]) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for observation in observations:
        grouped.setdefault(observation.country_code, []).append(observation.coverage_confidence)
    return {country: sum(values) / len(values) for country, values in sorted(grouped.items())}


def _confidence(wave: CountryWave) -> float:
    return wave.baseline.confidence if wave.baseline is not None else _wave_coverage(wave)


def _wave_coverage(wave: CountryWave) -> float:
    if wave.baseline is not None:
        return wave.baseline.coverage_confidence
    return min((point.coverage_confidence for point in wave.observations), default=0.0)


def _velocity(wave: CountryWave) -> float:
    if wave.baseline is not None:
        return wave.velocity
    if len(wave.observations) < 2:
        return 0.0
    span = max(1.0, (wave.observations[-1].observed_at - wave.observations[0].observed_at).total_seconds() / 86400)
    return len(wave.observations) / span


def _baseline_payload(wave: CountryWave) -> dict[str, object]:
    if wave.baseline is not None:
        baseline = wave.baseline
        return {
            "window_days": baseline.window_days,
            "acceleration_days": baseline.acceleration_days,
            "valid_days": baseline.valid_days,
            "missing_days": baseline.missing_days,
            "baseline_volume": baseline.baseline_volume,
            "baseline_attention": baseline.baseline_attention,
            "acceleration_volume": baseline.acceleration_volume,
            "acceleration_attention": baseline.acceleration_attention,
            "coverage_confidence": baseline.coverage_confidence,
            "coverage_gate": baseline.coverage_gate.value,
            "confirmation_allowed": baseline.confirmation_allowed,
            "confidence": baseline.confidence,
            "online_candidate_flags": list(baseline.online_candidate_flags),
            "velocity": wave.velocity,
            "t0_status": wave.t0_status,
        }
    return {
        "observation_count": len(wave.observations),
        "first_observed_at": wave.first_observed_at.isoformat(),
        "last_observed_at": wave.last_observed_at.isoformat() if wave.last_observed_at else None,
        "wave_key": wave.wave_key,
        "coverage_confidence": _confidence(wave),
    }


def _explanation_payload(wave: CountryWave) -> dict[str, object]:
    return {
        "wave_key": wave.wave_key,
        "lifecycle_reason": wave.lifecycle_reason,
        "observation_hashes": [point.input_hash for point in wave.observations],
        "calculated_metrics": {
            "velocity": _velocity(wave),
            "coverage_confidence": _wave_coverage(wave),
            "baseline_valid_days": wave.baseline.valid_days if wave.baseline else None,
            "baseline_missing_days": wave.baseline.missing_days if wave.baseline else None,
            "t0_status": wave.t0_status,
        },
    }


def _trend_title(subject_key: str) -> str:
    return " · ".join(
        part.replace("_", " ").strip().capitalize()
        for part in subject_key.split(":") if part
    )


def _alignment_identity(wave: CountryWave) -> tuple[str, str]:
    subjects = [
        str(point.evidence["alignment_subject"])
        for point in wave.observations
        if point.evidence.get("alignment_subject")
    ]
    directions = [
        str(point.evidence["alignment_direction"])
        for point in wave.observations
        if point.evidence.get("alignment_direction")
    ]
    return (
        max(set(subjects), key=lambda value: (subjects.count(value), value))
        if subjects else wave.subject_key,
        max(set(directions), key=lambda value: (directions.count(value), value))
        if directions else wave.direction,
    )


def _state_distribution(states: Iterable[TrendState | str]) -> dict[str, int]:
    values = [TrendState(state).value for state in states]
    return {state.value: values.count(state.value) for state in TrendState}


def _meta_state(
    meta: MetaTrend, previous_state: TrendState | str | None = None,
) -> TrendState:
    states = tuple(wave.state for wave in meta.waves)
    confirmed_countries = {
        wave.country_code for wave in meta.waves
        if wave.state is TrendState.CONFIRMED
    }
    if len(confirmed_countries) >= 2:
        current = TrendState.CONFIRMED
    elif states and all(state is TrendState.RESOLVED for state in states):
        current = TrendState.RESOLVED
    elif states and all(state is TrendState.REJECTED for state in states):
        current = TrendState.REJECTED
    elif any(state is TrendState.COOLING for state in states):
        current = TrendState.COOLING
    elif any(state in (TrendState.EMERGING, TrendState.CONFIRMED) for state in states):
        current = TrendState.EMERGING
    else:
        current = TrendState.CANDIDATE

    if previous_state is None:
        return current
    previous = TrendState(previous_state)
    if (
        previous in (TrendState.RESOLVED, TrendState.REJECTED)
        and current not in (TrendState.EMERGING, TrendState.CONFIRMED)
    ):
        return previous
    if previous is TrendState.COOLING and current is not TrendState.RESOLVED:
        return TrendState.COOLING
    if previous is TrendState.CONFIRMED and current in (
        TrendState.CANDIDATE, TrendState.EMERGING, TrendState.REJECTED,
    ):
        return TrendState.COOLING
    if previous is TrendState.EMERGING and current is TrendState.CANDIDATE:
        return TrendState.EMERGING
    return current


def _is_meta_reopening(
    previous_state: TrendState | str | None,
    current_state: TrendState | str,
) -> bool:
    if previous_state is None:
        return False
    return (
        TrendState(previous_state) in (TrendState.RESOLVED, TrendState.REJECTED)
        and TrendState(current_state) in (TrendState.EMERGING, TrendState.CONFIRMED)
    )


def _meta_effective_t0(meta: MetaTrend) -> datetime | None:
    if meta.t0_auto is not None:
        return meta.t0_auto
    overrides = {
        wave.country_code: wave.t0_effective
        for wave in meta.waves
        if wave.state is TrendState.CONFIRMED
        and wave.has_analyst_t0_override
        and wave.t0_effective is not None
    }
    return min(overrides.values()) if len(overrides) >= 2 else None


def _meta_identity(meta: MetaTrend) -> tuple[str, str, str]:
    return meta.subject_key, meta.meta_key, meta.direction


def _previous_meta_contexts(
    session, metas: tuple[MetaTrend, ...],
) -> dict[tuple[str, str, str], _PreviousMetaContext]:
    store = _store(session)
    if store is not None:
        previous: dict[tuple[str, str, str], _PreviousMetaContext] = {}
        for row in store["trends"]:
            identity = row.get("identity", ())
            if len(identity) != 5 or identity[0] != "meta":
                continue
            has_override = any(
                revision.get("trend_id") == row.get("id")
                and revision.get("revision_kind") == "analyst"
                for revision in store["revisions"]
            )
            previous[(identity[1], identity[2], identity[3])] = _PreviousMetaContext(
                state=TrendState(row["state"]),
                t0_auto=row.get("t0_auto"),
                t0_effective=row.get("t0_effective"),
                has_analyst_t0_override=has_override,
            )
        return previous

    previous = {}
    for meta in metas:
        params = {
            "subject_key": meta.subject_key,
            "meta_key": meta.meta_key,
            "direction": meta.direction,
            "detector_version": DETECTOR_VERSION,
        }
        row = session.execute(_META_BY_IDENTITY, params).first()
        if row is not None:
            previous[_meta_identity(meta)] = _PreviousMetaContext(
                state=TrendState(_row_value(row, "state")),
                t0_auto=_row_value(row, "t0_auto"),
                t0_effective=_row_value(row, "t0_effective"),
                has_analyst_t0_override=bool(
                    _row_value(row, "has_analyst_t0_override", False)
                ),
            )
    return previous


def _meta_timeline(
    meta: MetaTrend, state: TrendState,
) -> tuple[datetime | None, datetime | None]:
    detected_at = min(
        (wave.detected_at for wave in meta.waves if wave.detected_at is not None),
        default=None,
    )
    if state is not TrendState.CONFIRMED:
        return detected_at, None
    first_confirmation_by_country: dict[str, datetime] = {}
    for wave in meta.waves:
        if wave.state is not TrendState.CONFIRMED or wave.confirmed_at is None:
            continue
        current = first_confirmation_by_country.get(wave.country_code)
        if current is None or wave.confirmed_at < current:
            first_confirmation_by_country[wave.country_code] = wave.confirmed_at
    confirmations = sorted(first_confirmation_by_country.values())
    confirmed_at = confirmations[1] if len(confirmations) >= 2 else None
    return detected_at, confirmed_at


def _confirmation_capable_without_coverage(wave: CountryWave) -> bool:
    if wave.contour is Contour.ACTION:
        authorities = sum(
            point.authority in {"registry", "formal"}
            for point in wave.observations
        )
        return authorities > 0
    active_days = {point.observed_at.date() for point in wave.observations}
    families = max(
        (point.publisher_family_count for point in wave.observations),
        default=0,
    )
    signal = max(
        (min(1.0, abs(float(point.value or 0.0))) for point in wave.observations),
        default=0.0,
    )
    return len(active_days) >= 2 and families >= 2 and signal >= 0.5


def _collector_suppressed(waves: Iterable[CountryWave]) -> int:
    return sum(
        wave.state is not TrendState.CONFIRMED
        and _wave_coverage(wave) <= 0.5
        and _confirmation_capable_without_coverage(wave)
        for wave in waves
    )


def _t0_sanity(
    waves: tuple[CountryWave, ...], metas: tuple[MetaTrend, ...],
    meta_states: tuple[TrendState, ...] | None = None,
    *,
    meta_contexts: dict[tuple[str, str, str], _PreviousMetaContext] | None = None,
    as_of: datetime | None = None,
) -> dict[str, int]:
    entries: list[tuple[TrendState, datetime | None, datetime | None, datetime | None, datetime | None, bool]] = [
        (
            wave.state,
            wave.t0_auto,
            wave.t0_effective,
            wave.detected_at,
            wave.confirmed_at,
            wave.has_analyst_t0_override,
        )
        for wave in waves
    ]
    for index, meta in enumerate(metas):
        state = meta_states[index] if meta_states is not None else _meta_state(meta)
        detected, confirmed = _meta_timeline(meta, state)
        effective_t0 = meta.t0_auto
        inherited_analyst_override = False
        context = (meta_contexts or {}).get(_meta_identity(meta))
        if (
            context is not None
            and context.has_analyst_t0_override
            and not _is_meta_reopening(context.state, state)
        ):
            effective_t0 = context.t0_effective
            inherited_analyst_override = effective_t0 is not None
        elif state is TrendState.CONFIRMED and meta.t0_auto is None:
            effective_t0 = _meta_effective_t0(meta)
            inherited_analyst_override = effective_t0 is not None
        entries.append((
            state, meta.t0_auto, effective_t0, detected, confirmed,
            inherited_analyst_override,
        ))
    automatic = sum(t0_auto is not None for _, t0_auto, _, _, _, _ in entries)
    violations = sum(
        (
            state is TrendState.CONFIRMED and t0_auto is None
            and not (has_analyst_override and t0_effective is not None)
        ) or (
            t0_auto is not None and detected_at is not None and t0_auto > detected_at
        ) or (
            t0_auto is not None and confirmed_at is not None and t0_auto > confirmed_at
        ) or (
            t0_effective is not None and t0_auto is None
            and not has_analyst_override
        ) or (
            t0_effective is not None and detected_at is not None
            and t0_effective > detected_at
        ) or (
            t0_effective is not None and confirmed_at is not None
            and t0_effective > confirmed_at
        ) or (
            as_of is not None and (
                (t0_auto is not None and t0_auto > as_of)
                or (t0_effective is not None and t0_effective > as_of)
            )
        )
        for (
            state, t0_auto, t0_effective, detected_at, confirmed_at,
            has_analyst_override,
        ) in entries
    )
    return {
        "automatic": automatic,
        "unresolved": len(entries) - automatic,
        "violations": violations,
    }


def _contour_completeness(waves: tuple[CountryWave, ...]) -> dict[str, int | float | None]:
    grouped: dict[tuple[str, str, str], list[tuple[int, CountryWave]]] = defaultdict(list)
    for index, wave in enumerate(waves, 1):
        subject, direction = _alignment_identity(wave)
        grouped[(wave.country_code, subject, direction)].append((index, wave))
    linked = 0
    possible = 0
    identity_candidates = 0
    for members in grouped.values():
        media = [(index, wave) for index, wave in members if wave.contour is Contour.MEDIA]
        actions = [(index, wave) for index, wave in members if wave.contour is Contour.ACTION]
        identity_candidates += min(len(media), len(actions))
        eligible = match_contour_episodes(media, actions)
        possible += len(eligible)
        linked += len(eligible)
    return {
        "identity_candidate_pairs": identity_candidates,
        "possible_pairs": possible,
        "linked_pairs": linked,
        "ratio": linked / possible if possible else None,
    }


def run_radar_cycle(
    session,
    as_of: datetime,
    shadow: bool = True,
    *,
    days: int = 90,
    generation_days: int | None = None,
) -> RadarCycleReport:
    """Build a replay report; writes happen only when ``shadow`` is false."""

    as_of = _utc(as_of)
    if days != 90:
        raise ValueError("Radar lookback must be exactly 90 days")
    generation_days = days if generation_days is None else generation_days
    if not 1 <= generation_days <= days:
        raise ValueError("Radar generation window must fit the 90-day lookback")
    incremental = generation_days < days
    window = ObservationWindow(as_of - timedelta(days=days), as_of)
    generation_window = ObservationWindow(
        as_of - timedelta(days=generation_days),
        as_of,
    )
    before_counts = _protected_counts(session)
    generated = [
        *build_media_observations(session, generation_window),
        *build_action_observations(session, generation_window),
    ]
    current_generated_hashes = frozenset(
        observation.input_hash for observation in generated
    )
    history = (
        _history_for_identities(session, as_of, days, generated)
        if incremental
        else _history(session, as_of, days)
    )
    observations = _prefer_logical_observations(history, generated)
    audited_observations = tuple(
        point for point in observations
        if (
            point.input_hash in current_generated_hashes
            or window.start <= _utc(point.observed_at) < window.end
        )
    )
    previous_waves = (
        _previous_waves_for_identities(session, as_of, generated)
        if incremental
        else _previous_waves(session, as_of)
    )
    assignments = assign_country_waves(observations, previous_waves)
    assigned_keys = {wave.wave_key for wave in assignments.waves}
    unmatched_previous = tuple(
        wave for wave in previous_waves
        if wave.wave_key not in assigned_keys
        and wave.state not in (TrendState.RESOLVED, TrendState.REJECTED)
    )
    cycle_waves = (*assignments.waves, *unmatched_previous)
    healthy_buckets = _healthy_collection_buckets(observations, as_of)
    health_window = generation_window if incremental else window
    for country, buckets in _media_collection_health(session, health_window).items():
        healthy_buckets.setdefault((country, Contour.MEDIA), set()).update(buckets)
    scored_waves = tuple(
        _analyze_wave(
            wave,
            as_of,
            healthy_buckets.get((wave.country_code, wave.contour), set()),
        )
        for wave in cycle_waves
    )
    metas = assign_meta_trends(scored_waves, _story_anchors(session, as_of))
    state_counts = _state_distribution(wave.state for wave in scored_waves)
    previous_meta_contexts = _previous_meta_contexts(session, metas.meta_trends)
    meta_states = tuple(
        _meta_state(
            meta,
            previous_meta_contexts.get(_meta_identity(meta)).state
            if _meta_identity(meta) in previous_meta_contexts else None,
        )
        for meta in metas.meta_trends
    )
    meta_state_counts = _state_distribution(meta_states)
    inserted = 0
    updated = 0
    trend_id: int | None = None
    if not shadow:
        if _store(session) is not None:
            inserted, trend_id, updated = _persist_memory(session, generated, scored_waves, metas.meta_trends, as_of)
        else:
            inserted, trend_id, updated = _persist_sql(
                session,
                generated,
                scored_waves,
                metas.meta_trends,
                as_of,
                incremental=incremental,
            )
    after_counts = _protected_counts(session)
    evidence_complete = sum(_valid_evidence_root(point) for point in audited_observations)
    t0_count = sum(wave.t0_auto is not None for wave in scored_waves)
    evidence_validity = _evidence_validity(audited_observations)
    collector_suppressed = _collector_suppressed(scored_waves)
    confirmed_below_coverage_gate = sum(
        wave.state is TrendState.CONFIRMED and _wave_coverage(wave) <= 0.5
        for wave in scored_waves
    )
    return RadarCycleReport(
        as_of=as_of, shadow=shadow, inserted_observations=inserted, updated_trends=updated,
        country_waves=scored_waves, meta_trends=metas.meta_trends, trend_id=trend_id,
        state_counts=state_counts, collector_suppressed=collector_suppressed,
        country_coverage=_coverage(audited_observations),
        t0_distribution={"automatic": t0_count, "unresolved": len(scored_waves) - t0_count},
        evidence_completeness={
            "complete": evidence_complete,
            "incomplete": len(audited_observations) - evidence_complete,
        },
        protected_row_counts=_count_report(before_counts, after_counts),
        lookback_days=days,
        observation_count=len(audited_observations),
        country_state_counts=state_counts,
        meta_state_counts=meta_state_counts,
        evidence_validity=evidence_validity,
        confirmed_below_coverage_gate=confirmed_below_coverage_gate,
        t0_sanity=_t0_sanity(
            scored_waves, metas.meta_trends, meta_states,
            meta_contexts=previous_meta_contexts, as_of=as_of,
        ),
        contour_completeness=_contour_completeness(scored_waves),
    )


def record_analyst_t0_override(session, trend_id: int, revised_t0: datetime, reason: str, revised_by: str | None = None) -> None:
    """Append an analyst decision and set the current effective T0 once."""

    revised_t0 = _utc(revised_t0)
    if not reason.strip():
        raise ValueError("analyst T0 override reason must not be empty")
    store = _store(session)
    if store is not None:
        trend = trend_by_id(session, trend_id)
        if trend is None:
            raise ValueError("unknown radar trend")
        limits = [
            value for value in (
                trend.get("detected_at"), trend.get("confirmed_at"),
                datetime.now(timezone.utc),
            )
            if isinstance(value, datetime)
        ]
        if any(revised_t0 > _utc(limit) for limit in limits):
            raise ValueError(
                "analyst T0 override must not be later than detection, confirmation, or current time"
            )
        store["revisions"].append({"trend_id": trend_id, "previous_t0": trend["t0_effective"], "revised_t0": revised_t0, "revision_kind": "analyst", "reason": reason, "revised_by": revised_by})
        trend["t0_effective"] = revised_t0
        return
    row = session.execute(text("""
        SELECT t0_effective, detected_at, confirmed_at
        FROM radar_trends WHERE id = :id
    """), {"id": trend_id}).first()
    if row is None:
        raise ValueError("unknown radar trend")
    limits = [
        value for value in (
            _row_value(row, "detected_at"), _row_value(row, "confirmed_at"),
            datetime.now(timezone.utc),
        )
        if isinstance(value, datetime)
    ]
    if any(revised_t0 > _utc(limit) for limit in limits):
        raise ValueError(
            "analyst T0 override must not be later than detection, confirmation, or current time"
        )
    previous = _row_value(row, "t0_effective")
    session.execute(text("""
        INSERT INTO radar_t0_revisions (public_id, trend_id, previous_t0, revised_t0, revision_kind, reason, evidence, revised_by)
        VALUES (:public_id, :trend_id, :previous_t0, :revised_t0, 'analyst', :reason, '{}'::jsonb, :revised_by)
    """), {"public_id": uuid5(NAMESPACE_URL, f"geo-pulse:radar-t0:{trend_id}:{revised_t0.isoformat()}"), "trend_id": trend_id, "previous_t0": previous, "revised_t0": revised_t0, "reason": reason, "revised_by": revised_by})
    session.execute(text("UPDATE radar_trends SET t0_effective = :t0 WHERE id = :id"), {"id": trend_id, "t0": revised_t0})


def trend_by_id(session, trend_id: int) -> dict[str, Any] | None:
    store = _store(session)
    if store is not None:
        return next((trend for trend in store["trends"] if trend["id"] == trend_id), None)
    row = session.execute(text("SELECT id, t0_effective, t0_auto, state FROM radar_trends WHERE id = :id"), {"id": trend_id}).first()
    if row is None:
        return None
    return {name: _row_value(row, name) for name in ("id", "t0_effective", "t0_auto", "state")}
