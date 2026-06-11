"""LLM-based sentiment analysis via the provider fallback chain (src/llm.py)."""
import json
import logging

from tenacity import retry, stop_after_attempt, wait_exponential

from src.countries import COUNTRY_NAMES_ALL
from src.llm import LLMError, chat, extract_json
from src.pipeline.prompts import PROMPT_VERSION, ACTION_LEVEL_PROMPT, render_sentiment_prompt
from src.pipeline.topics import validate_topics

# Generic event_keys that are too broad for meaningful clustering
GENERIC_KEY_PATTERNS = [
    'сотрудничество', 'отношения', 'двусторонние', 'многовекторная',
    'мониторинг', 'развитие', 'перспективы', 'обсуждение',
]

def validate_event_key(event_key: str, title: str, is_relevant: bool) -> str:
    """Validate and clean event_key. Return empty string if invalid."""
    if not is_relevant:
        return ""
    if not event_key or not event_key.strip():
        return ""
    key = event_key.strip().lower()
    # Too short
    if len(key) < 8:
        return ""
    # Too generic — just a broad topic without specifics
    words = key.split()
    if len(words) <= 2:
        for pattern in GENERIC_KEY_PATTERNS:
            if pattern in key:
                return ""
    return key

logger = logging.getLogger(__name__)


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def analyze_sentiment(
    title: str,
    body: str,
    source_name: str,
    country_code: str,
) -> dict | None:
    """Analyze sentiment of an article through the LLM provider chain.

    Returns dict with: sentiment, confidence, event_type, topics, action_level,
    event_key, reasoning. Or None if no provider is configured.
    """
    country = COUNTRY_NAMES_ALL.get(country_code, country_code)

    # Truncate body for API call
    truncated_body = body[:3000] if body else "(нет текста)"

    prompt = render_sentiment_prompt(
        source=source_name,
        country=country,
        title=title,
        body=truncated_body,
    )

    try:
        text, model_used = chat(prompt, max_tokens=350, script="analyze.py")
    except LLMError as e:
        logger.warning(f"LLM chain unavailable: {e}")
        return None

    try:
        result = extract_json(text)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse LLM response as JSON: {e}, raw: {text[:200]}")
        return None

    # Check LLM relevance verdict
    llm_relevant = result.get("is_relevant", True)
    if isinstance(llm_relevant, str):
        llm_relevant = llm_relevant.lower() not in ("false", "0", "no")

    if not llm_relevant:
        logger.info(f"  LLM says not relevant: {result.get('reasoning', '')[:80]}")
        return {
            "is_relevant": False,
            "relevance_score": 0.0,
            "model_used": model_used,
            "prompt_version": PROMPT_VERSION,
            "raw_response": result,
        }

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
        "topics": validate_topics(result.get("topics")),
        "action_level": action_level,
        "reasoning": result.get("reasoning", ""),
        "event_key": validate_event_key(
            result.get("event_key", ""),
            title,
            True,  # already checked is_relevant above
        ),
        "model_used": model_used,
        "prompt_version": PROMPT_VERSION,
        "raw_response": result,
    }


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=30))
def analyze_action_level(
    title: str,
    body: str,
    country_code: str,
) -> int:
    """Analyze only action_level for an article (cheaper, shorter prompt).

    Returns action_level (1-6) or 1 as default.
    """
    country = COUNTRY_NAMES_ALL.get(country_code, country_code)
    truncated_body = (body[:1500] if body else "(нет текста)")

    prompt = ACTION_LEVEL_PROMPT.format(
        country=country,
        title=title,
        body=truncated_body,
    )

    try:
        text, _model = chat(prompt, max_tokens=100, script="analyze.py")
        result = extract_json(text)
        action_level = int(result.get("action_level", 1))
        return max(1, min(6, action_level))
    except (LLMError, json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to analyze action_level: {e}")
        return 1
