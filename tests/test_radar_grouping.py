from datetime import datetime, timedelta, timezone

from src.radar.grouping import CountryWave, assign_country_waves, assign_meta_trends
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
