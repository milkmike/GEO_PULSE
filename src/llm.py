"""LLM provider chain with fallback + Redis response cache.

worldmonitor-style multi-tier inference: an ordered model list is tried in
sequence on 429/5xx/timeouts; an optional local Ollama endpoint acts as the
last resort (no API key needed). Identical prompts within the cache TTL hit
Redis instead of the provider — N concurrent consumers trigger one call.

Env:
  LLM_MODELS   comma-separated OpenRouter model ids (first = primary)
  OLLAMA_URL   e.g. http://localhost:11434 — appended as final fallback
  OLLAMA_MODEL model name for the Ollama tier (default: llama3.1)
"""
import hashlib
import json
import logging
import os
import time

import httpx

from src.api_tracker import track_api_call, track_duration
from src.config import OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

DEFAULT_MODELS = [
    "deepseek/deepseek-v4-flash",      # ~10x cheaper than Gemini 3 Flash, strong multilingual
    "xiaomi/mimo-v2.5",                # fallback — different provider
    "google/gemini-2.0-flash-001",     # last resort — different provider
]

LLM_MODELS = [m.strip() for m in os.environ.get("LLM_MODELS", "").split(",") if m.strip()] \
             or DEFAULT_MODELS

OLLAMA_URL = os.environ.get("OLLAMA_URL", "").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.1")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMError(Exception):
    """All providers in the chain failed."""


def _cache_get(key: str) -> str | None:
    try:
        from src.queue import get_redis
        val = get_redis().get(key)
        return val.decode() if val else None
    except Exception:
        return None


def _cache_set(key: str, value: str, ttl: int):
    try:
        from src.queue import get_redis
        get_redis().setex(key, ttl, value)
    except Exception:
        pass


def _call_openrouter(model: str, prompt: str, max_tokens: int,
                     temperature: float | None, script: str) -> str:
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://geopulse.app",
        "X-Title": "GEO PULSE",
    }
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if temperature is not None:
        body["temperature"] = temperature

    with track_duration() as timer:
        resp = httpx.post(OPENROUTER_URL, headers=headers, json=body, timeout=90.0)
        resp.raise_for_status()

    data = resp.json()
    usage = data.get("usage", {})
    track_api_call(
        service="openrouter", endpoint="/chat/completions",
        model=model, script=script,
        tokens_in=usage.get("prompt_tokens", 0),
        tokens_out=usage.get("completion_tokens", 0),
        status="ok", duration_ms=timer.ms,
    )
    return data["choices"][0]["message"]["content"].strip()


def _call_ollama(prompt: str, max_tokens: int, temperature: float | None,
                 script: str) -> str:
    body = {
        "model": OLLAMA_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    if temperature is not None:
        body["temperature"] = temperature

    with track_duration() as timer:
        resp = httpx.post(f"{OLLAMA_URL}/v1/chat/completions", json=body, timeout=180.0)
        resp.raise_for_status()

    data = resp.json()
    track_api_call(
        service="ollama", endpoint="/v1/chat/completions",
        model=OLLAMA_MODEL, script=script,
        status="ok", duration_ms=timer.ms,
    )
    return data["choices"][0]["message"]["content"].strip()


def chat(prompt: str, max_tokens: int = 300, temperature: float | None = None,
         cache_ttl: int = 0, script: str = "llm") -> tuple[str, str]:
    """Run the prompt through the provider chain.

    Returns (text, model_used). Raises LLMError when every tier fails.
    """
    cache_key = None
    if cache_ttl > 0:
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        cache_key = f"llm:v1:{digest}"
        cached = _cache_get(cache_key)
        if cached:
            obj = json.loads(cached)
            return obj["text"], obj["model"] + " (cached)"

    errors = []

    if OPENROUTER_API_KEY:
        for model in LLM_MODELS:
            try:
                text = _call_openrouter(model, prompt, max_tokens, temperature, script)
                if cache_key:
                    _cache_set(cache_key, json.dumps({"text": text, "model": model}), cache_ttl)
                return text, model
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                track_api_call(service="openrouter", endpoint="/chat/completions",
                               model=model, script=script, status="error",
                               error=f"HTTP {status}")
                errors.append(f"{model}: HTTP {status}")
                if status in RETRYABLE_STATUS:
                    logger.warning(f"LLM {model} returned {status}, trying next in chain")
                    time.sleep(1.0)
                    continue
                raise LLMError(f"Non-retryable error from {model}: HTTP {status}") from e
            except httpx.HTTPError as e:
                errors.append(f"{model}: {e}")
                logger.warning(f"LLM {model} transport error: {e}, trying next in chain")
                continue

    if OLLAMA_URL:
        try:
            text = _call_ollama(prompt, max_tokens, temperature, script)
            if cache_key:
                _cache_set(cache_key, json.dumps({"text": text, "model": f"ollama/{OLLAMA_MODEL}"}),
                           cache_ttl)
            return text, f"ollama/{OLLAMA_MODEL}"
        except Exception as e:
            errors.append(f"ollama: {e}")

    raise LLMError("All LLM providers failed: " + "; ".join(errors[-4:]) if errors
                   else "No LLM provider configured (set OPENROUTER_API_KEY or OLLAMA_URL)")


def extract_json(text: str) -> dict:
    """Parse a JSON object from an LLM reply (handles ``` fences)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start:end + 1]
    return json.loads(cleaned)
