"""Story copy goes through the shared src/llm.py chain and fails closed."""
import json

import httpx
import pytest

from src import llm
from src.stories import StoryCandidate, deterministic_story_copy, resolve_story_copy


COPY = {
    "title_ru": "Россия задержала японского журналиста по делу о шпионаже",
    "title_en": "Russia detains Japanese journalist on espionage charges",
    "summary": "ФСБ задержала японского журналиста, МИД Японии потребовал объяснений.",
}
EMPTY_REASONING_ONLY = {
    "choices": [{"message": {"content": None}, "finish_reason": "length"}],
    "usage": {
        "prompt_tokens": 900, "completion_tokens": 1500, "cost": 0.002,
        "completion_tokens_details": {"reasoning_tokens": 1500},
    },
}


@pytest.fixture
def provider(monkeypatch):
    import scripts.build_threads as build_threads

    records, payloads, replies = [], [], {}
    monkeypatch.setattr(llm, "OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(llm, "OLLAMA_URL", "")
    monkeypatch.setattr(llm, "_openrouter_blocked_until", 0)
    monkeypatch.setattr(llm, "_empty_response_blocked_until", {})
    monkeypatch.setattr(llm, "track_api_call", lambda **kw: records.append(kw))
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)

    def post(url, **kw):
        payloads.append(kw["json"])
        data = replies.get(kw["json"]["model"], EMPTY_REASONING_ONLY)
        return httpx.Response(200, json=data, request=httpx.Request("POST", url))

    monkeypatch.setattr(llm.httpx, "post", post)
    return build_threads, replies, records, payloads


def _reply(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 80, "cost": 0.0002},
    }


def _candidates():
    return [
        StoryCandidate(
            thread_id=thread_id, country_code=country,
            event_key="задержание японского журналиста",
            title="Russia Detains Japanese Journalist on Espionage Charges",
            article_ids=(thread_id,),
        )
        for thread_id, country in ((1, "JP"), (2, "US"))
    ]


def test_story_copy_uses_shared_chain_and_skips_reasoning_only_reply(provider):
    build_threads, replies, records, payloads = provider
    primary, fallback_model = build_threads.STORY_COPY_MODELS[:2]
    replies[fallback_model] = _reply("```json\n" + json.dumps(COPY, ensure_ascii=False) + "\n```")

    result = build_threads.generate_story_copy({"titles": ["headline"]})

    assert result == {**COPY, "model": fallback_model}
    assert [payload["model"] for payload in payloads] == [primary, fallback_model]
    assert build_threads.STORY_COPY_MODELS[0] == build_threads.MODEL
    assert all(record["script"] == "build_threads.py" for record in records)
    assert records[0]["status"] == "error"
    assert "empty_content" in records[0]["error"]
    assert records[0]["cost"] == 0.002
    assert records[1]["status"] == "ok"
    assert payloads[0]["max_tokens"] == build_threads.STORY_COPY_MAX_TOKENS


def test_empty_responses_from_every_model_fall_back_cleanly(provider):
    build_threads, _, records, payloads = provider
    candidates = _candidates()

    assert build_threads.generate_story_copy({"titles": ["headline"]}) is None
    copy = resolve_story_copy(
        candidates,
        source_hash="new",
        previous_source_hash=None,
        previous_copy=None,
        summarizer=build_threads.generate_story_copy,
    )

    assert copy == deterministic_story_copy(candidates)
    assert copy.summary_model is None
    # Models that returned paid empty output are cooled down, not re-billed.
    assert len(payloads) == len(dict.fromkeys(build_threads.STORY_COPY_MODELS))
    assert all(record["status"] == "error" for record in records)


@pytest.mark.parametrize("content", ["not json at all", "```", '["list"]'])
def test_non_object_reply_returns_none(provider, content):
    build_threads, replies, _, _ = provider
    replies[build_threads.STORY_COPY_MODELS[0]] = _reply(content)

    assert build_threads.generate_story_copy({"titles": ["headline"]}) is None


def test_successful_copy_replaces_untranslated_fallback(provider):
    build_threads, replies, _, _ = provider
    replies[build_threads.STORY_COPY_MODELS[0]] = _reply(json.dumps(COPY, ensure_ascii=False))
    candidates = _candidates()
    stored = deterministic_story_copy(candidates)

    copy = resolve_story_copy(
        candidates,
        source_hash="same",
        previous_source_hash="same",
        previous_copy=stored,
        summarizer=build_threads.generate_story_copy,
        retry_fallback=True,
    )

    assert copy.title_ru == COPY["title_ru"]
    assert copy.title_en == COPY["title_en"]
    assert copy.summary_model == build_threads.STORY_COPY_MODELS[0]
