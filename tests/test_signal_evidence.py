from contextlib import nullcontext
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.engine import signals
from src.engine.signals import SignalEvidence, _emit
from src.api.routes.signal_detail import (
    SqlSignalDetailService,
    get_signal_detail_service,
    legacy_signal_evidence,
    router,
)


class QueryResult:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class SequentialSession:
    def __init__(self, result_sets):
        self.result_sets = list(result_sets)
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        rows = self.result_sets.pop(0) if self.result_sets else ()
        return QueryResult(rows)

    def begin_nested(self):
        return nullcontext()


class CanonicalPublisherTierSession(SequentialSession):
    def __init__(self):
        super().__init__([])
        self.discovery_articles = (
            SimpleNamespace(
                article_id=201,
                discovery_source_id=900,
                publisher_name="EL PAÍS",
                publisher_source_id=11,
                publisher_country_code="ES",
                publisher_tier="mainstream",
            ),
            SimpleNamespace(
                article_id=202,
                discovery_source_id=900,
                publisher_name="Reuters",
                publisher_source_id=22,
                publisher_country_code="GB",
                publisher_tier="mainstream",
            ),
            SimpleNamespace(
                article_id=203,
                discovery_source_id=900,
                publisher_name=None,
                publisher_source_id=None,
                publisher_country_code=None,
                publisher_tier=None,
            ),
            SimpleNamespace(
                article_id=204,
                discovery_source_id=900,
                publisher_name="Agencia EFE",
                publisher_source_id=12,
                publisher_country_code="ES",
                publisher_tier="official",
            ),
            SimpleNamespace(
                article_id=205,
                discovery_source_id=900,
                publisher_name="elDiario.es",
                publisher_source_id=13,
                publisher_country_code="ES",
                publisher_tier="independent",
            ),
        )

    def execute(self, statement, params=None):
        sql = str(statement)
        self.calls.append((sql, params or {}))
        if "JOIN article_country_facts s ON s.article_id = ar.id" not in sql:
            return QueryResult([])
        spanish = [
            item
            for item in self.discovery_articles
            if item.publisher_country_code == "ES"
            and item.publisher_source_id is not None
        ]
        return QueryResult([
            SimpleNamespace(
                country_code="ES",
                event_key="переговоры",
                tiers=len({item.publisher_tier for item in spanish}),
                n=len(spanish),
                avg_sent=-1.0,
                max_al=4,
                tier_list=["official", "mainstream", "independent"],
                article_ids=[item.article_id for item in spanish],
            )
        ])


def test_signal_evidence_contract_normalizes_stable_evidence_ids():
    at = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

    evidence = SignalEvidence(
        detector="index_shift",
        detector_version="1.0",
        threshold={"absolute_delta_min": 7.0},
        observed={"delta_24h": -8.5, "score": -22.0},
        baseline={"comparison_hours": 24},
        window_start=at,
        window_end=at,
        article_ids=(12, 7, 12),
        story_ids=(4, 4, 2),
        rri_points=(
            {"country_code": "ES", "time": at.isoformat(), "score": -22.0},
        ),
        evidence_ids=("rri:ES:2026-07-15T12:00:00+00:00",),
        confidence=0.8,
        completeness="complete",
        explanation={"rule": "Сдвиг RRI не меньше 7 пунктов за 24 часа"},
    )

    assert evidence.detector == "index_shift"
    assert evidence.detector_version == "1.0"
    assert evidence.threshold == {"absolute_delta_min": 7.0}
    assert evidence.observed["delta_24h"] == -8.5
    assert evidence.baseline == {"comparison_hours": 24}
    assert evidence.window_start == at
    assert evidence.window_end == at
    assert evidence.article_ids == (7, 12)
    assert evidence.story_ids == (2, 4)
    assert evidence.rri_points[0]["time"] == at.isoformat()
    assert evidence.evidence_ids == ("rri:ES:2026-07-15T12:00:00+00:00",)
    assert evidence.confidence == 0.8
    assert evidence.completeness == "complete"


def test_emit_persists_signal_and_immutable_evidence_in_one_session():
    at = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    evidence = SignalEvidence(
        detector="tier_convergence",
        detector_version="1.0",
        threshold={"minimum_distinct_tiers": 3},
        observed={"distinct_tiers": 4, "article_count": 5},
        baseline={},
        window_start=at,
        window_end=at,
        article_ids=(11, 12),
        confidence=0.9,
        completeness="complete",
        evidence_ids=("article:11", "article:12"),
        explanation={"rule": "Не менее трёх тиров за 24 часа"},
    )
    session = SequentialSession(
        [
            (),
            (),
            (SimpleNamespace(id=101),),
            (SimpleNamespace(story_id=8), SimpleNamespace(story_id=3)),
            (),
        ]
    )

    emitted = _emit(
        session,
        "tier_convergence",
        "ES",
        "tier_convergence:ES:event",
        "Конвергенция тиров",
        "Четыре тира освещают одно событие",
        {"event_key": "event"},
        evidence=evidence,
        severity="warning",
    )

    assert emitted is True
    assert len(session.calls) == 5
    signal_sql, signal_params = session.calls[2]
    story_sql, story_params = session.calls[3]
    evidence_sql, evidence_params = session.calls[4]
    assert "INSERT INTO signals" in signal_sql
    assert "RETURNING id" in signal_sql
    assert signal_params["confidence"] == 0.9
    assert "WITH RECURSIVE story_seed" in story_sql
    assert "merged_into_story_id" in story_sql
    assert "{0,18}" in story_sql
    assert "9223372036854775807" in story_sql
    assert "LEFT JOIN stories terminal_target" in story_sql
    assert "'^[1-9][0-9]*$'" not in story_sql
    assert story_params["story_ids"] == []
    assert story_params["article_ids"] == [11, 12]
    assert "INSERT INTO signal_evidence" in evidence_sql
    assert evidence_params["signal_id"] == 101
    assert evidence_params["article_ids"] == [11, 12]
    assert evidence_params["story_ids"] == [3, 8]
    assert evidence_params["detector"] == "tier_convergence"
    assert evidence_params["detector_version"] == "1.0"
    assert evidence_params["completeness"] == "complete"
    assert '"evidence_ids": ["article:11", "article:12"]' in evidence_params["explanation"]


def test_emit_canonicalizes_explicit_and_article_story_ids_before_persisting():
    at = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    evidence = SignalEvidence(
        detector="tier_convergence",
        detector_version="1.0",
        threshold={"minimum_distinct_tiers": 3},
        observed={"distinct_tiers": 4},
        baseline={},
        window_start=at,
        window_end=at,
        article_ids=(11,),
        story_ids=(4, 9),
        confidence=0.9,
        completeness="complete",
        explanation={"rule": "rule"},
    )
    session = SequentialSession([
        (),
        (),
        (SimpleNamespace(id=101),),
        (SimpleNamespace(story_id=3), SimpleNamespace(story_id=9)),
        (),
    ])

    assert _emit(
        session,
        "tier_convergence",
        "ES",
        "tier_convergence:ES:canonical",
        "Canonical story evidence",
        "Description",
        {},
        evidence=evidence,
    ) is True

    story_sql, story_params = session.calls[3]
    persisted_params = session.calls[4][1]
    assert "WITH RECURSIVE story_seed" in story_sql
    assert story_params == {"story_ids": [4, 9], "article_ids": [11]}
    assert persisted_params["story_ids"] == [3, 9]


def test_signal_detail_story_lookup_resolves_superseded_candidates():
    canonical = SimpleNamespace(
        id=3,
        slug="canonical",
        title_ru="Канонический сюжет",
        summary="Summary",
        lifecycle="developing",
        last_seen=datetime(2026, 7, 15, tzinfo=timezone.utc),
        clustering_confidence=0.9,
    )
    session = SequentialSession([(canonical,)])

    story = SqlSignalDetailService._load_story(
        session,
        story_ids=(4,),
        article_ids=(11,),
    )

    assert story.id == 3
    sql, params = session.calls[0]
    assert "WITH RECURSIVE candidates" in sql
    assert "merged_into_story_id" in sql
    assert "{0,18}" in sql
    assert "9223372036854775807" in sql
    assert "LEFT JOIN stories terminal_target" in sql
    assert "'^[1-9][0-9]*$'" not in sql
    assert params == {"story_ids": [4], "article_ids": [11]}


def test_emit_dedup_keeps_original_trigger_evidence_unchanged():
    evidence = SignalEvidence(
        detector="volume_surge",
        detector_version="1.0",
        threshold={"minimum_ratio": 2.0},
        observed={"ratio": 3.0},
        baseline={"share": 0.01},
        window_start=None,
        window_end=None,
        confidence=0.75,
        completeness="complete",
        evidence_ids=("gdelt_daily:ES:2026-07-15",),
        explanation={},
    )
    session = SequentialSession([(), (SimpleNamespace(id=77),)])

    emitted = _emit(
        session,
        "volume_surge",
        "ES",
        "volume_surge:ES:20260715",
        "Всплеск внимания",
        "Доля выросла втрое",
        {"ratio": 3.0},
        evidence=evidence,
    )

    assert emitted is False
    assert len(session.calls) == 2
    assert "pg_advisory_xact_lock" in session.calls[0][0]
    assert "SELECT id FROM signals" in session.calls[1][0]


def test_emit_normalizes_and_locks_long_dedup_key_before_lookup():
    evidence = SignalEvidence(
        detector="volume_surge",
        detector_version="1.0",
        threshold={"minimum_ratio": 2.0},
        observed={"ratio": 3.0},
        baseline={"share": 0.01},
        window_start=None,
        window_end=None,
        confidence=0.75,
        completeness="complete",
        evidence_ids=("gdelt_daily:ES:2026-07-15",),
        explanation={},
    )
    long_key = "volume_surge:" + "x" * 300
    normalized_key = long_key[:200]
    session = SequentialSession([(), (SimpleNamespace(id=77),)])

    emitted = _emit(
        session,
        "volume_surge",
        "ES",
        long_key,
        "Всплеск внимания",
        "Доля выросла втрое",
        {"ratio": 3.0},
        evidence=evidence,
    )

    assert emitted is False
    assert len(session.calls) == 2
    lock_sql, lock_params = session.calls[0]
    lookup_sql, lookup_params = session.calls[1]
    assert "pg_advisory_xact_lock(hashtextextended(:dk, 0))" in lock_sql
    assert "SELECT id FROM signals" in lookup_sql
    assert lock_params["dk"] == normalized_key
    assert lookup_params["dk"] == normalized_key
    assert len(normalized_key) == 200


def test_emit_rejects_evidence_from_a_different_detector():
    evidence = SignalEvidence(
        detector="tone_shift",
        detector_version="1.0",
        threshold={"absolute_z_score_min": 1.6},
        observed={"z_score": -2.0},
        baseline={"mean": 0.0},
        window_start=None,
        window_end=None,
        confidence=0.7,
        completeness="complete",
        evidence_ids=("gdelt_daily:ES:2026-07-15",),
        explanation={},
    )
    session = SequentialSession([])

    with pytest.raises(ValueError, match="does not match"):
        _emit(
            session,
            "volume_surge",
            "ES",
            "volume_surge:ES:20260715",
            "Всплеск",
            "Описание",
            {},
            evidence=evidence,
        )

    assert session.calls == []


def test_every_detector_path_builds_complete_versioned_evidence(monkeypatch):
    captured = {}

    def capture(
        session,
        signal_type,
        country_code,
        dedup_key,
        title,
        description,
        payload,
        *,
        evidence,
        severity="info",
    ):
        captured[signal_type] = evidence
        return True

    monkeypatch.setattr(signals, "_emit", capture)
    now = datetime.now(timezone.utc)

    signals.detect_tier_convergence(
        SequentialSession([[
            SimpleNamespace(
                country_code="ES",
                event_key="переговоры",
                tiers=3,
                n=4,
                avg_sent=-1.5,
                max_al=4,
                tier_list=["official", "mainstream", "independent"],
                article_ids=[11, 12, 13, 14],
            )
        ]])
    )
    signals.detect_official_silence(
        SequentialSession([
            [SimpleNamespace(
                country_code="ES",
                event_key="санкции",
                loud_n=3,
                quiet_n=0,
                first_seen=now - timedelta(hours=8),
                avg_sent=-4.0,
                article_ids=[21, 22, 23],
            )],
            [SimpleNamespace(exists=True)],
        ])
    )
    signals.detect_velocity_spike(
        SequentialSession([[
            SimpleNamespace(
                country_code="ES",
                last24=8,
                base=3.0,
                article_ids=[31, 32, 33, 34, 35, 36, 37, 38],
            )
        ]])
    )
    signals.detect_gdelt_shifts(
        SequentialSession([[
            SimpleNamespace(
                country_code="ES",
                latest_day=date(2026, 7, 15),
                earliest_day=date(2026, 7, 2),
                tones=[5.0, 0.0] + [0.0] * 12,
                shares=[0.03, 0.01] + [0.01] * 12,
                volumes=[20.0] + [15.0] * 13,
            )
        ]])
    )
    signals.detect_index_shifts(
        SequentialSession([[
            SimpleNamespace(
                country_code="ES",
                score=-22.0,
                level="cold",
                delta_24h=-8.5,
                time=now,
                baseline_time=now - timedelta(hours=20),
                baseline_score=-13.5,
                baseline_level="cool",
            )
        ]])
    )
    signals.detect_notable_events(
        SequentialSession([[
            SimpleNamespace(
                article_id=41,
                event_key="дипломатический кризис",
                event_type="diplomatic",
                action_level=4,
                sentiment=-5.0,
                title="Дипломатический кризис",
                reprint_count=2,
                published_at=now - timedelta(hours=2),
                country_code="ES",
            )
        ]])
    )
    signals.detect_fx_moves(
        SequentialSession([
            [SimpleNamespace(
                currency="KZT",
                day=date(2026, 7, 15),
                rate_to_rub=0.15,
                change_1d_pct=2.5,
                baseline_day=date(2026, 7, 14),
                baseline_rate=0.146,
            )],
            [SimpleNamespace(n=1)],
        ])
    )
    signals.detect_sanctions_escalation(
        SequentialSession([[
            SimpleNamespace(
                country_code="ES",
                delta=30,
                target_count=130,
                lists_count=4,
                last_change=date(2026, 7, 15),
            )
        ]])
    )

    assert set(captured) == {
        "tier_convergence",
        "official_silence",
        "velocity_spike",
        "tone_shift",
        "volume_surge",
        "index_shift",
        "notable_event",
        "fx_move",
        "sanctions_escalation",
    }
    for signal_type, evidence in captured.items():
        assert evidence.detector == signal_type
        assert evidence.detector_version
        assert evidence.threshold, signal_type
        assert evidence.observed, signal_type
        assert evidence.baseline
        assert evidence.baseline["type"]
        assert evidence.window_start is not None, signal_type
        assert evidence.window_end is not None, signal_type
        assert evidence.window_start <= evidence.window_end
        assert 0 <= evidence.confidence <= 1
        assert evidence.completeness == "complete"
        assert evidence.evidence_ids, signal_type
        assert evidence.explanation["limitations"], signal_type

    assert captured["tier_convergence"].article_ids == (11, 12, 13, 14)
    assert captured["official_silence"].threshold == {
        "minimum_loud_articles": 3,
        "maximum_quiet_articles": 0,
        "minimum_silence_hours": 6,
    }
    assert captured["velocity_spike"].observed["ratio"] == 2.67
    assert captured["tone_shift"].threshold["absolute_z_score_min"] == 1.6
    assert captured["tone_shift"].window_start == datetime(
        2026, 7, 2, tzinfo=timezone.utc
    )
    assert captured["volume_surge"].baseline["share"] == 0.01
    assert captured["volume_surge"].window_start == datetime(
        2026, 7, 2, tzinfo=timezone.utc
    )
    assert captured["index_shift"].rri_points == (
        {
            "country_code": "ES",
            "time": (now - timedelta(hours=20)).isoformat(),
            "score": -13.5,
            "level": "cool",
            "role": "baseline",
        },
        {
            "country_code": "ES",
            "time": now.isoformat(),
            "score": -22.0,
            "delta_24h": -8.5,
            "level": "cold",
            "role": "observed",
        },
    )
    assert captured["notable_event"].article_ids == (41,)
    assert captured["fx_move"].baseline == {
        "type": "previous_fx_rate",
        "status": "available",
        "day": "2026-07-14",
        "rate_to_rub": 0.146,
    }
    assert captured["fx_move"].observed["preceding_media_signal_count"] == 1
    assert captured["fx_move"].observed["media_lookback_hours"] == 72
    assert captured["fx_move"].evidence_ids == (
        "fx_rate:KZT:2026-07-14",
        "fx_rate:KZT:2026-07-15",
    )
    assert captured["sanctions_escalation"].threshold["minimum_new_targets"] == 25


def test_tier_convergence_uses_verified_publisher_tiers_and_article_evidence(
    monkeypatch,
):
    captured = {}

    def capture(
        session,
        signal_type,
        country_code,
        dedup_key,
        title,
        description,
        payload,
        *,
        evidence,
        severity="info",
    ):
        captured.update(
            country_code=country_code,
            evidence=evidence,
            payload=payload,
        )
        return True

    monkeypatch.setattr(signals, "_emit", capture)
    session = CanonicalPublisherTierSession()

    assert signals.detect_tier_convergence(session) == 1
    tier_sql = session.calls[0][0]
    assert "COUNT(DISTINCT s.tier)" in tier_sql
    assert "ARRAY_AGG(DISTINCT s.tier)" in tier_sql
    assert {item.discovery_source_id for item in session.discovery_articles} == {900}
    assert {
        item.publisher_source_id
        for item in session.discovery_articles
        if item.publisher_country_code == "ES"
    } == {11, 12, 13}
    assert captured["country_code"] == "ES"
    assert captured["payload"]["tiers"] == [
        "official",
        "mainstream",
        "independent",
    ]
    assert captured["evidence"].article_ids == (201, 204, 205)
    assert 202 not in captured["evidence"].article_ids  # Reuters belongs to GB.
    assert 203 not in captured["evidence"].article_ids  # Unknown is quarantined.


@pytest.mark.parametrize(
    "detector",
    (
        signals.detect_tier_convergence,
        signals.detect_official_silence,
        signals.detect_velocity_spike,
        signals.detect_notable_events,
    ),
)
def test_article_signal_detectors_join_canonical_publisher_facts(detector):
    session = SequentialSession([[]])

    assert detector(session) == 0
    article_sql = session.calls[0][0]
    assert "JOIN article_country_facts s ON s.article_id = ar.id" in article_sql
    assert "JOIN sources s ON ar.source_id = s.id" not in article_sql


def test_official_silence_keeps_active_official_outlet_catalog_query(monkeypatch):
    now = datetime.now(timezone.utc)
    session = SequentialSession([
        [SimpleNamespace(
            country_code="ES",
            event_key="санкции",
            loud_n=3,
            quiet_n=0,
            first_seen=now - timedelta(hours=8),
            avg_sent=-4.0,
            article_ids=[21, 22, 23],
        )],
        [],
    ])

    assert signals.detect_official_silence(session) == 0
    assert "JOIN article_country_facts s ON s.article_id = ar.id" in session.calls[0][0]
    assert "SELECT 1 FROM sources" in session.calls[1][0]


def test_missing_index_and_fx_comparators_mark_evidence_partial(monkeypatch):
    captured = {}

    def capture(
        session,
        signal_type,
        country_code,
        dedup_key,
        title,
        description,
        payload,
        *,
        evidence,
        severity="info",
    ):
        captured[signal_type] = evidence
        return True

    monkeypatch.setattr(signals, "_emit", capture)
    now = datetime.now(timezone.utc)
    signals.detect_index_shifts(
        SequentialSession([[
            SimpleNamespace(
                country_code="ES",
                score=-22.0,
                level="cold",
                delta_24h=-8.5,
                time=now,
                baseline_time=None,
                baseline_score=None,
                baseline_level=None,
            )
        ]])
    )
    signals.detect_fx_moves(
        SequentialSession([
            [SimpleNamespace(
                currency="KZT",
                day=date(2026, 7, 15),
                rate_to_rub=0.15,
                change_1d_pct=2.5,
                baseline_day=None,
                baseline_rate=None,
            )],
            [SimpleNamespace(n=0)],
        ])
    )

    assert captured["index_shift"].completeness == "partial"
    assert captured["index_shift"].baseline == {
        "type": "rri_point",
        "status": "missing",
        "comparison_hours": 24,
        "time": None,
        "score": None,
        "level": None,
    }
    assert captured["fx_move"].completeness == "partial"
    assert captured["fx_move"].baseline == {
        "type": "previous_fx_rate",
        "status": "missing",
        "day": None,
        "rate_to_rub": None,
    }


def test_velocity_evidence_does_not_retune_the_production_detector_predicate():
    session = SequentialSession([[]])

    assert signals.detect_velocity_spike(session) == 0

    detector_sql = session.calls[0][0]
    assert "last24 >= 5" in detector_sql
    assert "last24 >= 1.5 * GREATEST(base, 1.0)" in detector_sql
    assert "last24 >= 6" not in detector_sql
    assert "last24 >= 2.0 *" not in detector_sql


def test_old_signal_fallback_is_partial_and_never_invents_trigger_inputs():
    created_at = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    signal = SimpleNamespace(
        signal_type="tone_shift",
        confidence=0.7,
        payload={"tone": -4.2, "z_score": -2.1},
        created_at=created_at,
        expires_at=created_at + timedelta(hours=24),
    )

    evidence = legacy_signal_evidence(signal)

    assert evidence.detector == "tone_shift"
    assert evidence.detector_version == "legacy-unversioned"
    assert evidence.threshold == {}
    assert evidence.observed == {"tone": -4.2, "z_score": -2.1}
    assert evidence.baseline == {}
    assert evidence.window_start is None
    assert evidence.window_end is None
    assert evidence.completeness == "partial"
    assert "порог" in evidence.explanation["limitations"][0].lower()


class FakeSignalDetailService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def detail(self, *, signal_id):
        self.calls.append(signal_id)
        return self.result


def test_signal_detail_route_uses_injected_read_only_service_and_404s():
    result = {
        "id": 12,
        "type": "index_shift",
        "summary": {
            "headline": "Индекс Испании снизился на 8,5 пункта за сутки",
            "description": "RRI теперь −22,0",
        },
        "rule": {
            "detector": "index_shift",
            "version": "1.0",
            "threshold": {"absolute_delta_min": 7.0},
        },
        "values": {
            "observed": {"delta_24h": -8.5},
            "baseline": {"comparison_hours": 24},
            "window": {"start": None, "end": None},
        },
        "chart_points": [],
        "articles": [],
        "related_story": None,
        "countries": [{"code": "ES", "name": "Испания"}],
        "state": {"active": True},
        "confidence": 0.8,
        "evidence_completeness": "complete",
        "evidence_ids": [],
        "limitations": [],
    }
    service = FakeSignalDetailService(result)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_signal_detail_service] = lambda: service
    client = TestClient(app)

    response = client.get("/api/v2/signals/12")

    assert response.status_code == 200
    assert response.json()["rule"]["threshold"]["absolute_delta_min"] == 7.0
    assert service.calls == [12]

    service.result = None
    missing = client.get("/api/v2/signals/999")
    assert missing.status_code == 404


def test_signal_detail_route_rejects_non_positive_ids_before_service_call():
    service = FakeSignalDetailService({"id": 1})
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_signal_detail_service] = lambda: service
    client = TestClient(app)

    zero = client.get("/api/v2/signals/0")
    negative = client.get("/api/v2/signals/-1")

    assert zero.status_code == 422
    assert negative.status_code == 422
    assert service.calls == []


def test_sql_signal_detail_returns_concrete_evidence_and_http_safe_links(monkeypatch):
    created_at = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    expires_at = created_at + timedelta(hours=24)
    base = SimpleNamespace(
        id=12,
        signal_type="index_shift",
        country_code="ES",
        severity="warning",
        signal_confidence=0.8,
        title="Скачок индекса: Испания −8.5 за 24ч",
        description="Индекс отношений снизился на 8,5 пункта.",
        payload={"score": -22.0, "delta_24h": -8.5, "level": "cold"},
        created_at=created_at,
        expires_at=expires_at,
        evidence_detector="index_shift",
        detector_version="1.0",
        threshold={"absolute_delta_min": 7.0},
        observed={"score": -22.0, "delta_24h": -8.5},
        baseline={"comparison_hours": 24},
        window_start=created_at - timedelta(hours=24),
        window_end=created_at,
        article_ids=[41, 42],
        story_ids=[5],
        rri_points=[{
            "country_code": "ES",
            "time": created_at.isoformat(),
            "score": -22.0,
        }],
        evidence_confidence=0.8,
        completeness="complete",
        explanation={
            "rule": "Сдвиг не меньше семи пунктов",
            "evidence_ids": [f"rri:ES:{created_at.isoformat()}"],
        },
    )
    articles = [
        SimpleNamespace(
            id=41,
            title="Причина сдвига",
            url="https://example.org/article",
            published_at=created_at - timedelta(hours=2),
            source_name="Example",
            country_code="ES",
            sentiment=-4.0,
            action_level=4,
            event_key="кризис",
        ),
        SimpleNamespace(
            id=42,
            title="Небезопасная ссылка",
            url="javascript:alert(1)",
            published_at=created_at - timedelta(hours=1),
            source_name="Unsafe",
            country_code="ES",
            sentiment=-2.0,
            action_level=2,
            event_key="кризис",
        ),
    ]
    story = SimpleNamespace(
        id=5,
        slug="ispaniya-krizis",
        title_ru="Кризис в отношениях",
        summary="Межстрановой сюжет",
        lifecycle="developing",
        last_seen=created_at,
        clustering_confidence=0.86,
    )
    countries = [
        SimpleNamespace(
            country_code="ES",
            name_ru="Испания",
            article_count=2,
            media_tone=-3.0,
        ),
        SimpleNamespace(
            country_code="FR",
            name_ru="Франция",
            article_count=1,
            media_tone=-1.0,
        ),
    ]
    session = SequentialSession([[base], articles, [story], countries])

    from contextlib import contextmanager

    @contextmanager
    def session_factory():
        yield session

    monkeypatch.setattr(
        "src.api.routes.signal_detail.get_session",
        session_factory,
    )

    detail = SqlSignalDetailService().detail(signal_id=12)

    assert detail["summary"]["headline"] == base.title
    assert detail["rule"] == {
        "detector": "index_shift",
        "version": "1.0",
        "description": "Сдвиг не меньше семи пунктов",
        "threshold": {"absolute_delta_min": 7.0},
        "current_rule_reference": None,
    }
    assert detail["values"]["window"] == {
        "start": (created_at - timedelta(hours=24)).isoformat(),
        "end": created_at.isoformat(),
        "basis": None,
        "status": None,
    }
    assert detail["values"]["observed"]["delta_24h"] == -8.5
    assert detail["chart_points"][0]["score"] == -22.0
    assert detail["articles"][0]["url"] == "https://example.org/article"
    assert detail["articles"][1]["url"] is None
    assert detail["related_story"]["id"] == 5
    assert [country["code"] for country in detail["countries"]] == ["ES", "FR"]
    assert detail["state"] == {
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "active": True,
        "status": "active",
    }
    assert detail["confidence"] == 0.8
    assert detail["evidence_completeness"] == "complete"
    assert detail["context_preview"] is None
    assert detail["limitations"] == []
    assert len(session.calls) == 4


def test_signal_detail_articles_use_verified_publishers_and_resolved_urls():
    session = SequentialSession([[]])

    assert SqlSignalDetailService._load_articles(session, (41,)) == []

    sql = " ".join(session.calls[0][0].split())
    assert "JOIN article_country_facts s ON s.article_id = ar.id" in sql
    assert "COALESCE(NULLIF(ar.resolved_url, ''), ar.url) AS url" in sql
    assert "JOIN sources s ON s.id = ar.source_id" not in sql


def test_reconstructed_signal_detail_preserves_unknown_window_metadata(monkeypatch):
    created_at = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    base = SimpleNamespace(
        id=22,
        signal_type="tone_shift",
        country_code="ES",
        severity="warning",
        signal_confidence=0.88,
        title="Сдвиг тона",
        description="Исторический сигнал",
        payload={"tone": -3.2, "mean_90d": -0.4, "std": 0.8, "z_score": -3.5},
        created_at=created_at,
        expires_at=None,
        evidence_detector="tone_shift",
        detector_version="legacy-reconstructed-v1",
        threshold={},
        observed={"tone": -3.2, "z_score": -3.5},
        baseline={
            "type": "historical_tone_distribution",
            "mean": -0.4,
            "standard_deviation": 0.8,
            "status": "reconstructed_from_signal_payload",
        },
        window_start=None,
        window_end=None,
        article_ids=[],
        story_ids=[],
        rri_points=[],
        evidence_confidence=0.88,
        completeness="partial",
        explanation={
            "rule": "Восстановлено из сохранённого payload старого сигнала",
            "current_rule_reference": {
                "absolute_z_score_min": 1.6,
                "standard_deviation_floor": 0.3,
            },
            "window_basis": "not_persisted",
            "window_status": "unknown",
            "limitations": ["Историческое окно не сохранялось."],
        },
    )
    preview = SimpleNamespace(
        signal_id=22,
        kind="context",
        total=8,
        window_hours=72,
        window_start=created_at - timedelta(hours=72),
        window_end=created_at,
        article_id=501,
        title="Контекст сдвига",
        url="https://example.es/context",
        published_at=created_at - timedelta(hours=2),
        source_name="Ejemplo",
        country_code="ES",
    )
    session = SequentialSession([[base], [preview]])

    from contextlib import contextmanager

    @contextmanager
    def session_factory():
        yield session

    monkeypatch.setattr(
        "src.api.routes.signal_detail.get_session",
        session_factory,
    )

    detail = SqlSignalDetailService().detail(signal_id=22)

    assert detail["rule"]["threshold"] == {}
    assert detail["rule"]["current_rule_reference"] == {
        "absolute_z_score_min": 1.6,
        "standard_deviation_floor": 0.3,
    }
    assert detail["values"]["baseline"]["status"] == (
        "reconstructed_from_signal_payload"
    )
    assert detail["values"]["window"] == {
        "start": None,
        "end": None,
        "basis": "not_persisted",
        "status": "unknown",
    }
    # The global list endpoint treats NULL expiry as inactive, so detail must
    # retain the same semantics rather than silently calling it active.
    assert detail["state"] == {
        "created_at": created_at.isoformat(),
        "expires_at": None,
        "active": False,
        "status": "expired",
    }
    assert detail["evidence_completeness"] == "partial"
    assert detail["limitations"] == ["Историческое окно не сохранялось."]
    assert detail["articles"] == []
    assert detail["context_preview"]["articles"][0]["title"] == "Контекст сдвига"
    assert detail["context_preview"]["total"] == 8


def test_signal_detail_bounds_large_evidence_and_reports_truncation(monkeypatch):
    created_at = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    article_ids = list(range(1, 151))
    story_ids = list(range(1, 121))
    rri_points = [
        {"time": (created_at - timedelta(hours=index)).isoformat(), "score": index}
        for index in range(120)
    ]
    evidence_ids = [f"evidence:{index}" for index in range(250)]
    base = SimpleNamespace(
        id=88,
        signal_type="tier_convergence",
        country_code="ES",
        severity="warning",
        signal_confidence=0.8,
        title="Массовое доказательство",
        description="Проверка ограниченного ответа",
        payload={},
        created_at=created_at,
        expires_at=created_at + timedelta(hours=24),
        evidence_detector="tier_convergence",
        detector_version="1.0",
        threshold={"minimum_distinct_tiers": 3},
        observed={"distinct_tiers": 4},
        baseline={"type": "not_applicable"},
        window_start=created_at - timedelta(hours=24),
        window_end=created_at,
        article_ids=article_ids,
        story_ids=story_ids,
        rri_points=rri_points,
        evidence_confidence=0.8,
        completeness="complete",
        explanation={"evidence_ids": evidence_ids, "limitations": []},
    )
    articles = [
        SimpleNamespace(
            id=article_id,
            title=f"Article {article_id}",
            url=f"https://example.org/{article_id}",
            published_at=created_at,
            source_name="Example",
            country_code="ES",
            sentiment=0,
            action_level=1,
            event_key="event",
        )
        for article_id in article_ids[:100]
    ]
    story = SimpleNamespace(
        id=1,
        slug="bounded-story",
        title_ru="Сюжет",
        summary="Summary",
        lifecycle="developing",
        last_seen=created_at,
        clustering_confidence=0.8,
    )
    countries = [
        SimpleNamespace(
            country_code=f"{index:02d}",
            name_ru=f"Country {index}",
            article_count=1,
            media_tone=0,
        )
        for index in range(60)
    ]
    session = SequentialSession([[base], articles, [story], countries])

    from contextlib import contextmanager

    @contextmanager
    def session_factory():
        yield session

    monkeypatch.setattr("src.api.routes.signal_detail.get_session", session_factory)

    detail = SqlSignalDetailService().detail(signal_id=88)

    assert len(session.calls[1][1]["article_ids"]) == 100
    assert len(session.calls[2][1]["article_ids"]) == 100
    assert len(session.calls[2][1]["story_ids"]) == 100
    assert session.calls[3][1]["limit"] == 50
    assert len(detail["articles"]) == 100
    assert detail["articles_page"] == {
        "total": 150,
        "returned": 100,
        "limit": 100,
        "truncated": True,
        "has_more": True,
    }
    assert len(detail["countries"]) == 50
    assert len(detail["evidence_ids"]) == 200
    assert len(detail["evidence"]["article_ids"]) == 100
    assert len(detail["evidence"]["story_ids"]) == 100
    assert len(detail["evidence"]["rri_points"]) == 100
    assert detail["evidence_truncation"] == {
        "evidence_ids": {"total": 250, "returned": 200, "truncated": True},
        "article_ids": {"total": 150, "returned": 100, "truncated": True},
        "story_ids": {"total": 120, "returned": 100, "truncated": True},
        "rri_points": {"total": 120, "returned": 100, "truncated": True},
        "countries": {"total": 60, "returned": 50, "truncated": True},
    }


def test_main_app_registers_detail_without_replacing_existing_signal_list():
    from src.api.main import app

    paths = [route.path for route in app.routes]

    assert "/api/v2/signals" in paths
    assert "/api/v2/signals/{signal_id}" in paths
