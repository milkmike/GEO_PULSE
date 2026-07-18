"""Replay-safe Radar orchestration and auditable trend persistence."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

from .actions import build_action_observations
from .grouping import CountryWave, MetaTrend, assign_country_waves, assign_meta_trends
from .lifecycle import decide_state
from .media import build_media_observations
from .repository import upsert_observations
from .types import Contour, Observation, ObservationWindow, TrendMetrics, TrendState


DETECTOR_VERSION = "radar-wave-1"


@dataclass(frozen=True, slots=True)
class RadarCycleReport:
    as_of: datetime
    shadow: bool
    inserted_observations: int
    country_waves: tuple[CountryWave, ...]
    meta_trends: tuple[MetaTrend, ...]
    trend_id: int | None
    state_counts: dict[str, int]
    collector_suppressed: int
    country_coverage: dict[str, float]
    t0_distribution: dict[str, int]
    evidence_completeness: dict[str, int]
    protected_row_counts: dict[str, int]

    def json_report(self) -> dict[str, object]:
        return {
            "as_of": self.as_of.isoformat(),
            "shadow": self.shadow,
            "inserted_observations": self.inserted_observations,
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


def _state_for(wave: CountryWave, as_of: datetime) -> TrendState:
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
    )
    return decide_state(metrics, TrendState.CANDIDATE).state


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


def _persist_memory(session, observations: Iterable[Observation], waves: Iterable[CountryWave], metas: Iterable[MetaTrend], as_of: datetime) -> tuple[int, int | None]:
    store = _store(session)
    assert store is not None
    existing_hashes = {row["input_hash"] for row in store["observations"]}
    inserted = 0
    for observation in observations:
        if observation.input_hash not in existing_hashes:
            store["observations"].append(_observation_signature(observation))
            existing_hashes.add(observation.input_hash)
            inserted += 1
    first_id: int | None = None
    for wave in waves:
        identity = (wave.contour.value, wave.country_code, wave.subject_key, wave.direction, DETECTOR_VERSION)
        trend = next((row for row in store["trends"] if row["identity"] == identity), None)
        state = _state_for(wave, as_of).value
        if trend is None:
            trend = {
                "id": len(store["trends"]) + 1, "identity": identity,
                "state": state, "t0_auto": wave.t0_auto, "t0_effective": wave.t0_auto,
            }
            store["trends"].append(trend)
            store["events"].append({"trend_id": trend["id"], "from_state": None, "to_state": state})
        else:
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
    return inserted, first_id


_TREND_BY_IDENTITY = text("""
SELECT id, state, t0_auto, t0_effective FROM radar_trends
WHERE scope = 'country' AND contour = :contour AND country_code = :country_code
  AND subject_key = :subject_key AND direction = :direction
  AND detector_version = :detector_version
""")

_INSERT_TREND = text("""
INSERT INTO radar_trends (
  public_id, scope, contour, country_code, subject_key, title_ru, direction,
  state, confidence, coverage_confidence, velocity, first_observed_at,
  detected_at, confirmed_at, t0_auto, t0_effective, detector_version,
  baseline, explanation
) VALUES (
  :public_id, 'country', :contour, :country_code, :subject_key, :title_ru, :direction,
  :state, :confidence, :coverage_confidence, :velocity, :first_observed_at,
  :detected_at, :confirmed_at, :t0_auto, :t0_effective, :detector_version,
  CAST(:baseline AS jsonb), CAST(:explanation AS jsonb)
) RETURNING id
""")

_UPDATE_TREND = text("""
UPDATE radar_trends trend SET
  state = :state, t0_auto = :t0_auto,
  t0_effective = CASE WHEN EXISTS (
    SELECT 1 FROM radar_t0_revisions revision
    WHERE revision.trend_id = trend.id AND revision.revision_kind = 'analyst'
  ) THEN trend.t0_effective ELSE :t0_effective END,
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
SELECT id, state FROM radar_trends
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
  :t0_auto, :t0_effective, :detector_version, '{}'::jsonb, CAST(:explanation AS jsonb)
) RETURNING id
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


def _persist_sql(session, observations: list[Observation], waves: Iterable[CountryWave], metas: Iterable[MetaTrend], as_of: datetime) -> tuple[int, int | None]:
    inserted = upsert_observations(session, observations)
    first_id: int | None = None
    wave_ids: dict[tuple[str, Contour, str, str], int] = {}
    for wave in waves:
        params = {
            "contour": wave.contour.value, "country_code": wave.country_code,
            "subject_key": wave.subject_key, "direction": wave.direction,
            "detector_version": DETECTOR_VERSION,
        }
        existing = session.execute(_TREND_BY_IDENTITY, params).first()
        state = _state_for(wave, as_of).value
        if existing is None:
            public_id = uuid5(NAMESPACE_URL, "geo-pulse:radar-trend:" + "|".join(map(str, params.values())))
            result = session.execute(_INSERT_TREND, {
                **params, "public_id": public_id, "title_ru": wave.subject_key,
                "state": state, "confidence": 1.0, "coverage_confidence": min(
                    point.coverage_confidence for point in wave.observations),
                "velocity": 0.0, "first_observed_at": wave.first_observed_at,
                "detected_at": as_of, "confirmed_at": as_of if state == "confirmed" else None,
                "t0_auto": wave.t0_auto, "t0_effective": wave.t0_auto,
                "baseline": json.dumps({}), "explanation": json.dumps({"observation_hashes": [point.input_hash for point in wave.observations]}),
            })
            trend_id = int(result.scalar())
            _insert_state_event(session, trend_id, None, state, "initial_assignment", as_of)
        else:
            trend_id = int(_row_value(existing, "id"))
            previous_state = str(_row_value(existing, "state"))
            previous_t0_auto = _row_value(existing, "t0_auto")
            session.execute(_UPDATE_TREND, {"id": trend_id, "state": state, "t0_auto": wave.t0_auto, "t0_effective": wave.t0_auto})
            if previous_t0_auto != wave.t0_auto and wave.t0_auto is not None:
                session.execute(_INSERT_AUTOMATIC_T0_REVISION, {
                    "public_id": uuid5(NAMESPACE_URL, f"geo-pulse:radar-t0-auto:{trend_id}:{wave.t0_auto.isoformat()}"),
                    "trend_id": trend_id, "previous_t0": previous_t0_auto,
                    "revised_t0": wave.t0_auto,
                    "evidence": json.dumps({"detector_version": DETECTOR_VERSION}),
                })
            if previous_state != state:
                _insert_state_event(session, trend_id, previous_state, state, "recalculated", as_of)
        for index, observation in enumerate(wave.observations):
            session.execute(_INSERT_OBSERVATION_EVIDENCE, {
                "public_id": uuid5(NAMESPACE_URL, f"geo-pulse:radar-evidence:{trend_id}:{observation.input_hash}"),
                "trend_id": trend_id, "input_hash": observation.input_hash,
                "role": "trigger" if index == 0 else "support",
                "contribution": 1.0 if index == 0 else 0.5,
                "evidence": json.dumps(dict(observation.evidence)),
            })
        wave_ids[(wave.country_code, wave.contour, wave.subject_key, wave.direction)] = trend_id
        first_id = first_id or trend_id
    _persist_meta_and_contours(session, metas, wave_ids, as_of)
    return inserted, first_id


def _persist_meta_and_contours(
    session, metas: Iterable[MetaTrend],
    wave_ids: dict[tuple[str, Contour, str, str], int], as_of: datetime,
) -> None:
    """Persist meta membership and cross-contour alignment without state edits."""

    for meta in metas:
        params = {"subject_key": meta.subject_key, "direction": meta.direction, "detector_version": DETECTOR_VERSION}
        existing = session.execute(_META_BY_IDENTITY, params).first()
        state = "confirmed" if len({wave.country_code for wave in meta.waves}) >= 2 else "candidate"
        if existing is None:
            result = session.execute(_INSERT_META, {
                **params,
                "public_id": uuid5(NAMESPACE_URL, f"geo-pulse:radar-meta:{meta.subject_key}:{meta.direction}:{DETECTOR_VERSION}"),
                "title_ru": meta.subject_key, "state": state,
                "coverage_confidence": min((point.coverage_confidence for wave in meta.waves for point in wave.observations), default=0.0),
                "first_observed_at": min(wave.first_observed_at for wave in meta.waves),
                "detected_at": as_of, "confirmed_at": as_of if state == "confirmed" else None,
                "t0_auto": meta.t0_auto, "t0_effective": meta.t0_auto,
                "explanation": json.dumps({"member_countries": sorted({wave.country_code for wave in meta.waves})}),
            })
            meta_id = int(result.scalar())
            _insert_state_event(session, meta_id, None, state, "meta_assignment", as_of)
        else:
            meta_id = int(_row_value(existing, "id"))
        for wave in meta.waves:
            country_id = wave_ids[(wave.country_code, wave.contour, wave.subject_key, wave.direction)]
            session.execute(_INSERT_MEMBER, {
                "public_id": uuid5(NAMESPACE_URL, f"geo-pulse:radar-member:{meta_id}:{country_id}"),
                "meta_trend_id": meta_id, "country_trend_id": country_id,
                "evidence": json.dumps({"subject_key": meta.subject_key, "direction": meta.direction}),
            })
    grouped: dict[tuple[str, str, str], dict[Contour, tuple[int, CountryWave]]] = {}
    wave_by_identity = {
        (wave.country_code, wave.contour, wave.subject_key, wave.direction): wave
        for meta in metas for wave in meta.waves
    }
    for (country, contour, subject, direction), trend_id in wave_ids.items():
        grouped.setdefault((country, subject, direction), {})[contour] = (
            trend_id, wave_by_identity[(country, contour, subject, direction)],
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


def _insert_state_event(session, trend_id: int, from_state: str | None, to_state: str, reason: str, as_of: datetime) -> None:
    public_id = uuid5(NAMESPACE_URL, f"geo-pulse:radar-state:{trend_id}:{from_state}:{to_state}:{as_of.isoformat()}")
    session.execute(_INSERT_STATE_EVENT, {
        "public_id": public_id, "trend_id": trend_id, "from_state": from_state,
        "to_state": to_state, "reason": reason, "metrics": json.dumps({}),
        "evidence": json.dumps({}), "occurred_at": as_of,
    })


def _coverage(observations: Iterable[Observation]) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for observation in observations:
        grouped.setdefault(observation.country_code, []).append(observation.coverage_confidence)
    return {country: sum(values) / len(values) for country, values in sorted(grouped.items())}


def run_radar_cycle(session, as_of: datetime, shadow: bool = True, *, days: int = 90) -> RadarCycleReport:
    """Build a replay report; writes happen only when ``shadow`` is false."""

    as_of = _utc(as_of)
    if days <= 0:
        raise ValueError("days must be positive")
    window = ObservationWindow(as_of - timedelta(days=days), as_of)
    observations = [*build_media_observations(session, window), *build_action_observations(session, window)]
    assignments = assign_country_waves(observations, [])
    metas = assign_meta_trends(assignments.waves, [])
    states = [_state_for(wave, as_of).value for wave in assignments.waves]
    state_counts = {state: states.count(state) for state in TrendState}
    state_counts = {state.value if isinstance(state, TrendState) else state: count for state, count in state_counts.items() if count}
    inserted = 0
    trend_id: int | None = None
    if not shadow:
        if _store(session) is not None:
            inserted, trend_id = _persist_memory(session, observations, assignments.waves, metas.meta_trends, as_of)
        else:
            inserted, trend_id = _persist_sql(session, observations, assignments.waves, metas.meta_trends, as_of)
    evidence_complete = sum(bool(point.evidence) for point in observations)
    t0_count = sum(wave.t0_auto is not None for wave in assignments.waves)
    return RadarCycleReport(
        as_of=as_of, shadow=shadow, inserted_observations=inserted,
        country_waves=assignments.waves, meta_trends=metas.meta_trends, trend_id=trend_id,
        state_counts=state_counts, collector_suppressed=0,
        country_coverage=_coverage(observations),
        t0_distribution={"automatic": t0_count, "unresolved": len(assignments.waves) - t0_count},
        evidence_completeness={"complete": evidence_complete, "incomplete": len(observations) - evidence_complete},
        protected_row_counts={
            "radar_observations": 0 if shadow else inserted,
            "radar_trends": 0 if shadow else len(assignments.waves),
            "radar_state_events": 0,
            "radar_t0_revisions": 0,
        },
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
