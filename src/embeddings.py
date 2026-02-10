"""Embedding generation via OpenAI-compatible API (text-embedding-3-small).

Used for semantic clustering of articles. Generates 1536-dim vectors.
Architecture note: this module is designed to be swappable — future Graphiti
integration would add a graph-based enrichment layer on top of embeddings.
"""
import json
import logging
import time
from typing import Optional

import httpx

from src.config import OPENROUTER_API_KEY

logger = logging.getLogger(__name__)

# OpenAI embedding API (direct, not through OpenRouter — cheaper and faster)
OPENAI_API_KEY = None  # Will use OpenRouter key for now
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536

# OpenRouter supports embeddings too
OPENROUTER_EMBEDDING_URL = "https://openrouter.ai/api/v1/embeddings"


def _get_api_config() -> tuple[str, dict]:
    """Return (url, headers) for embedding API."""
    # Prefer direct OpenAI if key available
    import os
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        return "https://api.openai.com/v1/embeddings", {
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json",
        }
    # Fallback to OpenRouter
    if OPENROUTER_API_KEY:
        return OPENROUTER_EMBEDDING_URL, {
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://cis-thermometer.app",
            "X-Title": "CIS Thermometer",
        }
    return "", {}


def generate_embedding(text: str) -> Optional[list[float]]:
    """Generate embedding for a single text string.

    Args:
        text: Input text (title + summary/body). Truncated to ~8000 chars.

    Returns:
        List of 1536 floats, or None on failure.
    """
    url, headers = _get_api_config()
    if not url:
        return None

    # Truncate to ~8000 chars (~2000 tokens) — well within 8191 token limit
    text = text[:8000].strip()
    if not text:
        return None

    for attempt in range(3):
        try:
            response = httpx.post(
                url,
                headers=headers,
                json={
                    "model": EMBEDDING_MODEL,
                    "input": text,
                },
                timeout=30.0,
            )
            if response.status_code == 429:
                wait = 5 * (attempt + 1)
                logger.warning(f"Embedding rate limited, waiting {wait}s")
                time.sleep(wait)
                continue

            response.raise_for_status()
            data = response.json()
            embedding = data["data"][0]["embedding"]
            return embedding

        except Exception as e:
            logger.warning(f"Embedding error (attempt {attempt + 1}): {e}")
            time.sleep(2)

    return None


def generate_embeddings_batch(texts: list[str]) -> list[Optional[list[float]]]:
    """Generate embeddings for a batch of texts (up to 100).

    OpenAI supports batch embedding in a single call.

    Args:
        texts: List of input texts.

    Returns:
        List of embeddings (same order), None for failures.
    """
    url, headers = _get_api_config()
    if not url:
        return [None] * len(texts)

    # Truncate each text
    truncated = [t[:8000].strip() for t in texts]
    # Filter out empty
    valid_indices = [i for i, t in enumerate(truncated) if t]
    valid_texts = [truncated[i] for i in valid_indices]

    if not valid_texts:
        return [None] * len(texts)

    # Process in chunks of 100 (API limit)
    results = [None] * len(texts)

    for chunk_start in range(0, len(valid_texts), 100):
        chunk = valid_texts[chunk_start:chunk_start + 100]
        chunk_indices = valid_indices[chunk_start:chunk_start + 100]

        for attempt in range(3):
            try:
                response = httpx.post(
                    url,
                    headers=headers,
                    json={
                        "model": EMBEDDING_MODEL,
                        "input": chunk,
                    },
                    timeout=60.0,
                )
                if response.status_code == 429:
                    wait = 10 * (attempt + 1)
                    logger.warning(f"Batch embedding rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue

                response.raise_for_status()
                data = response.json()

                for item in data["data"]:
                    idx = item["index"]
                    original_idx = chunk_indices[idx]
                    results[original_idx] = item["embedding"]
                break

            except Exception as e:
                logger.warning(f"Batch embedding error (attempt {attempt + 1}): {e}")
                time.sleep(3)

        time.sleep(0.5)  # Rate limit between chunks

    return results


def prepare_embedding_text(title: str, body: str, summary: str = "") -> str:
    """Prepare text for embedding from article fields.

    Uses title + summary (preferred) or title + body start.
    """
    parts = [title or ""]
    if summary:
        parts.append(summary)
    elif body:
        # First 1000 chars of body
        parts.append(body[:1000])
    return "\n".join(parts)
