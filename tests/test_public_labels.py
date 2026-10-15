from contextlib import contextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import threads as thread_routes
from src.engine.ru_index import LEVELS, level_ru


def test_level_ru_covers_every_index_level_and_passes_unknown_values():
    assert {name for _, name in LEVELS} <= set(
        code for code in ("ally", "partner", "neutral", "cooling", "tension", "hostile")
        if level_ru(code) != code
    )
    assert level_ru("partner") == "партнёр"
    assert level_ru("unknown") == "unknown"
    assert level_ru(None) is None


class _EmptyResult:
    def fetchall(self):
        return []


class _EmptySession:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params):
        self.calls.append(params)
        return _EmptyResult()


def _client(monkeypatch, session):
    @contextmanager
    def fake_session():
        yield session

    monkeypatch.setattr(thread_routes, "get_session", fake_session)
    app = FastAPI()
    app.include_router(thread_routes.router)
    return TestClient(app)


def test_country_threads_returns_empty_list_for_monitored_country(monkeypatch):
    session = _EmptySession()
    with _client(monkeypatch, session) as client:
        response = client.get("/api/v1/countries/rs/threads", params={"limit": 6})

    assert response.status_code == 200
    assert response.json() == {"country": "RS", "name": "Сербия", "threads": []}
    assert session.calls == [{"cc": "RS", "lim": 6}]


def test_country_threads_still_rejects_unknown_codes(monkeypatch):
    session = _EmptySession()
    with _client(monkeypatch, session) as client:
        response = client.get("/api/v1/countries/XX/threads")

    assert response.status_code == 404
    assert session.calls == []
