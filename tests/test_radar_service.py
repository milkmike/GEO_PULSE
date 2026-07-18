from datetime import datetime, timedelta, timezone

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
