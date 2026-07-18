"""Conservative, deterministic country-wave and meta-trend grouping.

Grouping deliberately works from the canonical observation keys produced by the
adapters.  A similarity score may enrich a future explanation, but it never
joins observations whose event/subject or direction disagree.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from .types import BaselineResult, Contour, Observation, TrendState


MAX_WAVE_GAP = timedelta(days=14)


@dataclass(frozen=True, slots=True)
class CountryWave:
    country_code: str
    contour: Contour
    subject_key: str
    direction: str
    observations: tuple[Observation, ...]
    first_observed_at: datetime
    t0_auto: datetime | None
    wave_key: str = ""
    last_observed_at: datetime | None = None
    state: TrendState = TrendState.CONFIRMED
    detected_at: datetime | None = None
    confirmed_at: datetime | None = None
    t0_effective: datetime | None = None
    baseline: BaselineResult | None = None
    lifecycle_reason: str = "unscored"
    t0_status: str = "not_evaluated"
    velocity: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "contour", Contour(self.contour))
        object.__setattr__(self, "state", TrendState(self.state))
        if self.last_observed_at is None:
            object.__setattr__(self, "last_observed_at", self.observations[-1].observed_at)


@dataclass(frozen=True, slots=True)
class MetaTrend:
    subject_key: str
    direction: str
    waves: tuple[CountryWave, ...]
    t0_auto: datetime | None
    meta_key: str = ""


@dataclass(frozen=True, slots=True)
class CountryWaveAssignment:
    waves: tuple[CountryWave, ...]


@dataclass(frozen=True, slots=True)
class MetaTrendAssignment:
    meta_trends: tuple[MetaTrend, ...]


def _identity(observation: Observation) -> tuple[str, Contour, str, str]:
    return (
        observation.country_code,
        Contour(observation.contour),
        observation.subject_key,
        observation.direction,
    )


def assign_country_waves(
    observations: Iterable[Observation], previous: Sequence[CountryWave] | None,
) -> CountryWaveAssignment:
    """Assign observations only when all canonical identity fields agree.

    Prior waves make identities survive a moving lookback window.  We only
    reuse a prior key for the same hard identity with an overlapping evidence
    hash or a gap no greater than ``MAX_WAVE_GAP``; distant recurrence creates
    a distinct trend rather than silently rewriting old evidence.
    """

    previous = previous or ()
    grouped: dict[tuple[str, Contour, str, str], list[Observation]] = {}
    for observation in observations:
        grouped.setdefault(_identity(observation), []).append(observation)

    waves: list[CountryWave] = []
    for (country, contour, subject, direction), members in sorted(grouped.items()):
        ordered = sorted(members, key=lambda point: (point.observed_at, point.input_hash))
        segment: list[Observation] = []
        for observation in ordered:
            if segment and observation.observed_at - segment[-1].observed_at > MAX_WAVE_GAP:
                waves.append(_wave(country, contour, subject, direction, segment, previous))
                segment = []
            segment.append(observation)
        if segment:
            waves.append(_wave(country, contour, subject, direction, segment, previous))
    return CountryWaveAssignment(tuple(waves))


def _wave(
    country: str, contour: Contour, subject: str, direction: str,
    observations: list[Observation], previous: Sequence[CountryWave],
) -> CountryWave:
    first = observations[0].observed_at
    last = observations[-1].observed_at
    identity = (country, contour, subject, direction)
    hashes = {point.input_hash for point in observations}
    compatible = [wave for wave in previous if (
        wave.country_code, wave.contour, wave.subject_key, wave.direction
    ) == identity]
    reused = next((wave for wave in compatible if hashes & {
        point.input_hash for point in wave.observations
    }), None)
    if reused is None:
        nearby = [wave for wave in compatible if wave.last_observed_at is not None and (
            first - wave.last_observed_at <= MAX_WAVE_GAP
            and last >= wave.first_observed_at - MAX_WAVE_GAP
        )]
        reused = max(nearby, key=lambda wave: wave.last_observed_at or wave.first_observed_at, default=None)
    wave_key = reused.wave_key if reused is not None and reused.wave_key else hashlib.sha256(
        "|".join((country, contour.value, subject, direction, observations[0].input_hash)).encode()
    ).hexdigest()
    # The automatic baseline/T0 detector may later replace this safe initial
    # value.  It never replaces an analyst correction in the service layer.
    return CountryWave(
        country_code=country,
        contour=contour,
        subject_key=subject,
        direction=direction,
        observations=tuple(observations),
        first_observed_at=min(first, reused.first_observed_at) if reused is not None else first,
        t0_auto=reused.t0_auto if reused is not None else None,
        wave_key=wave_key,
        last_observed_at=last,
        state=reused.state if reused is not None else TrendState.CANDIDATE,
        detected_at=reused.detected_at if reused is not None else None,
        confirmed_at=reused.confirmed_at if reused is not None else None,
        t0_effective=reused.t0_effective if reused is not None else None,
    )


def assign_meta_trends(
    waves: Iterable[CountryWave], stories: Sequence[object] | None,
) -> MetaTrendAssignment:
    """Combine only identical canonical subjects and directions across waves.

    Country waves remain independent members.  In particular, contour is not
    an identity of a meta-trend: media/action agreement is evaluated separately
    by ``radar_contour_links`` and cannot rewrite either member's state.
    """

    anchors = _story_anchors(stories or ())
    grouped: dict[tuple[str, str], list[CountryWave]] = {}
    for wave in waves:
        # Persisted story/event context is an anchor, not a semantic hint.  A
        # generic subject with distinct canonical story anchors stays separate.
        anchor = _wave_anchor(wave, anchors)
        grouped.setdefault((wave.subject_key, wave.direction, anchor), []).append(wave)
    meta_trends: list[MetaTrend] = []
    for (subject, direction, anchor), members in sorted(grouped.items()):
        ordered = tuple(sorted(members, key=lambda wave: (
            wave.country_code, wave.contour.value, wave.first_observed_at,
        )))
        t0_candidates = [
            wave.t0_auto for wave in ordered
            if wave.state is TrendState.CONFIRMED and wave.t0_auto is not None
        ]
        meta_trends.append(MetaTrend(
            subject_key=subject,
            direction=direction,
            waves=ordered,
            t0_auto=min(t0_candidates) if t0_candidates else None,
            meta_key=_meta_key(subject, direction, anchor),
        ))
    return MetaTrendAssignment(tuple(meta_trends))


def _story_anchors(stories: Sequence[object]) -> dict[int, str]:
    anchors: dict[int, str] = {}
    for story in stories:
        story_id = getattr(story, "id", None)
        if isinstance(story, dict):
            story_id = story.get("id", story_id)
        if story_id is None:
            continue
        value = getattr(story, "event_key", None) or getattr(story, "subject_key", None)
        if isinstance(story, dict):
            value = story.get("event_key") or story.get("subject_key") or value
        if value:
            anchors[int(story_id)] = str(value)
    return anchors


def _wave_anchor(wave: CountryWave, anchors: dict[int, str]) -> str:
    if wave.subject_key.startswith("event:"):
        return wave.subject_key
    story_ids = {
        int(story_id) for point in wave.observations
        for story_id in point.evidence.get("story_ids", ())
    }
    resolved = sorted(
        f"story:{story_id}:{anchors[story_id]}"
        for story_id in story_ids if story_id in anchors
    )
    return "|".join(resolved) if resolved else wave.subject_key


def _meta_key(subject: str, direction: str, anchor: str) -> str:
    """A durable meta identity: canonical subject, or an explicit story anchor."""

    if subject.startswith(("event:", "policy:", "diplomacy:", "economy:", "energy:")):
        source = f"canonical:{subject}:{direction}"
    else:
        source = f"anchor:{anchor}:{direction}"
    return source
