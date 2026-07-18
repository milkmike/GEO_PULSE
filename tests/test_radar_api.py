from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
import inspect
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.api.routes import radar as radar_routes


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
TREND_ID = "e7313c19-8f24-4a06-938b-7d5f8ce741e2"
POSTGRES_URL = os.getenv("GEO_PULSE_TEST_DATABASE_URL")


def _trend(**overrides):
    payload = {
        "public_id": TREND_ID,
        "scope": "meta",
        "state": "confirmed",
        "title_ru": "Изменение политики распространяется по региону",
        "subject_key": "policy:energy",
        "direction": "restrictive",
        "confidence": 0.88,
        "coverage_confidence": 0.82,
        "velocity": 2.5,
        "first_observed_at": NOW,
        "detected_at": NOW,
        "confirmed_at": NOW,
        "t0_auto": NOW,
        "t0_effective": NOW,
        "country_waves": [{
            "public_id": "591b4e21-ec8f-48a6-9b3c-37f64eea1702",
            "country_code": "ES",
            "contour": "media",
            "state": "confirmed",
            "t0_effective": NOW,
        }],
        "contours": {
            "media": {"state": "confirmed", "status": "insufficient"},
        },
        "evidence_preview": {
            "role": "trigger",
            "title": "Начальный материал",
            "url": "https://news.example/trigger",
        },
        "contradiction_marker": True,
    }
    payload.update(overrides)
    return payload


class FakeRadarService:
    def __init__(self):
        self.list_calls = []
        self.rows_before = {"trends": 2, "evidence": 2}

    def list_trends(self, *, filters, cursor, limit):
        self.list_calls.append((filters, cursor, limit))
        return {
            "items": [_trend()],
            "next_key": {
                "state_rank": 0,
                "velocity": 2.5,
                "first_observed_at": NOW,
                "public_id": TREND_ID,
            },
        }

    def trend(self, public_id):
        return _trend() if str(public_id) == TREND_ID else None

    def country_trends(self, country_code, *, filters, cursor, limit):
        self.list_calls.append((filters, cursor, limit))
        if country_code != "ES":
            return {"items": [], "next_key": None}
        return {
            "items": [_trend(scope="country", country_code="ES")],
            "next_key": {
                "state_rank": 0,
                "velocity": 2.5,
                "first_observed_at": NOW,
                "public_id": TREND_ID,
            },
        }

    def timeline(self, public_id):
        if str(public_id) != TREND_ID:
            return None
        return {"trend": _trend(), "items": [
            {
                "kind": "state",
                "at": NOW,
                "state": "confirmed",
                "contour": "media",
                "evidence": {
                    "nested_url": "https://news.example/timeline",
                    "unsafe_url": "javascript:alert(1)",
                    "credential_url": "https://user:secret@news.example/private",
                    "neutral": {"value": "//news.example/protocol-relative"},
                    "children": [
                        {"href": "https://news.example/context"},
                        {"url": "https://news.example/has whitespace"},
                        {"url": "https://news.example/control\u0001"},
                    ],
                },
            },
            {
                "kind": "t0_revision",
                "at": NOW,
                "state": None,
                "contour": None,
                "revision_kind": "automatic",
                "evidence": {},
            },
            {
                "kind": "t0_revision",
                "at": NOW,
                "state": None,
                "contour": None,
                "revision_kind": "analyst",
                "evidence": {"reason": "reviewed"},
            },
        ]}

    def evidence(self, public_id, *, cursor, limit):
        if str(public_id) != TREND_ID:
            return None
        return {"items": [
            {
                "public_id": "777f8eba-244a-4714-81a6-6aa69e9dce23",
                "role": "trigger",
                "contribution": 0.8,
                "title": "Безопасная ссылка",
                "url": "https://news.example/trigger",
                "why_included": "triggered_detection",
                "evidence": {
                    "chunk": {"url": "https://news.example/chunk"},
                    "blocked": "javascript:alert(1)",
                    "nested": [{"url": "https://news.example/clean"}],
                },
            },
            {
                "public_id": "3a853fac-3ac6-4a93-b0be-18f9c2aa2b6c",
                "role": "context",
                "contribution": 0,
                "title": "Небезопасная ссылка",
                "url": "javascript:alert(1)",
                "why_included": "context_for_interpretation",
                "evidence": {"url": "https://user:secret@news.example/private"},
            },
        ], "next_key": None}

    def coverage(self):
        return {
            "updated_at": NOW,
            "countries": [{
                "country_code": "ES",
                "coverage_confidence": 0.82,
                "state": "healthy",
                "blind_spots": [],
            }],
        }


def _client(service: FakeRadarService) -> TestClient:
    app = FastAPI()
    app.include_router(radar_routes.router)
    app.dependency_overrides[radar_routes.get_radar_service] = lambda: service
    return TestClient(app)


def test_radar_list_is_read_only_exposes_both_contours_and_binds_cursor_to_filters():
    service = FakeRadarService()
    client = _client(service)
    before = dict(service.rows_before)

    response = client.get("/api/v2/radar", params={"state": "confirmed", "limit": 20})

    assert response.status_code == 200
    body = response.json()
    assert set(body["items"][0]["contours"]) == {"media", "action"}
    assert body["items"][0]["contours"]["action"]["status"] == "insufficient"
    assert body["items"][0]["why_included"] == "prioritized_by_state_and_velocity"
    assert service.rows_before == before
    assert body["next_cursor"]

    wrong_filters = client.get(
        "/api/v2/radar",
        params={"state": "emerging", "cursor": body["next_cursor"]},
    )
    malformed = client.get("/api/v2/radar", params={"cursor": "not-a-cursor"})
    assert wrong_filters.status_code == 422
    assert malformed.status_code == 422


def test_radar_relation_filters_are_positive_combined_with_and_and_cursor_bound():
    service = FakeRadarService()
    client = _client(service)

    first = client.get(
        "/api/v2/radar",
        params={"story_id": 501, "signal_id": 601, "limit": 1},
    )

    assert first.status_code == 200
    filters = service.list_calls[-1][0]
    assert filters.story_id == 501
    assert filters.signal_id == 601
    cursor = first.json()["next_cursor"]
    assert client.get(
        "/api/v2/radar",
        params={"story_id": 502, "signal_id": 601, "cursor": cursor},
    ).status_code == 422
    assert client.get(
        "/api/v2/radar",
        params={"story_id": 501, "signal_id": 602, "cursor": cursor},
    ).status_code == 422
    assert client.get("/api/v2/radar", params={"story_id": 0}).status_code == 422
    assert client.get("/api/v2/radar", params={"signal_id": -1}).status_code == 422


def test_country_radar_accepts_relation_filters_and_rejects_invalid_ids():
    service = FakeRadarService()
    client = _client(service)

    response = client.get(
        "/api/v2/countries/es/radar",
        params={"story_id": 501, "signal_id": 601},
    )

    assert response.status_code == 200
    filters = service.list_calls[-1][0]
    assert filters.story_id == 501
    assert filters.signal_id == 601
    cursor = response.json()["next_cursor"]
    assert client.get(
        "/api/v2/countries/es/radar",
        params={"story_id": 502, "signal_id": 601, "cursor": cursor},
    ).status_code == 422
    assert client.get(
        "/api/v2/countries/es/radar",
        params={"story_id": 501, "signal_id": 602, "cursor": cursor},
    ).status_code == 422
    assert client.get(
        "/api/v2/countries/es/radar", params={"story_id": 0}
    ).status_code == 422
    assert client.get(
        "/api/v2/countries/es/radar", params={"signal_id": -1}
    ).status_code == 422


@pytest.mark.parametrize("path", ["/api/v2/radar", "/api/v2/countries/es/radar"])
@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("story_id", "1.0", id="story-fractional"),
        pytest.param("story_id", "+1", id="story-leading-plus"),
        pytest.param("story_id", "-1", id="story-negative"),
        pytest.param("story_id", " 1", id="story-leading-space"),
        pytest.param("story_id", "1 ", id="story-trailing-space"),
        pytest.param("story_id", "01", id="story-leading-zero"),
        pytest.param("story_id", str(2**63), id="story-overflow"),
        pytest.param("signal_id", "1.0", id="signal-fractional"),
        pytest.param("signal_id", "+1", id="signal-leading-plus"),
        pytest.param("signal_id", "-1", id="signal-negative"),
        pytest.param("signal_id", " 1", id="signal-leading-space"),
        pytest.param("signal_id", "1 ", id="signal-trailing-space"),
        pytest.param("signal_id", "01", id="signal-leading-zero"),
        pytest.param("signal_id", str(2**31), id="signal-overflow"),
    ],
)
def test_relation_ids_require_canonical_unsigned_decimal(path, field, value):
    response = _client(FakeRadarService()).get(path, params={field: value})

    assert response.status_code == 422


@pytest.mark.parametrize("path", ["/api/v2/radar", "/api/v2/countries/es/radar"])
def test_relation_ids_accept_their_exact_maximum_and_keep_integer_openapi(path):
    service = FakeRadarService()
    app = FastAPI()
    app.include_router(radar_routes.router)
    app.dependency_overrides[radar_routes.get_radar_service] = lambda: service
    client = TestClient(app)

    response = client.get(
        path,
        params={"story_id": str(2**63 - 1), "signal_id": str(2**31 - 1)},
    )

    assert response.status_code == 200
    filters = service.list_calls[-1][0]
    assert filters.story_id == 2**63 - 1
    assert filters.signal_id == 2**31 - 1
    openapi_path = (
        "/api/v2/countries/{code}/radar"
        if path.startswith("/api/v2/countries/")
        else path
    )
    operation = app.openapi()["paths"][openapi_path]["get"]
    parameters = {item["name"]: item["schema"] for item in operation["parameters"]}
    story_schema = next(
        item for item in parameters["story_id"].get("anyOf", [parameters["story_id"]])
        if item.get("type") == "integer"
    )
    signal_schema = next(
        item for item in parameters["signal_id"].get("anyOf", [parameters["signal_id"]])
        if item.get("type") == "integer"
    )
    assert story_schema == {
        "maximum": 2**63 - 1,
        "minimum": 1,
        "type": "integer",
    }
    assert signal_schema == {
        "maximum": 2**31 - 1,
        "minimum": 1,
        "type": "integer",
    }


def test_radar_routes_serialize_persisted_detail_timeline_evidence_and_coverage():
    service = FakeRadarService()
    client = _client(service)

    detail = client.get(f"/api/v2/radar/trends/{TREND_ID}")
    country = client.get("/api/v2/countries/es/radar")
    timeline = client.get(f"/api/v2/radar/trends/{TREND_ID}/timeline")
    evidence = client.get(f"/api/v2/radar/trends/{TREND_ID}/evidence")
    coverage = client.get("/api/v2/radar/coverage")

    assert detail.status_code == country.status_code == timeline.status_code == 200
    assert evidence.status_code == coverage.status_code == 200
    assert detail.json()["public_id"] == TREND_ID
    assert country.json()["items"][0]["country_code"] == "ES"
    assert timeline.json()["items"][0]["kind"] == "state"
    evidence_items = evidence.json()["items"]
    assert {item["role"] for item in evidence_items} >= {"trigger", "context"}
    assert evidence_items[0]["url"] == "https://news.example/trigger"
    assert evidence_items[1]["url"] is None
    assert coverage.json()["countries"][0]["state"] == "healthy"
    assert coverage.json()["coverage_source"] == "temporary trend-derived proxy; not collection-health snapshots"


def test_radar_recursively_sanitizes_persisted_evidence_and_timeline_urls():
    client = _client(FakeRadarService())

    timeline_evidence = client.get(f"/api/v2/radar/trends/{TREND_ID}/timeline").json()["items"][0]["evidence"]
    evidence_items = client.get(f"/api/v2/radar/trends/{TREND_ID}/evidence").json()["items"]

    assert timeline_evidence["nested_url"] == "https://news.example/timeline"
    assert timeline_evidence["unsafe_url"] is None
    assert timeline_evidence["credential_url"] is None
    assert timeline_evidence["neutral"]["value"] is None
    assert timeline_evidence["children"][0]["href"] == "https://news.example/context"
    assert timeline_evidence["children"][1]["url"] is None
    assert timeline_evidence["children"][2]["url"] is None
    assert evidence_items[0]["evidence"]["chunk"]["url"] == "https://news.example/chunk"
    assert evidence_items[0]["evidence"]["blocked"] is None
    assert evidence_items[1]["evidence"]["url"] is None


def test_t0_timeline_revisions_keep_lifecycle_state_empty_and_revision_kind_in_evidence():
    items = _client(FakeRadarService()).get(
        f"/api/v2/radar/trends/{TREND_ID}/timeline"
    ).json()["items"]

    assert items[0]["state"] == "confirmed"
    assert items[0]["kind"] == "state"
    assert items[1]["kind"] == "t0_revision"
    assert items[1]["state"] is None
    assert items[1]["contour"] is None
    assert items[1]["evidence"]["revision_kind"] == "automatic"
    assert items[2]["kind"] == "t0_revision"
    assert items[2]["state"] is None
    assert items[2]["evidence"] == {
        "reason": "reviewed",
        "revision_kind": "analyst",
    }


def test_sql_radar_read_session_rolls_back_and_closes_without_commit(monkeypatch):
    class Rows:
        def fetchall(self):
            return []

    class RecordingSession:
        def __init__(self):
            self.rollback_calls = 0
            self.close_calls = 0
            self.commit_calls = 0

        def execute(self, statement, params=None):
            return Rows()

        def rollback(self):
            self.rollback_calls += 1

        def close(self):
            self.close_calls += 1

        def commit(self):
            self.commit_calls += 1

    session = RecordingSession()
    monkeypatch.setattr(radar_routes, "SessionLocal", lambda: session)

    assert radar_routes.SqlRadarReadService().coverage() == {
        "updated_at": None,
        "countries": [],
    }
    assert session.rollback_calls == 1
    assert session.close_calls == 1
    assert session.commit_calls == 0


def test_sql_meta_relation_filters_use_direct_or_member_evidence_and_require_both(monkeypatch):
    calls = []

    class Rows:
        def fetchall(self):
            return []

    class Session:
        def execute(self, statement, params=None):
            calls.append((str(statement), dict(params or {})))
            return Rows()

    @contextmanager
    def read_session():
        yield Session()

    monkeypatch.setattr(radar_routes, "radar_read_session", read_session)
    service = radar_routes.SqlRadarReadService()

    service.list_trends(
        filters=radar_routes.RadarFilters(
            country="ES", story_id=501, signal_id=601
        ),
        cursor=None,
        limit=20,
    )

    sql, params = calls[-1]
    assert params["story_id"] == 501
    assert params["signal_id"] == 601
    assert "/* radar_related_story */" in sql
    assert "/* radar_related_signal */" in sql
    assert "related.trend_id = trend.id" in sql
    assert "related_member.meta_trend_id = trend.id" in sql
    assert "related_member.country_trend_id = related.trend_id" in sql
    assert sql.count("related_member.left_at IS NULL") == 2
    assert sql.index("/* radar_related_story */") < sql.index("/* radar_related_signal */")
    assert "AND (:story_id IS NULL OR EXISTS" in sql
    assert "AND (:signal_id IS NULL OR EXISTS" in sql
    assert sql.count(radar_routes._PUBLIC_EVIDENCE_PREDICATE) >= 2
    assert sql.count("evidence.trend_id = ranked.id") >= 2
    assert sql.count("evidence_member.left_at IS NULL") >= 2


def test_sql_country_relation_filters_require_evidence_on_the_returned_wave(monkeypatch):
    calls = []

    class Rows:
        def fetchall(self):
            return []

    class Session:
        def execute(self, statement, params=None):
            calls.append((str(statement), dict(params or {})))
            return Rows()

    @contextmanager
    def read_session():
        yield Session()

    monkeypatch.setattr(radar_routes, "radar_read_session", read_session)
    service = radar_routes.SqlRadarReadService()

    service.country_trends(
        "ES",
        filters=radar_routes.RadarFilters(
            country="ES", story_id=501, signal_id=601
        ),
        cursor=None,
        limit=20,
    )

    sql, params = calls[-1]
    assert params["country"] == "ES"
    assert params["story_id"] == 501
    assert params["signal_id"] == 601
    assert "related.trend_id = trend.id" in sql
    assert "related.story_id = :story_id" in sql
    assert "related.signal_id = :signal_id" in sql
    assert "radar_trend_members related_member" not in sql
    assert "trend.country_code = :country" in sql
    assert sql.count(radar_routes._PUBLIC_EVIDENCE_PREDICATE) >= 2
    assert sql.count("evidence.trend_id = ranked.id") >= 2
    assert "evidence_member" not in sql


def test_public_radar_lists_hide_internal_states_by_default(monkeypatch):
    calls = []

    class Rows:
        def fetchall(self):
            return []

    class Session:
        def execute(self, statement, params=None):
            calls.append((str(statement), dict(params or {})))
            return Rows()

    @contextmanager
    def read_session():
        yield Session()

    monkeypatch.setattr(radar_routes, "radar_read_session", read_session)
    service = radar_routes.SqlRadarReadService()

    service.list_trends(
        filters=radar_routes.RadarFilters(), cursor=None, limit=20
    )
    meta_sql, meta_params = calls[-1]
    service.country_trends(
        "ES",
        filters=radar_routes.RadarFilters(country="ES"),
        cursor=None,
        limit=20,
    )
    country_sql, country_params = calls[-1]

    for sql, params in ((meta_sql, meta_params), (country_sql, country_params)):
        assert params["state"] is None
        assert params["public_states"] == ["emerging", "confirmed", "cooling"]
        assert "trend.state = ANY(:public_states)" in sql


def test_sql_trend_detail_scopes_evidence_to_direct_or_active_members(monkeypatch):
    calls = []

    class Rows:
        def first(self):
            return None

    class Session:
        def execute(self, statement, params=None):
            calls.append((str(statement), dict(params or {})))
            return Rows()

    @contextmanager
    def read_session():
        yield Session()

    monkeypatch.setattr(radar_routes, "radar_read_session", read_session)

    radar_routes.SqlRadarReadService().trend(UUID(TREND_ID))

    sql, params = calls[-1]
    assert params == {"public_id": UUID(TREND_ID)}
    assert sql.count("evidence.trend_id = trend.id") >= 2
    assert sql.count("trend.scope = 'meta'") >= 2
    assert sql.count("evidence_member.left_at IS NULL") >= 2
    assert sql.count(radar_routes._PUBLIC_EVIDENCE_PREDICATE) >= 2


def test_sql_meta_evidence_pages_direct_and_active_member_rows_without_duplicates(monkeypatch):
    calls = []

    class Rows:
        def fetchall(self):
            return []

    class Session:
        def execute(self, statement, params=None):
            calls.append((str(statement), dict(params or {})))
            return Rows()

    @contextmanager
    def read_session():
        yield Session()

    monkeypatch.setattr(radar_routes, "radar_read_session", read_session)
    service = radar_routes.SqlRadarReadService()
    monkeypatch.setattr(
        service,
        "trend",
        lambda public_id: {"id": 2, "scope": "meta"},
    )

    service.evidence(UUID(TREND_ID), cursor=None, limit=20)

    sql, params = calls[-1]
    assert params["trend_id"] == 2
    assert params["scope"] == "meta"
    assert "/* radar_related_evidence */" in sql
    assert "related_member.meta_trend_id = :trend_id" in sql
    assert "related_member.left_at IS NULL" in sql
    assert "UNION" in sql
    assert "DISTINCT ON (evidence.public_id)" in sql
    assert radar_routes._PUBLIC_EVIDENCE_PREDICATE in sql
    assert "ORDER BY evidence.id ASC" in sql


def test_sql_timeline_keeps_revision_kind_out_of_lifecycle_state(monkeypatch):
    calls = []

    class Rows:
        def fetchall(self):
            return []

    class Session:
        def execute(self, statement, params=None):
            calls.append((str(statement), dict(params or {})))
            return Rows()

    @contextmanager
    def read_session():
        yield Session()

    monkeypatch.setattr(radar_routes, "radar_read_session", read_session)
    service = radar_routes.SqlRadarReadService()
    monkeypatch.setattr(
        service,
        "trend",
        lambda public_id: {"id": 2, "scope": "meta"},
    )

    service.timeline(UUID(TREND_ID))

    sql, params = calls[-1]
    assert params == {"trend_id": 2}
    assert "NULL::text AS state" in sql
    assert "revision_kind" in sql


def test_radar_routes_reject_invalid_filters_and_return_not_found_for_missing_trends():
    client = _client(FakeRadarService())

    assert client.get("/api/v2/radar", params={"state": "made_up"}).status_code == 422
    assert client.get("/api/v2/radar", params={"country": "x"}).status_code == 422
    assert client.get(f"/api/v2/radar/trends/{UUID(int=0)}").status_code == 404
    assert client.get(f"/api/v2/radar/trends/{UUID(int=0)}/timeline").status_code == 404
    assert client.get(f"/api/v2/radar/trends/{UUID(int=0)}/evidence").status_code == 404


def test_sql_radar_evidence_reads_action_titles_from_persisted_details():
    source = inspect.getsource(radar_routes.SqlRadarReadService)

    assert "event.details->>'title'" in source
    assert "event.title" not in source


@pytest.mark.skipif(
    not POSTGRES_URL,
    reason="GEO_PULSE_TEST_DATABASE_URL is not configured",
)
def test_postgres_relation_filters_match_direct_and_active_member_evidence(monkeypatch):
    pytest.importorskip("psycopg2")
    engine = create_engine(POSTGRES_URL)
    connection = engine.connect()
    try:
        connection.execute(text("""
            CREATE TEMP TABLE radar_trends (
                id BIGINT PRIMARY KEY, public_id UUID NOT NULL, scope TEXT NOT NULL,
                contour TEXT, country_code CHAR(2), subject_key TEXT NOT NULL,
                title_ru TEXT NOT NULL, direction TEXT NOT NULL, state TEXT NOT NULL,
                confidence NUMERIC NOT NULL, coverage_confidence NUMERIC NOT NULL,
                velocity NUMERIC NOT NULL, first_observed_at TIMESTAMPTZ NOT NULL,
                detected_at TIMESTAMPTZ, confirmed_at TIMESTAMPTZ,
                t0_auto TIMESTAMPTZ, t0_effective TIMESTAMPTZ,
                updated_at TIMESTAMPTZ NOT NULL
            );
            CREATE TEMP TABLE radar_trend_members (
                meta_trend_id BIGINT NOT NULL, country_trend_id BIGINT NOT NULL,
                left_at TIMESTAMPTZ
            );
            CREATE TEMP TABLE radar_trend_evidence (
                id BIGINT PRIMARY KEY, public_id UUID NOT NULL, trend_id BIGINT NOT NULL,
                observation_id BIGINT, action_event_id BIGINT, article_id INTEGER,
                story_id BIGINT, signal_id INTEGER, role TEXT NOT NULL,
                contribution NUMERIC NOT NULL, evidence JSONB NOT NULL
            );
            CREATE TEMP TABLE radar_contour_links (
                media_trend_id BIGINT, action_trend_id BIGINT, status TEXT
            );
            CREATE TEMP TABLE radar_observations (
                id BIGINT PRIMARY KEY, evidence JSONB NOT NULL, article_id INTEGER
            );
            CREATE TEMP TABLE action_events (
                id BIGINT PRIMARY KEY, details JSONB NOT NULL, evidence JSONB NOT NULL
            );
            CREATE TEMP TABLE articles (
                id INTEGER PRIMARY KEY, title TEXT, resolved_url TEXT, url TEXT
            );
        """))
        connection.execute(text("""
            INSERT INTO radar_trends(
                id, public_id, scope, contour, country_code, subject_key,
                title_ru, direction, state, confidence, coverage_confidence,
                velocity, first_observed_at, detected_at, confirmed_at,
                t0_auto, t0_effective, updated_at
            )
            SELECT id,
                   ('00000000-0000-0000-0000-' || lpad(id::text, 12, '0'))::uuid,
                   CASE WHEN id < 100 THEN 'meta' ELSE 'country' END,
                   CASE WHEN id < 100 THEN NULL ELSE 'media' END,
                   CASE WHEN id < 100 THEN NULL ELSE 'ES' END,
                   'subject:' || id, 'Trend ' || id, 'negative', 'confirmed',
                   0.9, 0.9, (200 - id)::numeric,
                   '2026-07-18T10:00:00Z'::timestamptz,
                   '2026-07-18T10:00:00Z'::timestamptz,
                   '2026-07-18T10:00:00Z'::timestamptz,
                   '2026-07-18T10:00:00Z'::timestamptz,
                   '2026-07-18T10:00:00Z'::timestamptz,
                   '2026-07-18T12:00:00Z'::timestamptz
            FROM unnest(ARRAY[1,2,3,4,5,6,7,8,101,102,103,104,105,106,107,108]) id;

            INSERT INTO radar_trend_members(meta_trend_id, country_trend_id, left_at)
            VALUES (1,101,NULL), (2,102,NULL), (2,102,NULL), (3,103,NULL), (4,104,NULL),
                   (5,105,NULL), (6,106,NULL),
                   (7,107,'2026-07-18T11:00:00Z'), (8,108,NULL);

            INSERT INTO radar_observations(id, evidence, article_id)
            VALUES (201, '{}', NULL), (202, '{}', NULL);

            INSERT INTO radar_trend_evidence(
                id, public_id, trend_id, observation_id, story_id, signal_id, role,
                contribution, evidence
            ) VALUES
              (1,'10000000-0000-0000-0000-000000000001',1,NULL,501,NULL,'trigger',1,'{}'),
              (2,'10000000-0000-0000-0000-000000000002',102,201,501,601,'trigger',1,'{}'),
              (3,'10000000-0000-0000-0000-000000000003',103,NULL,999,999,'trigger',1,'{}'),
              (4,'10000000-0000-0000-0000-000000000004',4,NULL,501,NULL,'trigger',1,'{}'),
              (5,'10000000-0000-0000-0000-000000000005',5,NULL,NULL,601,'trigger',1,'{}'),
              (6,'10000000-0000-0000-0000-000000000006',6,NULL,501,601,'trigger',1,'{}'),
              (7,'10000000-0000-0000-0000-000000000007',107,NULL,501,601,'trigger',1,'{}'),
              (8,'10000000-0000-0000-0000-000000000008',108,NULL,501,601,'trigger',1,'{}'),
              (9,'10000000-0000-0000-0000-000000000009',102,201,NULL,NULL,'contradiction',-0.5,'{}'),
              (10,'10000000-0000-0000-0000-000000000010',2,202,NULL,NULL,'support',0.25,'{}'),
              (11,'10000000-0000-0000-0000-000000000011',102,201,502,601,'trigger',1,jsonb_build_object('_relation_only', true)),
              (12,'10000000-0000-0000-0000-000000000012',102,201,501,602,'trigger',1,jsonb_build_object('_relation_only', true)),
              (13,'10000000-0000-0000-0000-000000000013',107,NULL,NULL,NULL,'contradiction',-0.5,'{}');
        """))
        connection.commit()

        test_session = sessionmaker(bind=connection, expire_on_commit=False)
        monkeypatch.setattr(radar_routes, "SessionLocal", test_session)
        app = FastAPI()
        app.include_router(radar_routes.router)
        client = TestClient(app)

        def collect(path, *, story_id=501, signal_id=601):
            first = client.get(
                path,
                params={"story_id": story_id, "signal_id": signal_id, "limit": 1},
            )
            assert first.status_code == 200
            items = list(first.json()["items"])
            cursor = first.json()["next_cursor"]
            while cursor:
                page = client.get(
                    path,
                    params={
                        "story_id": story_id,
                        "signal_id": signal_id,
                        "limit": 1,
                        "cursor": cursor,
                    },
                )
                assert page.status_code == 200
                items.extend(page.json()["items"])
                cursor = page.json()["next_cursor"]
            return first.json()["next_cursor"], {item["public_id"] for item in items}

        meta_cursor, meta_ids = collect("/api/v2/radar")
        country_cursor, country_ids = collect("/api/v2/countries/es/radar")
        _, meta_relation_ids = collect(
            "/api/v2/radar", story_id=502, signal_id=602
        )
        _, country_relation_ids = collect(
            "/api/v2/countries/es/radar", story_id=502, signal_id=602
        )

        def collect_evidence(public_id):
            path = f"/api/v2/radar/trends/{public_id}/evidence"
            first = client.get(path, params={"limit": 1})
            assert first.status_code == 200
            items = list(first.json()["items"])
            cursor = first.json()["next_cursor"]
            first_cursor = cursor
            while cursor:
                page = client.get(path, params={"limit": 1, "cursor": cursor})
                assert page.status_code == 200
                items.extend(page.json()["items"])
                cursor = page.json()["next_cursor"]
            return first_cursor, items

        evidence_cursor, meta_evidence = collect_evidence(
            "00000000-0000-0000-0000-000000000002"
        )
        _, country_evidence = collect_evidence(
            "00000000-0000-0000-0000-000000000102"
        )
        active_meta = client.get(
            "/api/v2/radar/trends/00000000-0000-0000-0000-000000000002"
        ).json()
        direct_meta = client.get(
            "/api/v2/radar/trends/00000000-0000-0000-0000-000000000004"
        ).json()
        departed_meta = client.get(
            "/api/v2/radar/trends/00000000-0000-0000-0000-000000000007"
        ).json()
        direct_country = client.get(
            "/api/v2/radar/trends/00000000-0000-0000-0000-000000000107"
        ).json()

        assert meta_ids == {
            "00000000-0000-0000-0000-000000000002",
            "00000000-0000-0000-0000-000000000006",
            "00000000-0000-0000-0000-000000000008",
        }
        assert "00000000-0000-0000-0000-000000000007" not in meta_ids
        assert country_ids == {
            "00000000-0000-0000-0000-000000000102",
            "00000000-0000-0000-0000-000000000107",
            "00000000-0000-0000-0000-000000000108",
        }
        assert "00000000-0000-0000-0000-000000000103" not in country_ids
        assert meta_relation_ids == {
            "00000000-0000-0000-0000-000000000002",
        }
        assert country_relation_ids == {
            "00000000-0000-0000-0000-000000000102",
        }
        assert [item["public_id"] for item in meta_evidence] == [
            "10000000-0000-0000-0000-000000000002",
            "10000000-0000-0000-0000-000000000009",
            "10000000-0000-0000-0000-000000000010",
        ]
        assert {item["role"] for item in meta_evidence} == {
            "trigger", "contradiction", "support",
        }
        assert [item["public_id"] for item in country_evidence] == [
            "10000000-0000-0000-0000-000000000002",
            "10000000-0000-0000-0000-000000000009",
        ]
        assert active_meta["contradiction_marker"] is True
        assert active_meta["evidence_preview"]["public_id"] == (
            "10000000-0000-0000-0000-000000000002"
        )
        assert direct_meta["contradiction_marker"] is False
        assert direct_meta["evidence_preview"]["public_id"] == (
            "10000000-0000-0000-0000-000000000004"
        )
        assert departed_meta["contradiction_marker"] is False
        assert departed_meta["evidence_preview"] is None
        assert direct_country["contradiction_marker"] is True
        assert direct_country["evidence_preview"]["public_id"] == (
            "10000000-0000-0000-0000-000000000007"
        )
        assert client.get(
            "/api/v2/radar",
            params={"story_id": 502, "signal_id": 601, "cursor": meta_cursor},
        ).status_code == 422
        assert client.get(
            "/api/v2/countries/es/radar",
            params={"story_id": 501, "signal_id": 602, "cursor": country_cursor},
        ).status_code == 422
        assert client.get(
            "/api/v2/radar/trends/00000000-0000-0000-0000-000000000006/evidence",
            params={"cursor": evidence_cursor},
        ).status_code == 422
    finally:
        connection.close()
        engine.dispose()
