from datetime import datetime, timezone

from src.radar.repository import make_observation
from src.radar.grouping import CountryWave
from src.radar.service import _state_for, record_analyst_t0_override, run_radar_cycle
from src.radar.types import TrendState


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
