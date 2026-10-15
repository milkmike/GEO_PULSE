"""Paid AI brief regeneration is admin-only; cached and on-demand reads stay public."""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.pipeline import briefs

ADMIN_KEY = "test-admin-key"


@pytest.fixture
def calls(monkeypatch):
    recorded = []

    def fake_generate(code, force=False):
        recorded.append((code, force))
        return {"content": "## Главное", "model": "test", "created_at": "2026-10-15T12:00:00+00:00", "cached": not force}

    monkeypatch.setattr(briefs, "generate_country_brief", fake_generate)
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)
    return recorded


@pytest.mark.parametrize("headers", [{}, {"x-admin-key": "wrong"}])
@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_refresh_without_admin_key_is_forbidden_and_never_calls_the_model(calls, headers, value):
    response = TestClient(app).get(f"/api/v2/countries/RS/brief?refresh={value}", headers=headers)

    assert response.status_code == 403
    assert calls == []


def test_refresh_is_denied_to_everyone_when_no_admin_key_is_configured(calls, monkeypatch):
    monkeypatch.delenv("ADMIN_API_KEY")

    response = TestClient(app).get("/api/v2/countries/RS/brief?refresh=1", headers={"x-admin-key": ""})

    assert response.status_code == 403
    assert calls == []


def test_admin_can_force_regeneration(calls):
    response = TestClient(app).get("/api/v2/countries/rs/brief?refresh=1", headers={"x-admin-key": ADMIN_KEY})

    assert response.status_code == 200
    assert calls == [("RS", True)]


def test_on_demand_generation_stays_public_and_uses_the_cache(calls):
    response = TestClient(app).get("/api/v2/countries/RS/brief?generate=1&refresh=0")

    assert response.status_code == 200
    assert calls == [("RS", False)]


def test_write_guard_still_requires_the_admin_key(calls):
    client = TestClient(app)

    assert client.post("/api/v2/countries/RS/brief").status_code == 403
    assert client.post("/api/v2/countries/RS/brief", headers={"x-admin-key": ADMIN_KEY}).status_code == 405
