"""Conservative, deterministic country-wave and meta-trend grouping.

Grouping deliberately works from the canonical observation keys produced by the
adapters.  A similarity score may enrich a future explanation, but it never
joins observations whose event/subject or direction disagree.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from .types import Contour, Observation


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "contour", Contour(self.contour))


@dataclass(frozen=True, slots=True)
class MetaTrend:
    subject_key: str
    direction: str
    waves: tuple[CountryWave, ...]
    t0_auto: datetime | None


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

    ``previous`` is intentionally accepted as context rather than as mutable
    state: the stable identity is retained in persistence, while a replay can
    be reconstructed solely from immutable observations.
    """

    del previous
    grouped: dict[tuple[str, Contour, str, str], list[Observation]] = {}
    for observation in observations:
        grouped.setdefault(_identity(observation), []).append(observation)

    waves: list[CountryWave] = []
    for (country, contour, subject, direction), members in sorted(grouped.items()):
        ordered = sorted(members, key=lambda point: (point.observed_at, point.input_hash))
        segment: list[Observation] = []
        for observation in ordered:
            if segment and observation.observed_at - segment[-1].observed_at > MAX_WAVE_GAP:
                waves.append(_wave(country, contour, subject, direction, segment))
                segment = []
            segment.append(observation)
        if segment:
            waves.append(_wave(country, contour, subject, direction, segment))
    return CountryWaveAssignment(tuple(waves))


def _wave(
    country: str, contour: Contour, subject: str, direction: str,
    observations: list[Observation],
) -> CountryWave:
    first = observations[0].observed_at
    # The automatic baseline/T0 detector may later replace this safe initial
    # value.  It never replaces an analyst correction in the service layer.
    return CountryWave(
        country_code=country,
        contour=contour,
        subject_key=subject,
        direction=direction,
        observations=tuple(observations),
        first_observed_at=first,
        t0_auto=first,
    )


def assign_meta_trends(
    waves: Iterable[CountryWave], stories: Sequence[object] | None,
) -> MetaTrendAssignment:
    """Combine only identical canonical subjects and directions across waves.

    Country waves remain independent members.  In particular, contour is not
    an identity of a meta-trend: media/action agreement is evaluated separately
    by ``radar_contour_links`` and cannot rewrite either member's state.
    """

    del stories
    grouped: dict[tuple[str, str], list[CountryWave]] = {}
    for wave in waves:
        grouped.setdefault((wave.subject_key, wave.direction), []).append(wave)
    meta_trends: list[MetaTrend] = []
    for (subject, direction), members in sorted(grouped.items()):
        ordered = tuple(sorted(members, key=lambda wave: (
            wave.country_code, wave.contour.value, wave.first_observed_at,
        )))
        t0_candidates = [wave.t0_auto for wave in ordered if wave.t0_auto is not None]
        meta_trends.append(MetaTrend(
            subject_key=subject,
            direction=direction,
            waves=ordered,
            t0_auto=min(t0_candidates) if t0_candidates else None,
        ))
    return MetaTrendAssignment(tuple(meta_trends))
