from __future__ import annotations

import pytest

from src import embeddings


_EMBEDDING_ENV = (
    "EMBEDDING_PROXY_URL",
    "EMBEDDING_PROXY_SECRET",
    "JINA_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "OPENROUTER_EMBEDDING_MODEL",
    "OPENROUTER_EMBEDDING_DIMENSIONS",
)


@pytest.fixture(autouse=True)
def clean_embedding_environment(monkeypatch):
    for name in _EMBEDDING_ENV:
        monkeypatch.delenv(name, raising=False)


def test_openrouter_is_used_only_after_proxy_jina_and_openai(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")

    assert embeddings._get_api_config() == (
        "https://openrouter.ai/api/v1/embeddings",
        {
            "Authorization": "Bearer openrouter-secret",
            "Content-Type": "application/json",
        },
        "openai/text-embedding-3-small",
        1536,
    )

    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    assert embeddings._get_api_config()[0] == "https://api.openai.com/v1/embeddings"

    monkeypatch.setenv("JINA_API_KEY", "jina-secret")
    assert embeddings._get_api_config()[0] == "https://api.jina.ai/v1/embeddings"

    monkeypatch.setenv("EMBEDDING_PROXY_URL", "https://proxy.test/embeddings")
    assert embeddings._get_api_config()[0] == "https://proxy.test/embeddings"


def test_openrouter_model_and_dimensions_are_configurable(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    monkeypatch.setenv("OPENROUTER_EMBEDDING_MODEL", "vendor/custom-embedding")
    monkeypatch.setenv("OPENROUTER_EMBEDDING_DIMENSIONS", "768")

    url, headers, model, dimensions = embeddings._get_api_config()

    assert url == "https://openrouter.ai/api/v1/embeddings"
    assert headers["Authorization"] == "Bearer openrouter-secret"
    assert model == "vendor/custom-embedding"
    assert dimensions == 768
    assert embeddings.get_embedding_dim() == 768


class FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_openrouter_request_parses_embedding_and_tracks_openrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    monkeypatch.setenv("OPENROUTER_EMBEDDING_DIMENSIONS", "2")
    requests = []
    tracking = []

    def fake_post(url, *, headers, json, timeout):
        requests.append(
            {"url": url, "headers": headers, "json": json, "timeout": timeout}
        )
        return FakeResponse(
            {
                "data": [{"index": 0, "embedding": [0.25, 0.75]}],
                "usage": {"prompt_tokens": 7, "total_tokens": 7},
            }
        )

    monkeypatch.setattr(embeddings.httpx, "post", fake_post)
    monkeypatch.setattr(embeddings, "track_api_call", lambda **kwargs: tracking.append(kwargs))

    assert embeddings.generate_embedding(" document ") == [0.25, 0.75]
    assert requests == [
        {
            "url": "https://openrouter.ai/api/v1/embeddings",
            "headers": {
                "Authorization": "Bearer openrouter-secret",
                "Content-Type": "application/json",
            },
            "json": {
                "model": "openai/text-embedding-3-small",
                "input": ["document"],
                "dimensions": 2,
            },
            "timeout": 30.0,
        }
    ]
    assert tracking[-1]["service"] == "openrouter"
    assert tracking[-1]["status"] == "ok"
    assert tracking[-1]["tokens_in"] == 7


def test_openrouter_response_with_wrong_dimensions_is_rejected(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    monkeypatch.setenv("OPENROUTER_EMBEDDING_DIMENSIONS", "2")
    monkeypatch.setattr(
        embeddings.httpx,
        "post",
        lambda *args, **kwargs: FakeResponse(
            {"data": [{"index": 0, "embedding": [0.25]}]}
        ),
    )

    assert embeddings.generate_embedding("document") is None
