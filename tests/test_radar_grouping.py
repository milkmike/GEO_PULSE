from datetime import datetime, timedelta, timezone

from src.radar.grouping import CountryWave, assign_country_waves, assign_meta_trends
from src.radar.types import TrendState
from src.radar.repository import make_observation


DAY_1 = datetime(2026, 7, 1, tzinfo=timezone.utc)
DAY_4 = DAY_1 + timedelta(days=3)


def _observation(country, *, at=DAY_1, direction="warming", subject="event:energy"):
    return make_observation(
        country_code=country,
        contour="media",
        subject_key=subject,
        direction=direction,
        metric="attention_share",
        observed_at=at,
        evidence_ids=(f"story:{country}:{at.isoformat()}",),
        coverage_confidence=1.0,
        publisher_family_count=2,
        source_count=2,
        evidence={"story_ids": (1,)},
    )


def _wave(country, *, t0=DAY_1, direction="warming", subject="event:energy"):
    observation = _observation(country, direction=direction, subject=subject)
    return CountryWave(
        country_code=country,
        contour="media",
        subject_key=subject,
        direction=direction,
        observations=(observation,),
        first_observed_at=DAY_1,
        t0_auto=t0,
    )


def test_one_meta_trend_keeps_independent_country_waves():
    result = assign_meta_trends([_wave("ES", t0=DAY_1), _wave("PT", t0=DAY_4)], [])

    assert len(result.meta_trends) == 1
    assert {wave.country_code for wave in result.meta_trends[0].waves} == {"ES", "PT"}
    assert result.meta_trends[0].t0_auto == DAY_1


def test_contradictory_direction_does_not_merge():
    result = assign_meta_trends([
        _wave("ES", direction="warming"),
        _wave("PT", direction="cooling"),
    ], [])

    assert len(result.meta_trends) == 2


def test_country_grouping_never_uses_semantic_evidence_to_cross_direction_or_subject():
    observations = [
        _observation("ES", direction="warming", subject="event:energy"),
        _observation("ES", direction="cooling", subject="event:energy"),
        _observation("ES", direction="warming", subject="event:trade"),
    ]

    result = assign_country_waves(observations, [])

    assert len(result.waves) == 3


def test_country_grouping_splits_distant_observations_into_bounded_waves():
    result = assign_country_waves([
        _observation("ES", at=DAY_1),
        _observation("ES", at=DAY_1 + timedelta(days=30)),
    ], [])

    assert len(result.waves) == 2
    assert len({wave.wave_key for wave in result.waves}) == 2


def test_moving_replay_window_keeps_previous_active_wave_identity():
    first = assign_country_waves([
        _observation("ES", at=DAY_1),
        _observation("ES", at=DAY_1 + timedelta(days=10)),
    ], []).waves[0]

    replay = assign_country_waves([
        _observation("ES", at=DAY_1 + timedelta(days=10)),
        _observation("ES", at=DAY_1 + timedelta(days=20)),
    ], [first])

    assert replay.waves[0].wave_key == first.wave_key


def test_meta_t0_uses_earliest_confirmed_member_only():
    candidate = _wave("ES", t0=DAY_1)
    candidate = CountryWave(
        country_code=candidate.country_code, contour=candidate.contour, subject_key=candidate.subject_key,
        direction=candidate.direction, observations=candidate.observations, first_observed_at=candidate.first_observed_at,
        t0_auto=candidate.t0_auto, wave_key="candidate", state=TrendState.CANDIDATE,
    )
    confirmed = _wave("PT", t0=DAY_4)

    [meta] = assign_meta_trends([candidate, confirmed], []).meta_trends

    assert meta.t0_auto == DAY_4


def test_story_event_anchors_prevent_generic_subject_merge():
    first = make_observation(
        country_code="ES", contour="media", subject_key="media:coverage", direction="warming",
        metric="attention_share", observed_at=DAY_1, evidence_ids=("story:1",),
        coverage_confidence=1, evidence={"story_ids": (1,)},
    )
    second = make_observation(
        country_code="PT", contour="media", subject_key="media:coverage", direction="warming",
        metric="attention_share", observed_at=DAY_1, evidence_ids=("story:2",),
        coverage_confidence=1, evidence={"story_ids": (2,)},
    )
    waves = [
        CountryWave("ES", "media", "media:coverage", "warming", (first,), DAY_1, DAY_1),
        CountryWave("PT", "media", "media:coverage", "warming", (second,), DAY_1, DAY_1),
    ]

    result = assign_meta_trends(waves, [{"id": 1, "event_key": "energy"}, {"id": 2, "event_key": "trade"}])

    assert len(result.meta_trends) == 2
