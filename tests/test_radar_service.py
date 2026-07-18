from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID

from src.radar.repository import make_observation
from src.radar.grouping import CountryWave, MetaTrend
from src.radar.service import _persist_meta_and_contours, _state_for, match_contour_episodes, record_analyst_t0_override, run_radar_cycle
from src.radar.types import Contour, TrendState


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
