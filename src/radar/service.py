"""Replay-safe Radar orchestration and auditable trend persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text

from .actions import build_action_observations
from .grouping import CountryWave, MetaTrend, assign_country_waves, assign_meta_trends
from .lifecycle import decide_state
from .media import build_media_observations
from .repository import upsert_observations
from .types import Contour, Observation, ObservationWindow, TrendMetrics, TrendState, TrendTimeline


DETECTOR_VERSION = "radar-wave-1"

_PRIOR_WAVES = text("""
/* radar_prior_waves */
SELECT trend.id, trend.wave_key, trend.country_code, trend.contour, trend.subject_key,
       trend.direction, trend.state, trend.first_observed_at, trend.confirmed_at,
       trend.t0_auto, trend.t0_effective,
       (SELECT max(observation.observed_at) FROM radar_trend_evidence evidence
        JOIN radar_observations observation ON observation.id = evidence.observation_id
        WHERE evidence.trend_id = trend.id) AS last_observed_at
FROM radar_trends trend
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

    def json_report(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "shadow": self.shadow,
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


def _previous_waves(session, as_of: datetime) -> tuple[CountryWave, ...]:
    if _store(session) is not None:
        return ()
    rows = session.execute(_PRIOR_WAVES, {"detector_version": DETECTOR_VERSION, "as_of": as_of}).fetchall()
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
            state=_row_value(row, "state", TrendState.CANDIDATE), confirmed_at=_row_value(row, "confirmed_at"),
        ))
    return tuple(waves)


def _story_anchors(session, as_of: datetime) -> tuple[object, ...]:
    if _store(session) is not None:
        return ()
    return tuple(session.execute(_STORY_ANCHORS, {"as_of": as_of}).fetchall())


def _history(session, as_of: datetime, days: int) -> tuple[Observation, ...]:
    if _store(session) is not None:
        return ()
    rows = session.execute(_HISTORY, {"history_start": as_of - timedelta(days=days + 14), "as_of": as_of}).fetchall()
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


def _state_for(wave: CountryWave, as_of: datetime):
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
            confirmed_at=wave.confirmed_at,
            t0_auto=wave.t0_auto,
            t0_effective=wave.t0_effective,
        ),
    )
    return decide_state(metrics, wave.state)


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
        identity = ("meta", meta.subject_key, meta.direction, DETECTOR_VERSION)
        if not any(row["identity"] == identity for row in store["trends"]):
            store["trends"].append({
                "id": len(store["trends"]) + 1, "identity": identity,
                "state": "confirmed" if len(meta.waves) >= 2 else "candidate",
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
  state, confidence, coverage_confidence, velocity, first_observed_at,
  detected_at, confirmed_at, t0_auto, t0_effective, detector_version,
  baseline, explanation
) VALUES (
  :public_id, 'country', :contour, :country_code, :subject_key, :wave_key, :title_ru, :direction,
  :state, :confidence, :coverage_confidence, :velocity, :first_observed_at,
  :detected_at, :confirmed_at, :t0_auto, :t0_effective, :detector_version,
  CAST(:baseline AS jsonb), CAST(:explanation AS jsonb)
) RETURNING id
""")

_UPDATE_TREND = text("""
UPDATE radar_trends trend SET
  state = :state, confidence = :confidence, coverage_confidence = :coverage_confidence,
  velocity = :velocity, confirmed_at = COALESCE(trend.confirmed_at, :confirmed_at), t0_auto = :t0_auto,
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
  public_id, trend_id, observation_id, role, contribution, evidence
)
SELECT :public_id, :trend_id, observation.id, :role, :contribution,
       CAST(:evidence AS jsonb)
FROM radar_observations observation
WHERE observation.input_hash = :input_hash
  AND NOT EXISTS (
    SELECT 1 FROM radar_trend_evidence prior
    WHERE prior.trend_id = :trend_id AND prior.observation_id = observation.id
  )
""")

_META_BY_IDENTITY = text("""
SELECT id, state, confirmed_at, t0_auto, t0_effective FROM radar_trends
WHERE scope = 'meta' AND subject_key = :subject_key AND direction = :direction
  AND detector_version = :detector_version
""")

_INSERT_META = text("""
INSERT INTO radar_trends (
  public_id, scope, subject_key, title_ru, direction, state, confidence,
  coverage_confidence, velocity, first_observed_at, detected_at, confirmed_at,
  t0_auto, t0_effective, detector_version, baseline, explanation
) VALUES (
  :public_id, 'meta', :subject_key, :title_ru, :direction, :state, 1.0,
  :coverage_confidence, 0, :first_observed_at, :detected_at, :confirmed_at,
  :t0_auto, :t0_effective, :detector_version, CAST(:baseline AS jsonb), CAST(:explanation AS jsonb)
) RETURNING id
""")

_UPDATE_META = text("""
UPDATE radar_trends trend SET
  state = :state, confidence = :confidence, coverage_confidence = :coverage_confidence,
  confirmed_at = COALESCE(trend.confirmed_at, :confirmed_at), t0_auto = :t0_auto,
  t0_effective = CASE WHEN EXISTS (
    SELECT 1 FROM radar_t0_revisions revision
    WHERE revision.trend_id = trend.id AND revision.revision_kind = 'analyst'
  ) THEN trend.t0_effective ELSE :t0_effective END,
  baseline = CAST(:baseline AS jsonb), explanation = CAST(:explanation AS jsonb), updated_at = NOW()
WHERE trend.id = :id
""")

_INSERT_MEMBER = text("""
INSERT INTO radar_trend_members (public_id, meta_trend_id, country_trend_id, evidence)
VALUES (:public_id, :meta_trend_id, :country_trend_id, CAST(:evidence AS jsonb))
ON CONFLICT (meta_trend_id, country_trend_id) DO NOTHING
""")

_UPSERT_CONTOUR_LINK = text("""
INSERT INTO radar_contour_links (public_id, media_trend_id, action_trend_id, status, evidence, evaluated_at)
VALUES (:public_id, :media_trend_id, :action_trend_id, :status, CAST(:evidence AS jsonb), :evaluated_at)
ON CONFLICT (media_trend_id, action_trend_id) DO UPDATE SET
  status = EXCLUDED.status, evidence = EXCLUDED.evidence,
  evaluated_at = EXCLUDED.evaluated_at, updated_at = NOW()
""")


def _persist_sql(session, observations: list[Observation], waves: Iterable[CountryWave], metas: Iterable[MetaTrend], as_of: datetime) -> tuple[int, int | None, int]:
    inserted = upsert_observations(session, observations)
    first_id: int | None = None
    updated = 0
    wave_ids: dict[tuple[str, Contour, str, str, str], int] = {}
    for wave in waves:
        params = {
            "contour": wave.contour.value, "country_code": wave.country_code,
            "subject_key": wave.subject_key, "direction": wave.direction,
            "wave_key": wave.wave_key,
            "detector_version": DETECTOR_VERSION,
        }
        existing = session.execute(_TREND_BY_IDENTITY, params).first()
        decision = _state_for(wave, as_of)
        state = decision.state.value
        if existing is None:
            public_id = uuid5(NAMESPACE_URL, "geo-pulse:radar-trend:" + "|".join(map(str, params.values())))
            result = session.execute(_INSERT_TREND, {
                **params, "public_id": public_id, "title_ru": wave.subject_key,
                "state": state, "confidence": _confidence(wave), "coverage_confidence": min(
                    point.coverage_confidence for point in wave.observations),
                "velocity": 0.0, "first_observed_at": wave.first_observed_at,
                "detected_at": as_of, "confirmed_at": decision.timeline.confirmed_at,
                "t0_auto": wave.t0_auto, "t0_effective": wave.t0_auto,
                "baseline": json.dumps(_baseline_payload(wave)), "explanation": json.dumps({"wave_key": wave.wave_key, "observation_hashes": [point.input_hash for point in wave.observations]}),
            })
            trend_id = int(result.scalar())
            _insert_state_event(session, trend_id, None, state, "initial_assignment", as_of, metrics=_baseline_payload(wave), evidence={"wave_key": wave.wave_key, "observation_hashes": [point.input_hash for point in wave.observations]})
        else:
            trend_id = int(_row_value(existing, "id"))
            previous_state = str(_row_value(existing, "state"))
            previous_t0_auto = _row_value(existing, "t0_auto")
            result = session.execute(_UPDATE_TREND, {
                "id": trend_id, "state": state, "t0_auto": wave.t0_auto,
                "t0_effective": wave.t0_auto, "confidence": _confidence(wave),
                "coverage_confidence": min(point.coverage_confidence for point in wave.observations),
                "velocity": _velocity(wave), "confirmed_at": decision.timeline.confirmed_at,
                "baseline": json.dumps(_baseline_payload(wave)),
                "explanation": json.dumps({"observation_hashes": [point.input_hash for point in wave.observations], "wave_key": wave.wave_key}),
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
                _insert_state_event(session, trend_id, previous_state, state, "recalculated", as_of, metrics=_baseline_payload(wave), evidence={"wave_key": wave.wave_key, "observation_hashes": [point.input_hash for point in wave.observations]})
        for index, observation in enumerate(wave.observations):
            session.execute(_INSERT_OBSERVATION_EVIDENCE, {
                "public_id": uuid5(NAMESPACE_URL, f"geo-pulse:radar-evidence:{trend_id}:{observation.input_hash}"),
                "trend_id": trend_id, "input_hash": observation.input_hash,
                "role": "trigger" if index == 0 else "support",
                "contribution": 1.0 if index == 0 else 0.5,
                "evidence": json.dumps(dict(observation.evidence)),
            })
        wave_ids[(wave.country_code, wave.contour, wave.subject_key, wave.direction, wave.wave_key)] = trend_id
        first_id = first_id or trend_id
    _persist_meta_and_contours(session, metas, wave_ids, as_of)
    return inserted, first_id, updated


def _persist_meta_and_contours(
    session, metas: Iterable[MetaTrend],
    wave_ids: dict[tuple[str, Contour, str, str, str], int], as_of: datetime,
) -> None:
    """Persist meta membership and cross-contour alignment without state edits."""

    for meta in metas:
        params = {"subject_key": meta.subject_key, "direction": meta.direction, "detector_version": DETECTOR_VERSION}
        existing = session.execute(_META_BY_IDENTITY, params).first()
        confirmed = tuple(wave for wave in meta.waves if wave.state is TrendState.CONFIRMED)
        state = "confirmed" if len({wave.country_code for wave in confirmed}) >= 2 else "candidate"
        coverage = min((point.coverage_confidence for wave in meta.waves for point in wave.observations), default=0.0)
        explanation = {"member_countries": sorted({wave.country_code for wave in meta.waves}), "confirmed_members": sorted({wave.country_code for wave in confirmed})}
        baseline = {"member_count": len(meta.waves), "confirmed_member_count": len(confirmed)}
        if existing is None:
            result = session.execute(_INSERT_META, {
                **params,
                "public_id": uuid5(NAMESPACE_URL, f"geo-pulse:radar-meta:{meta.subject_key}:{meta.direction}:{DETECTOR_VERSION}"),
                "title_ru": meta.subject_key, "state": state,
                "coverage_confidence": coverage,
                "first_observed_at": min(wave.first_observed_at for wave in meta.waves),
                "detected_at": as_of, "confirmed_at": as_of if state == "confirmed" else None,
                "t0_auto": meta.t0_auto, "t0_effective": meta.t0_auto,
                "baseline": json.dumps(baseline), "explanation": json.dumps(explanation),
            })
            meta_id = int(result.scalar())
            _insert_state_event(session, meta_id, None, state, "meta_assignment", as_of, metrics=baseline, evidence=explanation)
        else:
            meta_id = int(_row_value(existing, "id"))
            prior_state = str(_row_value(existing, "state"))
            prior_t0 = _row_value(existing, "t0_auto")
            session.execute(_UPDATE_META, {
                "id": meta_id, "state": state, "confidence": coverage,
                "coverage_confidence": coverage,
                "confirmed_at": as_of if state == "confirmed" else None,
                "t0_auto": meta.t0_auto, "t0_effective": meta.t0_auto,
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
        for wave in meta.waves:
            country_id = wave_ids[(wave.country_code, wave.contour, wave.subject_key, wave.direction, wave.wave_key)]
            session.execute(_INSERT_MEMBER, {
                "public_id": uuid5(NAMESPACE_URL, f"geo-pulse:radar-member:{meta_id}:{country_id}"),
                "meta_trend_id": meta_id, "country_trend_id": country_id,
                "evidence": json.dumps({"subject_key": meta.subject_key, "direction": meta.direction}),
            })
    grouped: dict[tuple[str, str, str], dict[Contour, tuple[int, CountryWave]]] = {}
    wave_by_identity = {
        (wave.country_code, wave.contour, wave.subject_key, wave.direction, wave.wave_key): wave
        for meta in metas for wave in meta.waves
    }
    for (country, contour, subject, direction, wave_key), trend_id in wave_ids.items():
        grouped.setdefault((country, subject, direction), {})[contour] = (
            trend_id, wave_by_identity[(country, contour, subject, direction, wave_key)],
        )
    for (country, subject, direction), contours in grouped.items():
        media, action = contours.get(Contour.MEDIA), contours.get(Contour.ACTION)
        if media is None or action is None:
            continue
        media_id, media_wave = media
        action_id, action_wave = action
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
    return min(point.coverage_confidence for point in wave.observations)


def _velocity(wave: CountryWave) -> float:
    if len(wave.observations) < 2:
        return 0.0
    span = max(1.0, (wave.observations[-1].observed_at - wave.observations[0].observed_at).total_seconds() / 86400)
    return len(wave.observations) / span


def _baseline_payload(wave: CountryWave) -> dict[str, object]:
    return {
        "observation_count": len(wave.observations),
        "first_observed_at": wave.first_observed_at.isoformat(),
        "last_observed_at": wave.last_observed_at.isoformat() if wave.last_observed_at else None,
        "wave_key": wave.wave_key,
        "coverage_confidence": _confidence(wave),
    }


def run_radar_cycle(session, as_of: datetime, shadow: bool = True, *, days: int = 90) -> RadarCycleReport:
    """Build a replay report; writes happen only when ``shadow`` is false."""

    as_of = _utc(as_of)
    if days <= 0:
        raise ValueError("days must be positive")
    window = ObservationWindow(as_of - timedelta(days=days), as_of)
    before_counts = _protected_counts(session)
    generated = [*build_media_observations(session, window), *build_action_observations(session, window)]
    history = _history(session, as_of, days)
    observations = list({point.input_hash: point for point in [*history, *generated]}.values())
    assignments = assign_country_waves(observations, _previous_waves(session, as_of))
    scored_waves = tuple(
        replace(wave, state=decision.state, confirmed_at=decision.timeline.confirmed_at,
                t0_effective=decision.timeline.t0_effective or wave.t0_effective or wave.t0_auto)
        for wave in assignments.waves for decision in (_state_for(wave, as_of),)
    )
    metas = assign_meta_trends(scored_waves, _story_anchors(session, as_of))
    states = [wave.state.value for wave in scored_waves]
    state_counts = {state: states.count(state) for state in TrendState}
    state_counts = {state.value if isinstance(state, TrendState) else state: count for state, count in state_counts.items() if count}
    inserted = 0
    updated = 0
    trend_id: int | None = None
    if not shadow:
        if _store(session) is not None:
            inserted, trend_id, updated = _persist_memory(session, generated, scored_waves, metas.meta_trends, as_of)
        else:
            inserted, trend_id, updated = _persist_sql(session, generated, scored_waves, metas.meta_trends, as_of)
    after_counts = _protected_counts(session)
    evidence_complete = sum(bool(point.evidence) for point in observations)
    t0_count = sum(wave.t0_auto is not None for wave in assignments.waves)
    return RadarCycleReport(
        as_of=as_of, shadow=shadow, inserted_observations=inserted, updated_trends=updated,
        country_waves=scored_waves, meta_trends=metas.meta_trends, trend_id=trend_id,
        state_counts=state_counts, collector_suppressed=0,
        country_coverage=_coverage(observations),
        t0_distribution={"automatic": t0_count, "unresolved": len(scored_waves) - t0_count},
        evidence_completeness={"complete": evidence_complete, "incomplete": len(observations) - evidence_complete},
        protected_row_counts=_count_report(before_counts, after_counts),
    )


def record_analyst_t0_override(session, trend_id: int, revised_t0: datetime, reason: str, revised_by: str | None = None) -> None:
    """Append an analyst decision and set the current effective T0 once."""

    revised_t0 = _utc(revised_t0)
    store = _store(session)
    if store is not None:
        trend = trend_by_id(session, trend_id)
        if trend is None:
            raise ValueError("unknown radar trend")
        store["revisions"].append({"trend_id": trend_id, "previous_t0": trend["t0_effective"], "revised_t0": revised_t0, "revision_kind": "analyst", "reason": reason, "revised_by": revised_by})
        trend["t0_effective"] = revised_t0
        return
    row = session.execute(text("SELECT t0_effective FROM radar_trends WHERE id = :id"), {"id": trend_id}).first()
    if row is None:
        raise ValueError("unknown radar trend")
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
