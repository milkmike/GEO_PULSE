"""HTTP boundary for the read-only country projection."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.routes import decision_workspace as route


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(route.router)
    return TestClient(app)


@pytest.mark.parametrize('country', ['R', 'RUS', '123', '<s>', ''])
def test_bad_country_never_loads_corpus(monkeypatch, client, country):
    monkeypatch.setattr(route, 'load_decision_workspace', lambda *_: pytest.fail('invalid country reached DB'))
    assert client.get('/api/v2/decision-workspace', params={'country':country}).status_code == 422


def test_known_country_is_normalized_and_unknown_is_not_silently_replaced(monkeypatch, client):
    def load(country):
        if country != 'RS':
            raise ValueError('Unknown country code')
        return {'country': {'code':'RS'}, 'coverage': {'independent_confirmation':'not_assessed'}}
    monkeypatch.setattr(route, 'load_decision_workspace', load)
    response = client.get('/api/v2/decision-workspace?country=rs')
    assert response.status_code == 200
    assert response.json()['country']['code'] == 'RS'
    assert client.get('/api/v2/decision-workspace?country=ZZ').status_code == 422
