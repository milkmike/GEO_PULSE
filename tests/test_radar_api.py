from __future__ import annotations

from datetime import datetime, timezone
import inspect
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import radar as radar_routes


NOW = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
TREND_ID = "e7313c19-8f24-4a06-938b-7d5f8ce741e2"


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
        if country_code != "ES":
            return {"items": [], "next_key": None}
        return {"items": [_trend(scope="country", country_code="ES")], "next_key": None}

    def timeline(self, public_id):
        if str(public_id) != TREND_ID:
            return None
        return {"trend": _trend(), "items": [{
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
        }]}

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
