"""Persistence helpers for immutable Radar observations."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Iterable
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import text

from .types import Contour, Observation, ObservationWindow


def _json_default(value: object) -> str:
    if isinstance(value, (datetime, date, UUID)):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def observation_input_hash(
    *, contour: Contour | str, country_code: str, subject_key: str,
    direction: str, observed_at: datetime, metric: str,
    evidence_ids: Iterable[str],
) -> str:
    """Hash only the immutable observation identity and sorted source evidence."""

    payload = {
        "contour": Contour(contour).value,
        "country_code": country_code,
        "subject_key": subject_key,
        "direction": direction,
        "observed_at": observed_at.isoformat(),
        "metric": metric,
        "evidence_ids": sorted(set(str(value) for value in evidence_ids)),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def make_observation(
    *, country_code: str, contour: Contour | str, subject_key: str,
    direction: str, metric: str, observed_at: datetime,
    evidence_ids: Iterable[str], window: ObservationWindow | None = None,
    value: float | None = None, publisher_family_count: int = 0,
    source_count: int = 0, coverage_confidence: float = 0.0,
    authority: str | None = None, article_id: int | None = None,
    story_id: int | None = None, signal_id: int | None = None,
    canonical_entity_id: UUID | None = None, baseline: dict[str, object] | None = None,
    evidence: dict[str, object] | None = None,
) -> Observation:
    evidence_payload = dict(evidence or {})
    normalized_evidence_ids = tuple(sorted(set(str(item) for item in evidence_ids)))
    evidence_payload["evidence_ids"] = normalized_evidence_ids
    input_hash = observation_input_hash(
        contour=contour, country_code=country_code, subject_key=subject_key,
        direction=direction, observed_at=observed_at, metric=metric,
        evidence_ids=normalized_evidence_ids,
    )
    return Observation(
        public_id=uuid5(NAMESPACE_URL, f"geo-pulse:radar:{input_hash}"),
        input_hash=input_hash,
        country_code=country_code,
        contour=Contour(contour),
        subject_key=subject_key,
        direction=direction,
        metric=metric,
        observed_at=observed_at,
        window=window,
        value=value,
        publisher_family_count=publisher_family_count,
        source_count=source_count,
        coverage_confidence=coverage_confidence,
        authority=authority,
        article_id=article_id,
        story_id=story_id,
        signal_id=signal_id,
        canonical_entity_id=canonical_entity_id,
        baseline=baseline or {},
        evidence=evidence_payload,
    )


_UPSERT = text("""
    INSERT INTO radar_observations (
        public_id, input_hash, country_code, contour, subject_key, direction,
        metric, observed_at, window_start, window_end, value,
        publisher_family_count, source_count, coverage_confidence, authority,
        article_id, story_id, signal_id, canonical_entity_id, baseline, evidence
    ) VALUES (
        :public_id, :input_hash, :country_code, :contour, :subject_key, :direction,
        :metric, :observed_at, :window_start, :window_end, :value,
        :publisher_family_count, :source_count, :coverage_confidence, :authority,
        :article_id, :story_id, :signal_id, :canonical_entity_id,
        CAST(:baseline AS jsonb), CAST(:evidence AS jsonb)
    ) ON CONFLICT (input_hash) DO NOTHING
""")


def upsert_observations(session, observations: Iterable[Observation]) -> int:
    """Insert observations once; reruns are safe by immutable input hash."""

    inserted = 0
    for observation in observations:
        result = session.execute(_UPSERT, {
            "public_id": observation.public_id,
            "input_hash": observation.input_hash,
            "country_code": observation.country_code,
            "contour": observation.contour.value,
            "subject_key": observation.subject_key,
            "direction": observation.direction,
            "metric": observation.metric,
            "observed_at": observation.observed_at,
            "window_start": observation.window.start if observation.window else None,
            "window_end": observation.window.end if observation.window else None,
            "value": observation.value,
            "publisher_family_count": observation.publisher_family_count,
            "source_count": observation.source_count,
            "coverage_confidence": observation.coverage_confidence,
            "authority": observation.authority,
            "article_id": observation.article_id,
            "story_id": observation.story_id,
            "signal_id": observation.signal_id,
            "canonical_entity_id": observation.canonical_entity_id,
            "baseline": json.dumps(dict(observation.baseline), default=_json_default),
            "evidence": json.dumps(dict(observation.evidence), default=_json_default),
        })
        inserted += max(0, int(getattr(result, "rowcount", 0) or 0))
    return inserted
