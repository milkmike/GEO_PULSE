"""LLM-based sentiment analysis using OpenRouter (OpenAI-compatible API)."""
import json
import logging

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import OPENROUTER_API_KEY, COUNTRY_NAMES
from src.pipeline.prompts import PROMPT_VERSION, SENTIMENT_PROMPT, ACTION_LEVEL_PROMPT
from src.api_tracker import track_api_call, track_duration

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "anthropic/claude-sonnet-4"


def get_headers() -> dict | None:
    if not OPENROUTER_API_KEY:
        return None
    return {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://cis-thermometer.app",
        "X-Title": "CIS Thermometer",
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def analyze_sentiment(
    title: str,
    body: str,
    source_name: str,
    country_code: str,
) -> dict | None:
    """Analyze sentiment of an article using OpenRouter API.
    
    Returns dict with: sentiment, confidence, event_type, action_level, reasoning
    Or None if API key is not set.
    """
    headers = get_headers()
    if headers is None:
        logger.warning("OPENROUTER_API_KEY not set, skipping sentiment analysis")
        return None

    country = COUNTRY_NAMES.get(country_code, country_code)
    
    # Truncate body for API call
    truncated_body = body[:3000] if body else "(нет текста)"
    
    prompt = SENTIMENT_PROMPT.format(
        source=source_name,
        country=country,
        title=title,
        body=truncated_body,
    )

    try:
        with track_duration() as timer:
            response = httpx.post(
                OPENROUTER_URL,
                headers=headers,
                json={
                    "model": MODEL,
                    "max_tokens": 300,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60.0,
            )
            response.raise_for_status()
        
        data = response.json()
        text = data["choices"][0]["message"]["content"].strip()

        # Extract token usage from response
        usage = data.get("usage", {})
        _tokens_in = usage.get("prompt_tokens", 0)
        _tokens_out = usage.get("completion_tokens", 0)

        # Track successful call
        track_api_call(
            service="openrouter", endpoint="/chat/completions",
            model=MODEL, script="analyze.py",
            tokens_in=_tokens_in, tokens_out=_tokens_out,
            status="ok", duration_ms=timer.ms,
        )
        
        # Try to extract JSON from response (handle markdown code blocks)
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        
        result = json.loads(text)
        
        # Validate sentiment
        sentiment = float(result.get("sentiment", 0))
        sentiment = max(-3.0, min(3.0, sentiment))
        
        confidence = float(result.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))
        
        event_type = result.get("event_type", "diplomatic")
        if event_type not in ("diplomatic", "military", "economic", "cultural", "security"):
            event_type = "diplomatic"

        # Validate action_level
        action_level = int(result.get("action_level", 1))
        action_level = max(1, min(6, action_level))

        return {
            "sentiment": sentiment,
            "confidence": confidence,
            "event_type": event_type,
            "action_level": action_level,
            "reasoning": result.get("reasoning", ""),
            "event_key": result.get("event_key", ""),
            "model_used": MODEL,
            "prompt_version": PROMPT_VERSION,
            "raw_response": result,
        }

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}, raw: {text[:200]}")
        track_api_call(
            service="openrouter", endpoint="/chat/completions",
            model=MODEL, script="analyze.py",
            status="error", error=f"JSON parse: {e}",
        )
        return None
    except httpx.HTTPStatusError as e:
        logger.error(f"OpenRouter API error: {e.response.status_code} {e.response.text[:200]}")
        track_api_call(
            service="openrouter", endpoint="/chat/completions",
            model=MODEL, script="analyze.py",
            status="error", error=f"HTTP {e.response.status_code}",
        )
        raise
    except Exception as e:
        logger.error(f"Unexpected error in sentiment analysis: {e}")
        track_api_call(
            service="openrouter", endpoint="/chat/completions",
            model=MODEL, script="analyze.py",
            status="error", error=str(e)[:500],
        )
        raise


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def analyze_action_level(
    title: str,
    body: str,
    country_code: str,
) -> int:
    """Analyze only action_level for an article (cheaper, shorter prompt).
    
    Returns action_level (1-6) or 1 as default.
    """
    headers = get_headers()
    if headers is None:
        return 1

    country = COUNTRY_NAMES.get(country_code, country_code)
    truncated_body = (body[:1500] if body else "(нет текста)")
    
    prompt = ACTION_LEVEL_PROMPT.format(
        country=country,
        title=title,
        body=truncated_body,
    )

    try:
        with track_duration() as timer:
            response = httpx.post(
                OPENROUTER_URL,
                headers=headers,
                json={
                    "model": MODEL,
                    "max_tokens": 100,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=60.0,
            )
            response.raise_for_status()
        
        data = response.json()
        text = data["choices"][0]["message"]["content"].strip()

        usage = data.get("usage", {})
        track_api_call(
            service="openrouter", endpoint="/chat/completions",
            model=MODEL, script="analyze.py",
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
            status="ok", duration_ms=timer.ms,
        )
        
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        
        result = json.loads(text)
        action_level = int(result.get("action_level", 1))
        return max(1, min(6, action_level))

    except Exception as e:
        logger.error(f"Failed to analyze action_level: {e}")
        track_api_call(
            service="openrouter", endpoint="/chat/completions",
            model=MODEL, script="analyze.py",
            status="error", error=str(e)[:500],
        )
        return 1
