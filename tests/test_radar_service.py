from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from src.radar.actions import build_action_observations
from src.radar.media import build_media_observations
from src.radar.repository import make_observation
from src.radar.grouping import CountryWave, MetaTrend
from src.radar.service import _persist_meta_and_contours, _persist_sql, _state_for, match_contour_episodes, record_analyst_t0_override, run_radar_cycle
from src.radar.types import Contour, ObservationWindow, TrendState


AS_OF = datetime(2026, 7, 18, tzinfo=timezone.utc)
ANALYST_T0 = datetime(2026, 7, 3, tzinfo=timezone.utc)


def _observation():
    return make_observation(
        country_code="ES",
        contour="action",
        subject_key="policy:sanctions:russia",
        direction="increase",
        metric="delta",
        observed_at=AS_OF,
        evidence_ids=("sanctions:1",),
        value=4,
        authority="registry",
        source_count=1,
        coverage_confidence=1.0,
        evidence={"status": "verified"},
    )


class _Result:
    def __init__(self, rows=(), scalar=None, rowcount=0):
        self.rows = list(rows)
        self._scalar = scalar
        self.rowcount = rowcount

    def fetchall(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def scalar(self):
        return self._scalar


class _Session:
    """The public service supports this small in-memory transaction adapter."""

    def __init__(self):
        self.radar_store = {"observations": [], "trends": [], "events": [], "revisions": []}
        self.writes = 0


class _RecordingSqlSession:
    """Minimal SQLAlchemy-shaped recorder; no in-memory Radar shortcut."""

    def __init__(self):
        self.calls = []
        self.count_reads = 0
        self.next_id = 1

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        self.calls.append((sql, params))
        if "radar_protected_counts" in sql:
            self.count_reads += 1
            return _Result(rows=[{
                "articles": 10, "stories": 2, "signals": 3, "temperature": 4,
                "radar_observations": 0 if self.count_reads == 1 else 2,
                "radar_trends": 0 if self.count_reads == 1 else 3,
                "radar_trend_members": 0, "radar_trend_evidence": 0,
                "radar_state_events": 0 if self.count_reads == 1 else 3,
                "radar_t0_revisions": 0, "radar_contour_links": 0,
            }])
        if "radar_prior_waves" in sql or "radar_story_anchors" in sql or "radar_observation_history" in sql:
            return _Result()
        if "SELECT id, state, first_observed_at" in sql or "SELECT id, state, confirmed_at" in sql:
            return _Result()
        if "RETURNING id" in sql:
            value = self.next_id
            self.next_id += 1
            return _Result(scalar=value, rowcount=1)
        if "INSERT INTO radar_observations" in sql:
            return _Result(rowcount=1)
        return _Result(rowcount=1)


def test_radar_shadow_creates_no_rows(monkeypatch):
    import src.radar.service as service

    monkeypatch.setattr(service, "build_media_observations", lambda *_: [_observation()])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])
    session = _Session()

    report = run_radar_cycle(session, AS_OF, shadow=True)

    assert report.inserted_observations == 0
    assert session.radar_store == {"observations": [], "trends": [], "events": [], "revisions": []}


@pytest.mark.parametrize("days", (89, 91))
def test_radar_cycle_rejects_any_non_exact_lookback(monkeypatch, days):
    import src.radar.service as service

    monkeypatch.setattr(service, "build_media_observations", lambda *_: [])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])

    with pytest.raises(ValueError, match="exactly 90"):
        run_radar_cycle(_Session(), AS_OF, shadow=True, days=days)


def test_incremental_cycle_generates_three_days_but_keeps_90_day_lookback(
    monkeypatch,
):
    import src.radar.service as service

    generated = _media_point(
        AS_OF - timedelta(days=1), value=0.8, article_id=920,
    )
    windows = []

    def media(_session, window):
        windows.append(window)
        return [generated]

    monkeypatch.setattr(service, "build_media_observations", media)
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])

    report = run_radar_cycle(
        _Session(), AS_OF, shadow=True, generation_days=3,
    )

    assert windows[0].start == AS_OF - timedelta(days=3)
    assert windows[0].end == AS_OF
    assert report.lookback_days == 90
    assert report.observation_count == 1


@pytest.mark.parametrize("generation_days", (0, 91))
def test_incremental_generation_window_must_fit_lookback(generation_days):
    with pytest.raises(ValueError, match="generation window"):
        run_radar_cycle(
            _Session(), AS_OF, shadow=True,
            generation_days=generation_days,
        )


def test_release_metrics_exclude_detector_context_before_exact_90_day_window(monkeypatch):
    import src.radar.service as service

    context = make_observation(
        country_code="ES", contour="media", subject_key="event:energy",
        direction="negative", metric="attention_share",
        observed_at=AS_OF - timedelta(days=95),
        evidence_ids=("opaque:context",), value=0.1,
        publisher_family_count=2, source_count=2, coverage_confidence=0.2,
        evidence={},
    )
    audited = _media_point(
        AS_OF - timedelta(days=89), value=0.8, article_id=909,
    )
    monkeypatch.setattr(service, "_history", lambda *_: (context, audited))
    monkeypatch.setattr(service, "build_media_observations", lambda *_: [])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])

    report = run_radar_cycle(_Session(), AS_OF, shadow=True)

    assert len(report.country_waves[0].observations) == 2
    assert report.observation_count == 1
    assert report.evidence_validity == {
        "total": 1, "valid": 1, "invalid": 0, "ratio": 1.0,
    }
    assert report.evidence_completeness == {"complete": 1, "incomplete": 0}
    assert report.country_coverage == {"ES": 1.0}


def test_release_metrics_include_current_window_generated_day_bucket_but_not_history(monkeypatch):
    import src.radar.service as service

    as_of = AS_OF + timedelta(hours=12)
    window_start = as_of - timedelta(days=90)
    generated = _media_point(
        window_start.replace(hour=0, minute=0, second=0, microsecond=0),
        article_id=910,
    )
    historical = _media_point(
        window_start - timedelta(microseconds=1),
        article_id=911,
    )
    assert generated.observed_at < window_start
    assert historical.observed_at < window_start

    monkeypatch.setattr(service, "_history", lambda *_: (historical,))
    monkeypatch.setattr(service, "build_media_observations", lambda *_: [generated])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])

    shadow = run_radar_cycle(_Session(), as_of, shadow=True)
    applied = run_radar_cycle(_Session(), as_of, shadow=False)

    for report in (shadow, applied):
        assert sum(len(wave.observations) for wave in report.country_waves) == 2
        assert report.observation_count == 1
        assert report.evidence_validity == {
            "total": 1, "valid": 1, "invalid": 0, "ratio": 1.0,
        }
        assert report.evidence_completeness == {"complete": 1, "incomplete": 0}
        assert report.country_coverage == {"ES": 1.0}

    assert shadow.observation_count == applied.observation_count
    assert shadow.evidence_validity == applied.evidence_validity
    assert applied.inserted_observations == applied.observation_count == 1


def test_replay_prefers_corrected_richer_observation_without_double_count(monkeypatch):
    import src.radar.service as service

    entity_id = UUID("00000000-0000-0000-0000-000000000099")
    common = dict(
        country_code="ES", contour="media", subject_key="event:energy",
        direction="negative", metric="attention_share", observed_at=AS_OF.replace(day=10),
        evidence_ids=("article:101", "story:202", "signal:303", f"entity:{entity_id}"),
        value=0.8, publisher_family_count=2, source_count=2, coverage_confidence=1,
        article_id=101, story_id=202,
        evidence={"article_ids": (101,), "story_ids": (202,), "signal_ids": (303,), "entity_ids": (str(entity_id),)},
    )
    pre_fix = make_observation(**common)
    corrected = make_observation(**common, signal_id=303, canonical_entity_id=entity_id)
    assert pre_fix.input_hash != corrected.input_hash
    monkeypatch.setattr(service, "_history", lambda *_: (pre_fix,))
    monkeypatch.setattr(service, "build_media_observations", lambda *_: [corrected])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])

    report = run_radar_cycle(_Session(), AS_OF, shadow=True)

    assert len(report.country_waves) == 1
    assert report.country_waves[0].observations == (corrected,)


def test_replay_prefers_current_generated_root_when_history_is_equally_rich(monkeypatch):
    import src.radar.service as service

    common = dict(
        country_code="ES", contour="media", subject_key="event:energy",
        direction="negative", metric="attention_share",
        observed_at=AS_OF.replace(day=10), value=0.8,
        publisher_family_count=2, source_count=2, coverage_confidence=1,
        article_id=101, story_id=202,
    )
    historical = make_observation(
        **common,
        signal_id=900,
        evidence_ids=("article:101", "story:202", "signal:900"),
        evidence={"article_ids": (101,), "story_ids": (202,), "signal_ids": (900,)},
    )
    historical = replace(historical, input_hash="f" * 64)
    corrected = make_observation(
        **common,
        signal_id=303,
        evidence_ids=("article:101", "story:202", "signal:303"),
        evidence={"article_ids": (101,), "story_ids": (202,), "signal_ids": (303,)},
    )
    monkeypatch.setattr(service, "_history", lambda *_: (historical,))
    monkeypatch.setattr(service, "build_media_observations", lambda *_: [corrected])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])

    report = run_radar_cycle(_Session(), AS_OF, shadow=True)

    assert report.country_waves[0].observations == (corrected,)
    assert report.country_waves[0].observations[0].signal_id == 303


def test_replay_keeps_legitimate_independent_observations(monkeypatch):
    import src.radar.service as service

    def observation(article_id):
        return make_observation(
            country_code="ES", contour="media", subject_key="event:energy",
            direction="negative", metric="attention_share", observed_at=AS_OF.replace(day=10),
            evidence_ids=(f"article:{article_id}",), value=0.8,
            publisher_family_count=2, source_count=2, coverage_confidence=1,
            article_id=article_id, evidence={"article_ids": (article_id,)},
        )

    first, second = observation(101), observation(102)
    monkeypatch.setattr(service, "build_media_observations", lambda *_: [first, second])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])

    report = run_radar_cycle(_Session(), AS_OF, shadow=True)

    assert {point.input_hash for point in report.country_waves[0].observations} == {
        first.input_hash, second.input_hash,
    }


def test_radar_apply_is_idempotent_and_preserves_t0_override(monkeypatch):
    import src.radar.service as service

    monkeypatch.setattr(service, "build_media_observations", lambda *_: [_observation()])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])
    session = _Session()

    first = run_radar_cycle(session, AS_OF, shadow=False)
    service.record_analyst_t0_override(session, first.trend_id, ANALYST_T0, "reviewed")
    before_events = len(session.radar_store["events"])
    second = run_radar_cycle(session, AS_OF, shadow=False)

    assert second.inserted_observations == 0
    assert service.trend_by_id(session, first.trend_id)["t0_effective"] == ANALYST_T0
    assert len(session.radar_store["events"]) == before_events


def test_sql_persistence_uses_wave_keys_and_reports_real_count_deltas(monkeypatch):
    import src.radar.service as service

    later = make_observation(
        country_code="ES", contour="action", subject_key="policy:sanctions:russia",
        direction="increase", metric="delta", observed_at=AS_OF.replace(day=1),
        evidence_ids=("sanctions:later",), value=3, authority="registry",
        source_count=1, coverage_confidence=1.0, evidence={"status": "verified"},
    )
    monkeypatch.setattr(service, "build_media_observations", lambda *_: [_observation(), later])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])
    session = _RecordingSqlSession()

    report = run_radar_cycle(session, AS_OF, shadow=False)

    trend_inserts = [params for sql, params in session.calls if "INSERT INTO radar_trends" in sql and "wave_key" in params]
    assert len({params["wave_key"] for params in trend_inserts}) == 2
    assert report.protected_row_counts["radar_observations"] == {"before": 0, "after": 2, "delta": 2}
    assert report.protected_row_counts["articles"]["delta"] == 0
    assert any("radar_observation_history" in sql for sql, _ in session.calls)


def test_sql_update_preserves_first_detection_timestamp():
    import src.radar.service as service

    sql = str(service._UPDATE_TREND)

    assert "detected_at = COALESCE(trend.detected_at, :detected_at)" in sql


def test_sql_evidence_copies_observation_roots_to_the_country_trend(monkeypatch):
    import src.radar.service as service

    entity_id = UUID("00000000-0000-0000-0000-000000000099")
    observation = make_observation(
        country_code="ES", contour="media", subject_key="event:energy", direction="negative",
        metric="attention_share", observed_at=AS_OF.replace(day=10), evidence_ids=("article:101",),
        value=0.8, publisher_family_count=2, source_count=2, coverage_confidence=1,
        article_id=101, story_id=202, signal_id=303, canonical_entity_id=entity_id,
        evidence={"article_ids": (101,), "story_ids": (202,), "signal_ids": (303,), "entity_ids": (str(entity_id),)},
    )
    monkeypatch.setattr(service, "build_media_observations", lambda *_: [observation])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])
    session = _RecordingSqlSession()

    run_radar_cycle(session, AS_OF, shadow=False)

    observation_write = next(params for sql, params in session.calls if "INSERT INTO radar_observations" in sql)
    evidence_sql, evidence_write = next((sql, params) for sql, params in session.calls if "INSERT INTO radar_trend_evidence" in sql)
    assert (observation_write["article_id"], observation_write["story_id"], observation_write["signal_id"], observation_write["canonical_entity_id"]) == (101, 202, 303, entity_id)
    assert evidence_write["trend_id"] == 1
    assert evidence_write["input_hash"] == observation.input_hash
    assert (evidence_write["story_id"], evidence_write["signal_id"]) == (202, 303)
    assert evidence_write["relation_kind"] == "base"
    for root in ("article_id", "canonical_entity_id"):
        assert f"observation.{root}" in evidence_sql
    for root in ("story_id", "signal_id"):
        assert f":{root}" in evidence_sql
    for root in ("article_id", "story_id", "signal_id", "canonical_entity_id"):
        assert f"COALESCE(radar_trend_evidence.{root}, EXCLUDED.{root})" in evidence_sql
    assert "ON CONFLICT (public_id) DO UPDATE" in evidence_sql
    assert ":relation_kind = 'base'" in evidence_sql
    assert "prior.trend_id = :trend_id" in evidence_sql
    assert "prior.observation_id = observation.id" in evidence_sql
    assert "role =" not in evidence_sql.split("DO UPDATE", 1)[1]
    assert "contribution =" not in evidence_sql.split("DO UPDATE", 1)[1]
    assert "radar_trend_evidence.evidence ||" in evidence_sql.split("DO UPDATE", 1)[1]
    assert "jsonb_build_object('_relation_only', true)" in evidence_sql


def test_sql_evidence_materializes_every_story_and_signal_relation(monkeypatch):
    import src.radar.service as service

    observation = make_observation(
        country_code="ES", contour="media", subject_key="event:energy", direction="negative",
        metric="attention_share", observed_at=AS_OF.replace(day=10),
        evidence_ids=("article:101", "story:202", "story:203", "signal:303", "signal:304"),
        value=0.8, publisher_family_count=2, source_count=2, coverage_confidence=1,
        article_id=101, story_id=202, signal_id=303,
        evidence={"article_ids": (101,), "story_ids": (202, 203), "signal_ids": (303, 304)},
    )
    monkeypatch.setattr(service, "build_media_observations", lambda *_: [observation])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])
    session = _RecordingSqlSession()

    report = run_radar_cycle(session, AS_OF, shadow=False)

    writes = [params for sql, params in session.calls if "INSERT INTO radar_trend_evidence" in sql]
    assert {(params["story_id"], params["signal_id"]) for params in writes} == {
        (202, 303), (203, 303), (202, 304),
    }
    assert len({params["public_id"] for params in writes}) == 3
    assert all(params["trend_id"] == 1 for params in writes)
    persisted_evidence = {
        params["relation_kind"]: json.loads(params["evidence"])
        for params in writes
    }
    assert "_relation_only" not in persisted_evidence["base"]
    assert persisted_evidence["story"]["_relation_only"] is True
    assert persisted_evidence["signal"]["_relation_only"] is True
    assert all(payload["story_ids"] == [202, 203] for payload in persisted_evidence.values())
    assert all(payload["signal_ids"] == [303, 304] for payload in persisted_evidence.values())
    assert len(report.country_waves[0].observations) == 1


def test_lifecycle_uses_persisted_confirmed_state_and_timeline():
    point = _observation()
    wave = CountryWave(
        country_code="ES", contour="action", subject_key=point.subject_key, direction=point.direction,
        observations=(point,), first_observed_at=AS_OF, t0_auto=AS_OF,
        wave_key="existing", state=TrendState.CONFIRMED, confirmed_at=ANALYST_T0,
        t0_effective=ANALYST_T0,
    )

    decision = _state_for(wave, AS_OF)

    assert decision.state is TrendState.CONFIRMED
    assert decision.timeline.confirmed_at == ANALYST_T0


def test_shadow_preserves_analyst_effective_t0_when_auto_t0_was_null(monkeypatch):
    import src.radar.service as service

    point = _media_point(AS_OF - timedelta(days=2), value=0.9, article_id=404)
    previous = CountryWave(
        country_code="ES", contour="media", subject_key=point.subject_key,
        direction=point.direction, observations=(point,),
        first_observed_at=point.observed_at,
        last_observed_at=point.observed_at,
        t0_auto=None, t0_effective=ANALYST_T0,
        wave_key="analyst-override", state=TrendState.CONFIRMED,
        detected_at=point.observed_at, confirmed_at=point.observed_at,
        has_analyst_t0_override=True,
    )
    monkeypatch.setattr(service, "_previous_waves", lambda *_: (previous,))
    monkeypatch.setattr(service, "build_media_observations", lambda *_: [point])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])

    report = run_radar_cycle(_Session(), AS_OF, shadow=True)
    [wave] = report.country_waves

    assert "radar_t0_revisions" in str(service._PRIOR_WAVES)
    assert "has_analyst_t0_override" in str(service._PRIOR_WAVES)
    assert wave.t0_auto is None
    assert wave.t0_effective == ANALYST_T0
    assert wave.has_analyst_t0_override is True
    assert report.t0_sanity["violations"] == 0


def _media_point(at, *, subject="event:energy", value=0.1, article_id=1):
    return make_observation(
        country_code="ES", contour="media", subject_key=subject,
        direction="negative", metric="attention_share", observed_at=at,
        evidence_ids=(f"article:{article_id}",), value=value,
        publisher_family_count=2, source_count=2, coverage_confidence=1.0,
        article_id=article_id,
        evidence={
            "article_ids": (article_id,),
            "alignment_subject": "energy:imports:russia",
            "alignment_direction": "hardening",
        },
    )


def test_cycle_runs_real_baseline_and_refines_t0_after_confirmation(monkeypatch):
    import src.radar.service as service

    start = AS_OF - timedelta(days=89)
    observations = [
        _media_point(
            start + timedelta(days=offset),
            value=0.05 if offset < 63 else 0.9,
            article_id=1000 + offset,
        )
        for offset in range(89)
    ]
    monkeypatch.setattr(service, "build_media_observations", lambda *_: observations)
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])

    report = run_radar_cycle(_Session(), AS_OF, shadow=True)

    [wave] = report.country_waves
    assert wave.state is TrendState.CONFIRMED
    assert wave.baseline is not None
    assert wave.baseline.window_days == 90
    assert wave.baseline.acceleration_days == 7
    assert wave.detected_at == observations[63].observed_at
    assert wave.confirmed_at == observations[63].observed_at
    assert wave.t0_auto is not None
    assert start < wave.t0_auto < AS_OF
    assert wave.velocity > 0


def test_replay_detection_time_is_first_historical_emerging_cutoff(monkeypatch):
    import src.radar.service as service

    low = _media_point(AS_OF - timedelta(days=20), value=0.1, article_id=901)
    first_emerging = _media_point(
        AS_OF - timedelta(days=10), value=0.8, article_id=902,
    )
    confirming = _media_point(
        AS_OF - timedelta(days=9), value=0.9, article_id=903,
    )
    monkeypatch.setattr(
        service, "build_media_observations",
        lambda *_: [low, first_emerging, confirming],
    )
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])

    report = run_radar_cycle(_Session(), AS_OF, shadow=True)

    [wave] = report.country_waves
    assert wave.detected_at == first_emerging.observed_at
    assert wave.detected_at != AS_OF


def test_new_media_wave_cannot_confirm_without_automatic_t0(monkeypatch):
    import src.radar.service as service

    points = [
        _media_point(AS_OF - timedelta(days=2), value=0.8, article_id=911),
        _media_point(AS_OF - timedelta(days=1), value=0.9, article_id=912),
    ]
    monkeypatch.setattr(service, "build_media_observations", lambda *_: points)
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])

    report = run_radar_cycle(_Session(), AS_OF, shadow=True)

    [wave] = report.country_waves
    assert wave.state is TrendState.EMERGING
    assert wave.confirmed_at is None
    assert wave.t0_auto is None
    assert wave.lifecycle_reason == "automatic_t0_unresolved"
    assert report.t0_sanity["violations"] == 0


def test_cycle_json_report_has_exact_release_metrics_from_vertical_slice(monkeypatch):
    import src.radar.service as service

    media_first = _media_point(
        AS_OF - timedelta(days=4), value=0.8, article_id=701,
    )
    media_second = _media_point(
        AS_OF - timedelta(days=3), value=0.9, article_id=702,
    )
    action = make_observation(
        country_code="ES", contour="action",
        subject_key="energy:imports:russia", direction="decrease",
        metric="import_value_delta", observed_at=AS_OF - timedelta(days=2),
        evidence_ids=("fossil:701",), value=-10, authority="registry",
        source_count=1, coverage_confidence=1.0,
        evidence={
            "dataset": "ru_fossil_imports", "source_id": "fossil:701",
            "alignment_subject": "energy:imports:russia",
            "alignment_direction": "hardening",
        },
    )
    def invalid_point(days_ago, suffix):
        return make_observation(
            country_code="PT", contour="media", subject_key="event:invalid",
            direction="negative", metric="attention_share",
            observed_at=AS_OF - timedelta(days=days_ago),
            evidence_ids=(f"opaque:{suffix}",), value=0.9,
            publisher_family_count=2, source_count=2,
            coverage_confidence=1.0,
            evidence={
                "alignment_subject": "russia:general",
                "alignment_direction": "hardening",
            },
        )
    invalid_first = invalid_point(2, "first")
    invalid_second = invalid_point(1, "second")
    previous_media = CountryWave(
        "ES", "media", media_first.subject_key, media_first.direction,
        (media_first,), media_first.observed_at, media_first.observed_at,
        wave_key="existing-media", state=TrendState.CONFIRMED,
        detected_at=media_first.observed_at,
        confirmed_at=media_second.observed_at,
        t0_effective=media_first.observed_at,
    )
    previous_media = replace(previous_media, t0_auto=media_first.observed_at)
    previous_action = CountryWave(
        "ES", "action", action.subject_key, action.direction,
        (action,), action.observed_at, action.observed_at,
        wave_key="existing-action", state=TrendState.CONFIRMED,
        detected_at=action.observed_at, confirmed_at=action.observed_at,
        t0_effective=action.observed_at,
    )
    monkeypatch.setattr(
        service, "_previous_waves", lambda *_: (previous_media, previous_action),
    )
    monkeypatch.setattr(
        service, "build_media_observations",
        lambda *_: [media_first, media_second, invalid_first, invalid_second],
    )
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [action])

    payload = run_radar_cycle(_Session(), AS_OF, shadow=True).json_report()

    all_states = {
        "candidate": 0, "emerging": 0, "confirmed": 0,
        "cooling": 0, "resolved": 0, "rejected": 0,
    }
    assert payload["as_of"] == AS_OF.isoformat()
    assert payload["shadow"] is True
    assert payload["detector_version"] == "radar-wave-1"
    assert payload["lookback_days"] == 90
    assert payload["observation_count"] == 5
    assert payload["country_trend_count"] == 3
    assert payload["meta_trend_count"] == 3
    assert payload["country_state_counts"] == {
        **all_states, "candidate": 1, "confirmed": 2,
    }
    assert payload["meta_state_counts"] == {
        **all_states, "candidate": 1, "emerging": 2,
    }
    assert payload["evidence_validity"] == {
        "total": 5, "valid": 3, "invalid": 2, "ratio": 0.6,
    }
    assert payload["collector_suppressed"] == 1
    assert payload["confirmed_below_coverage_gate"] == 0
    assert payload["t0_sanity"] == {
        "automatic": 4, "unresolved": 2, "violations": 0,
    }
    assert payload["contour_completeness"] == {
        "identity_candidate_pairs": 1,
        "possible_pairs": 1, "linked_pairs": 1, "ratio": 1.0,
    }


def test_cycle_zero_fills_only_a_healthy_collection_day(monkeypatch):
    import src.radar.service as service

    first = AS_OF - timedelta(days=4)
    target = [
        _media_point(first, article_id=1),
        _media_point(first + timedelta(days=2), article_id=2),
    ]
    healthy_other_subject = _media_point(
        first + timedelta(days=1), subject="event:trade", article_id=3,
    )
    monkeypatch.setattr(
        service, "build_media_observations",
        lambda *_: [*target, healthy_other_subject],
    )
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])
    monkeypatch.setattr(
        service,
        "_media_collection_health",
        lambda *_: {
            "ES": {service._bucket_index(first + timedelta(days=1), AS_OF)},
        },
    )

    report = run_radar_cycle(_Session(), AS_OF, shadow=True)
    wave = next(item for item in report.country_waves if item.subject_key == "event:energy")
    points = {
        point.at.date(): point
        for point in wave.baseline.dense_points
        if point is not None
    }

    assert points[(first + timedelta(days=1)).date()].volume == 0
    assert wave.baseline.dense_points[-1] is None


def test_observation_does_not_imply_healthy_collection_without_explicit_telemetry():
    import src.radar.service as service

    action = make_observation(
        country_code="ES", contour="action",
        subject_key="diplomacy:un_alignment:russia", direction="hardening",
        metric="alignment_delta", observed_at=AS_OF - timedelta(days=1),
        evidence_ids=("un_vote:1",), authority="registry", value=1.0,
        evidence={"dataset": "un_votes"},
    )

    assert service._healthy_collection_buckets((action,), AS_OF) == {}


def test_collection_gap_cannot_manufacture_cooling(monkeypatch):
    import src.radar.service as service

    observation = _media_point(AS_OF - timedelta(days=10), article_id=7)
    previous = CountryWave(
        country_code="ES", contour="media", subject_key=observation.subject_key,
        direction=observation.direction, observations=(),
        first_observed_at=observation.observed_at,
        last_observed_at=observation.observed_at,
        t0_auto=observation.observed_at, t0_effective=observation.observed_at,
        wave_key="existing", state=TrendState.CONFIRMED,
        detected_at=AS_OF - timedelta(days=12),
        confirmed_at=AS_OF - timedelta(days=11),
    )
    monkeypatch.setattr(service, "_previous_waves", lambda *_: (previous,))
    monkeypatch.setattr(service, "build_media_observations", lambda *_: [observation])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])

    report = run_radar_cycle(_Session(), AS_OF, shadow=True)

    [wave] = report.country_waves
    assert wave.state is TrendState.CONFIRMED
    assert wave.lifecycle_reason == "collection_gap"
    assert wave.detected_at == previous.detected_at
    assert wave.confirmed_at == previous.confirmed_at


def test_independent_media_health_uses_verified_non_duplicate_country_articles():
    import src.radar.service as service

    def health_row(day, publisher_id, domain):
        return {
            "article_id": day * 100 + publisher_id,
            "country_code": "ES",
            "healthy_day": AS_OF - timedelta(days=90 - day),
            "publisher_id": publisher_id,
            "publisher_url": f"https://{domain}/rss",
            "publisher_config": {"publisher_domain": domain},
        }

    class _HealthSession:
        def __init__(self):
            self.sql = ""

        def execute(self, statement, params=None):
            self.sql = str(statement)
            return _Result(rows=[
                health_row(day, publisher_id, domain)
                for day in (0, 1, 2, 89)
                for publisher_id, domain in (
                    (1, "diario.example"), (2, "radio.example"),
                )
            ])

    session = _HealthSession()
    window = ObservationWindow(AS_OF - timedelta(days=90), AS_OF)

    health = service._media_collection_health(session, window)

    assert health == {"ES": {0, 1, 2, 89}}
    assert "article_country_facts" in session.sql
    assert "JOIN sources" in session.sql
    assert "articles" in session.sql
    assert "is_duplicate = FALSE" in session.sql
    assert "is_backfill = FALSE" in session.sql
    assert "collected_at <= article.published_at + INTERVAL '24 hours'" in session.sql
    assert "article.collected_at < :window_end" in session.sql
    assert "AT TIME ZONE 'UTC'" in session.sql
    assert "analysis" not in session.sql
    assert "is_relevant" not in session.sql


@pytest.mark.parametrize(
    "publishers",
    (
        ((1, "solo.example"),),
        ((1, "shared.example"), (2, "shared.example")),
    ),
)
def test_media_health_rejects_single_source_or_single_publisher_family(publishers):
    import src.radar.service as service

    rows = [
        {
            "article_id": day * 100 + publisher_id,
            "country_code": "ES",
            "healthy_day": AS_OF - timedelta(days=90 - day),
            "publisher_id": publisher_id,
            "publisher_url": f"https://feed-{publisher_id}.example/rss",
            "publisher_config": {"publisher_domain": domain},
        }
        for day in (0, 1, 2, 89)
        for publisher_id, domain in publishers
    ]

    class _HealthSession:
        def execute(self, statement, params=None):
            return _Result(rows=rows)

    window = ObservationWindow(AS_OF - timedelta(days=90), AS_OF)

    assert service._media_collection_health(_HealthSession(), window) == {}


def test_media_health_rejects_day_below_historical_source_breadth():
    import src.radar.service as service

    rows = []
    for day, count in ((0, 4), (1, 4), (2, 4), (89, 2)):
        for publisher_id in range(1, count + 1):
            rows.append({
                "article_id": day * 100 + publisher_id,
                "country_code": "ES",
                "healthy_day": AS_OF - timedelta(days=90 - day),
                "publisher_id": publisher_id,
                "publisher_url": f"https://publisher-{publisher_id}.example/rss",
                "publisher_config": {
                    "publisher_domain": f"publisher-{publisher_id}.example",
                },
            })

    class _HealthSession:
        def execute(self, statement, params=None):
            return _Result(rows=rows)

    window = ObservationWindow(AS_OF - timedelta(days=90), AS_OF)
    health = service._media_collection_health(_HealthSession(), window)

    assert health == {"ES": {0, 1, 2}}


def _stale_previous_media(state, days_ago):
    observed_at = AS_OF - timedelta(days=days_ago)
    return CountryWave(
        country_code="ES", contour="media", subject_key="event:stale",
        direction="negative", observations=(),
        first_observed_at=observed_at, last_observed_at=observed_at,
        t0_auto=observed_at, t0_effective=observed_at,
        wave_key="stale-wave", state=state,
        detected_at=observed_at, confirmed_at=observed_at,
    )


def test_unmatched_previous_media_wave_cools_with_continuous_country_health(monkeypatch):
    import src.radar.service as service

    previous = _stale_previous_media(TrendState.CONFIRMED, 8)
    monkeypatch.setattr(service, "_previous_waves", lambda *_: (previous,))
    monkeypatch.setattr(service, "build_media_observations", lambda *_: [])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])
    monkeypatch.setattr(
        service, "_media_collection_health", lambda *_: {"ES": set(range(83, 90))},
    )

    report = run_radar_cycle(_Session(), AS_OF, shadow=True)

    [wave] = report.country_waves
    assert wave.wave_key == previous.wave_key
    assert wave.state is TrendState.COOLING
    assert wave.lifecycle_reason == "quiet_window_elapsed"


def test_unmatched_previous_media_wave_resolves_after_thirty_healthy_days(monkeypatch):
    import src.radar.service as service

    previous = _stale_previous_media(TrendState.COOLING, 31)
    monkeypatch.setattr(service, "_previous_waves", lambda *_: (previous,))
    monkeypatch.setattr(service, "build_media_observations", lambda *_: [])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])
    monkeypatch.setattr(
        service, "_media_collection_health", lambda *_: {"ES": set(range(60, 90))},
    )

    [wave] = run_radar_cycle(_Session(), AS_OF, shadow=True).country_waves

    assert wave.state is TrendState.RESOLVED
    assert wave.lifecycle_reason == "quiet_window_elapsed"


def test_unmatched_previous_media_wave_stays_confirmed_without_collection_health(monkeypatch):
    import src.radar.service as service

    previous = _stale_previous_media(TrendState.CONFIRMED, 8)
    monkeypatch.setattr(service, "_previous_waves", lambda *_: (previous,))
    monkeypatch.setattr(service, "build_media_observations", lambda *_: [])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])
    monkeypatch.setattr(service, "_media_collection_health", lambda *_: {})

    [wave] = run_radar_cycle(_Session(), AS_OF, shadow=True).country_waves

    assert wave.state is TrendState.CONFIRMED
    assert wave.lifecycle_reason == "collection_gap"


def test_analyst_meta_t0_override_is_append_only():
    class _OverrideSession:
        def __init__(self):
            self.calls = []

        def execute(self, statement, params=None):
            self.calls.append((str(statement), params or {}))
            if "SELECT t0_effective" in str(statement):
                return _Result(rows=[{"t0_effective": AS_OF}])
            return _Result(rowcount=1)

    session = _OverrideSession()
    record_analyst_t0_override(session, 99, ANALYST_T0, "reviewed")

    assert any("INSERT INTO radar_t0_revisions" in sql and params["trend_id"] == 99 for sql, params in session.calls)
    assert any("UPDATE radar_trends SET t0_effective" in sql for sql, _ in session.calls)


def test_analyst_t0_override_rejects_future_or_post_detection_time():
    session = _Session()
    session.radar_store["trends"].append({
        "id": 99,
        "t0_effective": ANALYST_T0,
        "detected_at": AS_OF - timedelta(days=2),
        "confirmed_at": AS_OF - timedelta(days=1),
    })

    with pytest.raises(ValueError, match="not be later"):
        record_analyst_t0_override(
            session, 99, AS_OF + timedelta(days=1), "future override",
        )


def _episode_wave(contour, at, wave_key):
    observation = make_observation(
        country_code="ES", contour=contour, subject_key="event:energy", direction="increase",
        metric="delta", observed_at=at, evidence_ids=(f"{contour}:{wave_key}",),
        value=1, authority="registry" if contour == "action" else None,
        source_count=2, coverage_confidence=1, publisher_family_count=2,
        evidence={"story_ids": (1,)},
    )
    return CountryWave("ES", contour, "event:energy", "increase", (observation,), at, at, wave_key=wave_key)


def test_sql_persistence_keeps_anchor_separated_meta_keys():
    session = _RecordingSqlSession()
    first = _episode_wave("media", AS_OF, "first")
    second = _episode_wave("media", AS_OF, "second")
    metas = (
        MetaTrend("media:coverage", "increase", (first,), AS_OF, meta_key="anchor:story:1:energy:increase"),
        MetaTrend("media:coverage", "increase", (second,), AS_OF, meta_key="anchor:story:2:trade:increase"),
    )
    wave_ids = {
        (first.country_code, first.contour, first.subject_key, first.direction, first.wave_key): 10,
        (second.country_code, second.contour, second.subject_key, second.direction, second.wave_key): 11,
    }

    _persist_meta_and_contours(session, metas, wave_ids, AS_OF)

    meta_inserts = [params for sql, params in session.calls if "INSERT INTO radar_trends" in sql]
    assert {params["meta_key"] for params in meta_inserts} == {
        "anchor:story:1:energy:increase", "anchor:story:2:trade:increase",
    }


def test_incremental_meta_persistence_does_not_close_unrelated_memberships():
    session = _RecordingSqlSession()

    _persist_meta_and_contours(
        session, (), {}, AS_OF, incremental=True,
    )

    assert not any(
        "UPDATE radar_trend_members" in sql
        for sql, _params in session.calls
    )


def test_meta_persistence_uses_member_detection_and_second_country_confirmation():
    import src.radar.service as service

    first = replace(
        _episode_wave("media", AS_OF - timedelta(days=5), "first-country"),
        country_code="ES", state=TrendState.CONFIRMED,
        detected_at=AS_OF - timedelta(days=8),
        confirmed_at=AS_OF - timedelta(days=5),
    )
    second = replace(
        _episode_wave("media", AS_OF - timedelta(days=3), "second-country"),
        country_code="PT", state=TrendState.CONFIRMED,
        detected_at=AS_OF - timedelta(days=6),
        confirmed_at=AS_OF - timedelta(days=3),
    )
    meta = MetaTrend(
        "event:energy", "increase", (first, second), first.t0_auto,
        meta_key="member-timeline",
    )
    wave_ids = {
        (wave.country_code, wave.contour, wave.subject_key, wave.direction, wave.wave_key): trend_id
        for trend_id, wave in enumerate((first, second), 10)
    }
    session = _RecordingSqlSession()

    _persist_meta_and_contours(session, (meta,), wave_ids, AS_OF)

    insert = next(
        params for sql, params in session.calls
        if "INSERT INTO radar_trends" in sql and params.get("meta_key") == "member-timeline"
    )
    assert insert["detected_at"] == first.detected_at
    assert insert["confirmed_at"] == second.confirmed_at
    assert "detected_at = CASE WHEN :reopening THEN :detected_at" in str(service._UPDATE_META)


def test_contour_alignment_pairs_each_recurrence_episode_separately():
    session = _RecordingSqlSession()
    first_media = _episode_wave("media", AS_OF.replace(day=1), "media-1")
    first_action = _episode_wave("action", AS_OF.replace(day=2), "action-1")
    second_media = _episode_wave("media", AS_OF.replace(day=25), "media-2")
    second_action = _episode_wave("action", AS_OF.replace(day=26), "action-2")
    meta = MetaTrend("event:energy", "increase", (first_media, first_action, second_media, second_action), AS_OF, meta_key="canonical:event:energy:increase")
    waves = (first_media, first_action, second_media, second_action)
    wave_ids = {(wave.country_code, wave.contour, wave.subject_key, wave.direction, wave.wave_key): index for index, wave in enumerate(waves, 1)}

    _persist_meta_and_contours(session, (meta,), wave_ids, AS_OF)

    links = [params for sql, params in session.calls if "INSERT INTO radar_contour_links" in sql]
    assert {(params["media_trend_id"], params["action_trend_id"]) for params in links} == {(1, 2), (3, 4)}


def test_contour_alignment_uses_shared_evidence_identity_across_local_keys():
    session = _RecordingSqlSession()
    media_observation = _media_point(AS_OF - timedelta(days=2), article_id=88)
    action_observation = make_observation(
        country_code="ES", contour="action",
        subject_key="energy:imports:russia", direction="decrease",
        metric="import_value_delta", observed_at=AS_OF - timedelta(days=1),
        evidence_ids=("fossil:88",), value=-10, authority="registry",
        source_count=1, coverage_confidence=1.0,
        evidence={
            "alignment_subject": "energy:imports:russia",
            "alignment_direction": "hardening",
        },
    )
    media = CountryWave(
        "ES", "media", media_observation.subject_key,
        media_observation.direction, (media_observation,),
        media_observation.observed_at, media_observation.observed_at,
        wave_key="media-aligned",
    )
    action = CountryWave(
        "ES", "action", action_observation.subject_key,
        action_observation.direction, (action_observation,),
        action_observation.observed_at, action_observation.observed_at,
        wave_key="action-aligned",
    )
    metas = (
        MetaTrend(media.subject_key, media.direction, (media,), media.t0_auto, meta_key="media-meta"),
        MetaTrend(action.subject_key, action.direction, (action,), action.t0_auto, meta_key="action-meta"),
    )
    wave_ids = {
        (wave.country_code, wave.contour, wave.subject_key, wave.direction, wave.wave_key): trend_id
        for wave, trend_id in ((media, 1), (action, 2))
    }

    _persist_meta_and_contours(session, metas, wave_ids, AS_OF)

    links = [params for sql, params in session.calls if "INSERT INTO radar_contour_links" in sql]
    assert [(params["media_trend_id"], params["action_trend_id"]) for params in links] == [(1, 2)]
    assert json.loads(links[0]["evidence"])["direction"] == "hardening"


def test_real_adapter_alignment_is_persisted_on_both_country_trends():
    class _AdapterResult:
        def __init__(self, rows=(), scalar_value=False):
            self.rows = list(rows)
            self.scalar_value = scalar_value

        def fetchall(self):
            return self.rows

        def scalar(self):
            return self.scalar_value

    class _AdapterSession:
        def execute(self, statement, params=None):
            sql = str(statement)
            if "radar_registered_country_codes" in sql:
                return _AdapterResult((SimpleNamespace(code="ES"),))
            if "FROM articles" in sql:
                return _AdapterResult((SimpleNamespace(
                    article_id=501, published_at=AS_OF - timedelta(hours=2),
                    is_duplicate=False, country_code="ES", publisher_id=10,
                    publisher_name="Diario", publisher_url="https://diario.es",
                    publisher_config={"publisher_domain": "diario.es"},
                    story_id=77, event_key="russia fossil import energy debate", sentiment=-0.5,
                    is_relevant=True, topics=("energy",),
                    story_event_keys=("russia-fossil-import-energy-debate",), entity_ids=(), signal_ids=(),
                ),))
            if "FROM ru_fossil_imports" in sql:
                return _AdapterResult((SimpleNamespace(
                    country_code="ES", current_value=Decimal("90"),
                    previous_value=Decimal("100"), value=Decimal("-10"),
                    observed_at=AS_OF - timedelta(hours=1),
                ),))
            return _AdapterResult()

    window = ObservationWindow(AS_OF - timedelta(days=1), AS_OF)
    adapter_session = _AdapterSession()
    [media] = build_media_observations(adapter_session, window)
    actions = build_action_observations(adapter_session, window)
    [action] = [point for point in actions if point.subject_key == "energy:imports:russia"]
    media_wave = CountryWave(
        "ES", "media", media.subject_key, media.direction, (media,),
        media.observed_at, media.observed_at, wave_key="media-real",
    )
    action_wave = CountryWave(
        "ES", "action", action.subject_key, action.direction, (action,),
        action.observed_at, action.observed_at, wave_key="action-real",
    )
    metas = (
        MetaTrend(media.subject_key, media.direction, (media_wave,), media_wave.t0_auto, meta_key="media-real-meta"),
        MetaTrend(action.subject_key, action.direction, (action_wave,), action_wave.t0_auto, meta_key="action-real-meta"),
    )
    persistence = _RecordingSqlSession()

    _persist_sql(
        persistence, [media, action], (media_wave, action_wave), metas, AS_OF,
    )

    inserts = [
        (sql, params) for sql, params in persistence.calls
        if "INSERT INTO radar_trends" in sql and "wave_key" in params
    ]
    assert len(inserts) == 2
    assert {params["alignment_subject"] for _, params in inserts} == {"energy:imports:russia"}
    assert {params["alignment_direction"] for _, params in inserts} == {"hardening"}
    assert {params["subject_key"] for _, params in inserts} == {
        media.subject_key, action.subject_key,
    }
    assert {params["direction"] for _, params in inserts} == {
        media.direction, action.direction,
    }
    assert all("alignment_subject" in sql and "alignment_direction" in sql for sql, _ in inserts)
    assert any("INSERT INTO radar_contour_links" in sql for sql, _ in persistence.calls)


def test_meta_persistence_closes_stale_members_before_reopening_current_ones():
    session = _RecordingSqlSession()
    wave = _episode_wave("media", AS_OF, "current")
    meta = MetaTrend("event:energy", "increase", (wave,), AS_OF, meta_key="current-meta")
    wave_ids = {(wave.country_code, wave.contour, wave.subject_key, wave.direction, wave.wave_key): 10}

    _persist_meta_and_contours(session, (meta,), wave_ids, AS_OF)

    close_sql = next(sql for sql, _ in session.calls if "UPDATE radar_trend_members member" in sql)
    member_sql = next(sql for sql, _ in session.calls if "INSERT INTO radar_trend_members" in sql)
    assert "member.left_at IS NULL" in close_sql
    assert "left_at = NULL" in member_sql


def test_incremental_meta_refresh_closes_only_stale_members_of_current_meta():
    class _ExistingMetaSession(_RecordingSqlSession):
        def execute(self, statement, params=None):
            sql = str(statement)
            if "scope = 'meta'" in sql and "has_analyst_t0_override" in sql:
                self.calls.append((sql, params or {}))
                return _Result(rows=[{
                    "id": 77,
                    "state": TrendState.EMERGING.value,
                    "confirmed_at": None,
                    "t0_auto": AS_OF,
                    "t0_effective": AS_OF,
                    "meta_key": "current-meta",
                }])
            return super().execute(statement, params)

    first = replace(_episode_wave("media", AS_OF, "first"), country_code="ES")
    second = replace(_episode_wave("media", AS_OF, "second"), country_code="PT")
    meta = MetaTrend(
        "event:energy", "increase", (first, second), AS_OF,
        meta_key="current-meta",
    )
    wave_ids = {
        (wave.country_code, wave.contour, wave.subject_key, wave.direction, wave.wave_key): trend_id
        for trend_id, wave in zip((901, 902), (first, second), strict=True)
    }
    session = _ExistingMetaSession()

    _persist_meta_and_contours(
        session, (meta,), wave_ids, AS_OF, incremental=True,
    )

    sql, params = next(
        (sql, params) for sql, params in session.calls
        if "UPDATE radar_trend_members" in sql
        and "meta_trend_id = :meta_id" in sql
    )
    assert params == {
        "meta_id": 77,
        "active_country_ids": [901, 902],
        "as_of": AS_OF,
    }
    assert "detector_version" not in params


def test_meta_persistence_uses_prior_state_to_prevent_lifecycle_regression():
    class _ExistingMetaSession(_RecordingSqlSession):
        def execute(self, statement, params=None):
            sql = str(statement)
            if "scope = 'meta'" in sql and "has_analyst_t0_override" in sql:
                self.calls.append((sql, params or {}))
                return _Result(rows=[{
                    "id": 99,
                    "state": TrendState.CONFIRMED.value,
                    "confirmed_at": AS_OF - timedelta(days=2),
                    "t0_auto": AS_OF - timedelta(days=3),
                    "t0_effective": AS_OF - timedelta(days=3),
                    "meta_key": "meta-cooling",
                }])
            return super().execute(statement, params)

    confirmed = replace(
        _episode_wave("media", AS_OF, "confirmed"),
        country_code="ES", state=TrendState.CONFIRMED,
    )
    cooling = replace(
        _episode_wave("media", AS_OF, "cooling"),
        country_code="PT", state=TrendState.COOLING,
    )
    meta = MetaTrend(
        "event:energy", "increase", (confirmed, cooling), AS_OF,
        meta_key="meta-cooling",
    )
    wave_ids = {
        (wave.country_code, wave.contour, wave.subject_key, wave.direction, wave.wave_key): trend_id
        for trend_id, wave in enumerate((confirmed, cooling), 10)
    }
    session = _ExistingMetaSession()

    _persist_meta_and_contours(session, (meta,), wave_ids, AS_OF)

    update = next(
        params for sql, params in session.calls
        if "UPDATE radar_trends trend SET" in sql and params.get("id") == 99
    )
    assert update["state"] == TrendState.COOLING.value
    transition = next(
        params for sql, params in session.calls
        if "INSERT INTO radar_state_events" in sql and params.get("trend_id") == 99
    )
    assert (transition["from_state"], transition["to_state"]) == (
        TrendState.CONFIRMED.value, TrendState.COOLING.value,
    )


def test_meta_reopen_resets_episode_timeline_and_ignores_old_analyst_t0():
    import src.radar.service as service

    class _ResolvedMetaSession(_RecordingSqlSession):
        def execute(self, statement, params=None):
            sql = str(statement)
            if "scope = 'meta'" in sql and "has_analyst_t0_override" in sql:
                self.calls.append((sql, params or {}))
                return _Result(rows=[{
                    "id": 99,
                    "state": TrendState.RESOLVED.value,
                    "confirmed_at": AS_OF - timedelta(days=40),
                    "t0_auto": AS_OF - timedelta(days=45),
                    "t0_effective": AS_OF - timedelta(days=46),
                    "has_analyst_t0_override": True,
                    "meta_key": "canonical:event:energy:increase",
                }])
            return super().execute(statement, params)

    new_t0 = AS_OF - timedelta(days=4)
    waves = tuple(
        replace(
            _episode_wave("media", AS_OF - timedelta(days=3), f"reopen-{country}"),
            country_code=country,
            state=TrendState.CONFIRMED,
            detected_at=AS_OF - timedelta(days=3),
            confirmed_at=AS_OF - timedelta(days=2),
            t0_auto=new_t0,
            t0_effective=new_t0,
        )
        for country in ("ES", "PT")
    )
    meta = MetaTrend(
        "event:energy", "increase", waves, new_t0,
        meta_key="canonical:event:energy:increase",
    )
    wave_ids = {
        (wave.country_code, wave.contour, wave.subject_key, wave.direction, wave.wave_key): index
        for index, wave in enumerate(waves, 10)
    }
    session = _ResolvedMetaSession()

    _persist_meta_and_contours(session, (meta,), wave_ids, AS_OF)

    update = next(
        params for sql, params in session.calls
        if "UPDATE radar_trends trend SET" in sql and params.get("id") == 99
    )
    assert update["reopening"] is True
    assert update["detected_at"] == AS_OF - timedelta(days=3)
    assert update["confirmed_at"] == AS_OF - timedelta(days=2)
    assert update["t0_auto"] == new_t0
    assert update["t0_effective"] == new_t0
    update_sql = str(service._UPDATE_META)
    assert "WHEN :reopening THEN :t0_effective" in update_sql
    assert "FROM radar_state_events event" in update_sql


def test_meta_cooling_preserves_last_automatic_t0():
    import src.radar.service as service

    class _ConfirmedMetaSession(_RecordingSqlSession):
        def execute(self, statement, params=None):
            sql = str(statement)
            if "scope = 'meta'" in sql and "has_analyst_t0_override" in sql:
                self.calls.append((sql, params or {}))
                return _Result(rows=[{
                    "id": 99,
                    "state": TrendState.CONFIRMED.value,
                    "confirmed_at": AS_OF - timedelta(days=8),
                    "t0_auto": AS_OF - timedelta(days=10),
                    "t0_effective": AS_OF - timedelta(days=10),
                    "meta_key": "meta-cooling-preserve-t0",
                }])
            return super().execute(statement, params)

    wave = replace(
        _episode_wave("media", AS_OF - timedelta(days=8), "cooling-preserve"),
        state=TrendState.COOLING,
    )
    meta = MetaTrend(
        "event:energy", "increase", (wave,), None,
        meta_key="meta-cooling-preserve-t0",
    )
    wave_ids = {
        (wave.country_code, wave.contour, wave.subject_key, wave.direction, wave.wave_key): 10,
    }
    session = _ConfirmedMetaSession()

    _persist_meta_and_contours(session, (meta,), wave_ids, AS_OF)

    update = next(
        params for sql, params in session.calls
        if "UPDATE radar_trends trend SET" in sql and params.get("id") == 99
    )
    assert update["state"] == TrendState.COOLING.value
    assert update["reopening"] is False
    assert update["t0_auto"] is None
    assert "ELSE COALESCE(:t0_auto, trend.t0_auto)" in str(service._UPDATE_META)


@pytest.mark.parametrize(
    ("states", "countries", "expected"),
    (
        ((TrendState.CONFIRMED, TrendState.CONFIRMED), ("ES", "PT"), TrendState.CONFIRMED),
        ((TrendState.CONFIRMED,), ("ES",), TrendState.EMERGING),
        ((TrendState.EMERGING,), ("ES",), TrendState.EMERGING),
        ((TrendState.COOLING, TrendState.CANDIDATE), ("ES", "PT"), TrendState.COOLING),
        ((TrendState.RESOLVED, TrendState.RESOLVED), ("ES", "PT"), TrendState.RESOLVED),
        ((TrendState.REJECTED, TrendState.REJECTED), ("ES", "PT"), TrendState.REJECTED),
        ((TrendState.CANDIDATE,), ("ES",), TrendState.CANDIDATE),
    ),
)
def test_meta_lifecycle_exposes_early_and_terminal_states(states, countries, expected):
    import src.radar.service as service

    waves = tuple(
        replace(
            _episode_wave("media", AS_OF, f"wave-{index}"),
            state=state,
            country_code=country,
        )
        for index, (state, country) in enumerate(zip(states, countries), 1)
    )
    meta = MetaTrend(
        "event:energy", "increase", waves, AS_OF,
        meta_key="meta-lifecycle",
    )

    assert service._meta_state(meta) is expected


def test_meta_lifecycle_cools_when_confirmed_members_start_cooling():
    import src.radar.service as service

    confirmed = replace(
        _episode_wave("media", AS_OF, "confirmed"),
        country_code="ES", state=TrendState.CONFIRMED,
    )
    cooling = replace(
        _episode_wave("media", AS_OF, "cooling"),
        country_code="PT", state=TrendState.COOLING,
    )
    meta = MetaTrend(
        "event:energy", "increase", (confirmed, cooling), AS_OF,
        meta_key="meta-cooling",
    )

    assert service._meta_state(meta, TrendState.CONFIRMED) is TrendState.COOLING


def test_meta_lifecycle_does_not_regress_after_cooling():
    import src.radar.service as service

    emerging = replace(
        _episode_wave("media", AS_OF, "emerging"),
        state=TrendState.EMERGING,
    )
    meta = MetaTrend(
        "event:energy", "increase", (emerging,), None,
        meta_key="meta-cooling",
    )

    assert service._meta_state(meta, TrendState.COOLING) is TrendState.COOLING


def test_terminal_meta_reopens_when_two_countries_confirm_a_new_wave():
    import src.radar.service as service

    waves = tuple(
        replace(
            _episode_wave("media", AS_OF, f"confirmed-{country}"),
            country_code=country,
            state=TrendState.CONFIRMED,
            detected_at=AS_OF - timedelta(days=2),
            confirmed_at=AS_OF - timedelta(days=1),
        )
        for country in ("ES", "PT")
    )
    meta = MetaTrend(
        "event:energy", "increase", waves, AS_OF,
        meta_key="canonical:event:energy:increase",
    )

    assert service._meta_state(meta, TrendState.RESOLVED) is TrendState.CONFIRMED


def test_confirmed_meta_accepts_independent_member_analyst_t0_overrides():
    import src.radar.service as service

    waves = tuple(
        replace(
            _episode_wave("media", AS_OF - timedelta(days=2), f"override-{country}"),
            country_code=country,
            state=TrendState.CONFIRMED,
            detected_at=AS_OF - timedelta(days=2),
            confirmed_at=AS_OF - timedelta(days=1),
            t0_auto=None,
            t0_effective=ANALYST_T0,
            has_analyst_t0_override=True,
        )
        for country in ("ES", "PT")
    )
    meta = MetaTrend(
        "event:energy", "increase", waves, None,
        meta_key="canonical:event:energy:increase",
    )

    sanity = service._t0_sanity(
        waves, (meta,), (TrendState.CONFIRMED,),
    )

    assert sanity["violations"] == 0


def test_t0_sanity_rejects_chronologically_impossible_analyst_override():
    import src.radar.service as service

    wave = replace(
        _episode_wave("media", AS_OF - timedelta(days=2), "future-t0"),
        state=TrendState.CONFIRMED,
        detected_at=AS_OF - timedelta(days=2),
        confirmed_at=AS_OF - timedelta(days=1),
        t0_auto=None,
        t0_effective=AS_OF + timedelta(days=1),
        has_analyst_t0_override=True,
    )

    sanity = service._t0_sanity((wave,), (), as_of=AS_OF)

    assert sanity["violations"] == 1


def test_persisted_meta_analyst_override_is_honoured_by_release_sanity():
    import src.radar.service as service

    waves = tuple(
        replace(
            _episode_wave("media", AS_OF - timedelta(days=2), f"persisted-{country}"),
            country_code=country,
            state=TrendState.CONFIRMED,
            detected_at=AS_OF - timedelta(days=2),
            confirmed_at=AS_OF - timedelta(days=1),
            t0_auto=ANALYST_T0,
            t0_effective=ANALYST_T0,
        )
        for country in ("ES", "PT")
    )
    meta = MetaTrend(
        "event:energy", "increase", waves, None,
        meta_key="canonical:event:energy:increase",
    )
    session = _Session()
    session.radar_store["trends"].append({
        "id": 99,
        "identity": (
            "meta", meta.subject_key, meta.meta_key, meta.direction,
            service.DETECTOR_VERSION,
        ),
        "state": TrendState.CONFIRMED.value,
        "t0_auto": None,
        "t0_effective": ANALYST_T0,
    })
    session.radar_store["revisions"].append({
        "trend_id": 99, "revision_kind": "analyst",
    })

    contexts = service._previous_meta_contexts(session, (meta,))
    sanity = service._t0_sanity(
        waves, (meta,), (TrendState.CONFIRMED,),
        meta_contexts=contexts, as_of=AS_OF,
    )

    assert sanity["violations"] == 0


def test_shadow_report_uses_persisted_meta_state_for_monotonic_lifecycle(monkeypatch):
    import src.radar.service as service

    point = _media_point(
        AS_OF - timedelta(days=1), value=0.9, article_id=919,
    )
    session = _Session()
    session.radar_store["trends"].append({
        "id": 99,
        "identity": (
            "meta", point.subject_key,
            f"canonical:{point.subject_key}:{point.direction}",
            point.direction, service.DETECTOR_VERSION,
        ),
        "state": TrendState.COOLING.value,
        "t0_auto": None,
        "t0_effective": None,
    })
    monkeypatch.setattr(service, "build_media_observations", lambda *_: [point])
    monkeypatch.setattr(service, "build_action_observations", lambda *_: [])

    report = run_radar_cycle(session, AS_OF, shadow=True)

    assert report.meta_state_counts[TrendState.COOLING.value] == 1
    assert report.meta_state_counts[TrendState.EMERGING.value] == 0


def test_contour_denominator_excludes_temporally_ineligible_identity_candidate():
    import src.radar.service as service

    media = _episode_wave("media", AS_OF - timedelta(days=30), "media-old")
    action = _episode_wave("action", AS_OF, "action-new")

    assert match_contour_episodes([(1, media)], [(2, action)]) == ()
    assert service._contour_completeness((media, action)) == {
        "identity_candidate_pairs": 1,
        "possible_pairs": 0,
        "linked_pairs": 0,
        "ratio": None,
    }


def test_contour_denominator_keeps_exact_fourteen_day_boundary_only():
    import src.radar.service as service

    media = _episode_wave("media", AS_OF - timedelta(days=30), "media-old")
    boundary = _episode_wave("action", AS_OF - timedelta(days=16), "action-boundary")
    outside = _episode_wave(
        "action", AS_OF - timedelta(days=16) + timedelta(seconds=1),
        "action-outside",
    )

    assert service._contour_completeness((media, boundary)) == {
        "identity_candidate_pairs": 1,
        "possible_pairs": 1,
        "linked_pairs": 1,
        "ratio": 1.0,
    }
    assert service._contour_completeness((media, outside)) == {
        "identity_candidate_pairs": 1,
        "possible_pairs": 0,
        "linked_pairs": 0,
        "ratio": None,
    }


def test_contour_matching_maximizes_bounded_pairs_before_nearest_gap():
    base = AS_OF.replace(day=1)
    media_zero = _episode_wave("media", base, "media-0")
    media_fifteen = _episode_wave("media", base.replace(day=16), "media-15")
    action_minus_fourteen = _episode_wave("action", base - timedelta(days=14), "action--14")
    action_eight = _episode_wave("action", base.replace(day=9), "action-8")

    pairs = match_contour_episodes(
        [(1, media_zero), (3, media_fifteen)],
        [(2, action_minus_fourteen), (4, action_eight)],
    )

    assert [(media_id, action_id) for media_id, _, action_id, _ in pairs] == [(1, 2), (3, 4)]


def test_contour_matching_uses_wave_key_for_stable_equal_gap_ties():
    base = AS_OF.replace(day=10)
    media = _episode_wave("media", base, "media")
    earlier = _episode_wave("action", base - timedelta(days=5), "z-earlier")
    later = _episode_wave("action", base + timedelta(days=5), "a-later")

    pairs = match_contour_episodes([(1, media)], [(2, earlier), (3, later)])

    assert [(media_id, action_id) for media_id, _, action_id, _ in pairs] == [(1, 3)]
