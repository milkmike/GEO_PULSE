"""Embedding generation for semantic article clustering.

Supports multiple backends:
1. OpenAI API (text-embedding-3-small, 1536 dims)
2. Jina AI (jina-embeddings-v3, 1024 dims) — no geo restrictions
3. OpenRouter fallback

Architecture note: this module is designed to be swappable — future Graphiti
integration would add a graph-based enrichment layer on top of embeddings.
"""
import json
import logging
import os
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Embedding configuration — auto-detect best available backend
EMBEDDING_DIM = None  # Set dynamically based on backend


def _get_api_config() -> tuple[str, dict, str, int]:
    """Return (url, headers, model, dimensions) for embedding API.

    Priority: Jina AI → OpenAI → OpenRouter
    """
    # Option 1: Jina AI (no geo restrictions, great multilingual)
    jina_key = os.environ.get("JINA_API_KEY", "")
    if jina_key:
        return "https://api.jina.ai/v1/embeddings", {
            "Authorization": f"Bearer {jina_key}",
            "Content-Type": "application/json",
        }, "jina-embeddings-v3", 1024

    # Option 2: OpenAI direct
    openai_key = os.environ.get("OPENAI_API_KEY", "")
    if openai_key:
        return "https://api.openai.com/v1/embeddings", {
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json",
        }, "text-embedding-3-small", 1536

    # Option 3: OpenRouter (limited embedding support)
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        return "https://openrouter.ai/api/v1/embeddings", {
            "Authorization": f"Bearer {openrouter_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://cis-thermometer.app",
        }, "openai/text-embedding-3-small", 1536

    return "", {}, "", 0


def get_embedding_dim() -> int:
    """Return the embedding dimension for the current backend."""
    _, _, _, dim = _get_api_config()
    return dim or 1536


def generate_embedding(text: str) -> Optional[list[float]]:
    """Generate embedding for a single text string.

    Args:
        text: Input text (title + summary/body). Truncated to ~8000 chars.

    Returns:
        List of floats (dimension depends on backend), or None on failure.
    """
    url, headers, model, dim = _get_api_config()
    if not url:
        logger.warning("No embedding API configured (set JINA_API_KEY or OPENAI_API_KEY)")
        return None

    text = text[:8000].strip()
    if not text:
        return None

    for attempt in range(3):
        try:
            payload = {"model": model, "input": [text]}
            # Jina-specific: task type for better quality
            if "jina" in model:
                payload["task"] = "text-matching"

            response = httpx.post(url, headers=headers, json=payload, timeout=30.0)

            if response.status_code == 429:
                wait = 5 * (attempt + 1)
                logger.warning(f"Embedding rate limited, waiting {wait}s")
                time.sleep(wait)
                continue

            response.raise_for_status()
            data = response.json()

            if "data" not in data or not data["data"]:
                logger.warning(f"No embedding data in response: {str(data)[:200]}")
                return None

            embedding = data["data"][0]["embedding"]
            return embedding

        except Exception as e:
            logger.warning(f"Embedding error (attempt {attempt + 1}): {e}")
            time.sleep(2)

    return None


def generate_embeddings_batch(texts: list[str]) -> list[Optional[list[float]]]:
    """Generate embeddings for a batch of texts.

    Args:
        texts: List of input texts.

    Returns:
        List of embeddings (same order), None for failures.
    """
    url, headers, model, dim = _get_api_config()
    if not url:
        logger.warning("No embedding API configured")
        return [None] * len(texts)

    truncated = [t[:8000].strip() for t in texts]
    valid_indices = [i for i, t in enumerate(truncated) if t]
    valid_texts = [truncated[i] for i in valid_indices]

    if not valid_texts:
        return [None] * len(texts)

    results = [None] * len(texts)

    # Jina supports up to 2048 inputs, OpenAI up to 100
    chunk_size = 50 if "jina" in model else 100

    for chunk_start in range(0, len(valid_texts), chunk_size):
        chunk = valid_texts[chunk_start:chunk_start + chunk_size]
        chunk_indices = valid_indices[chunk_start:chunk_start + chunk_size]

        for attempt in range(3):
            try:
                payload = {"model": model, "input": chunk}
                if "jina" in model:
                    payload["task"] = "text-matching"

                response = httpx.post(url, headers=headers, json=payload, timeout=60.0)

                if response.status_code == 429:
                    wait = 10 * (attempt + 1)
                    logger.warning(f"Batch embedding rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue

                response.raise_for_status()
                data = response.json()

                if "data" not in data:
                    logger.warning(f"No data in batch response: {str(data)[:200]}")
                    break

                for item in data["data"]:
                    idx = item["index"]
                    if idx < len(chunk_indices):
                        original_idx = chunk_indices[idx]
                        results[original_idx] = item["embedding"]

                logger.info(f"  Batch {chunk_start//chunk_size + 1}: {len(data['data'])} embeddings")
                break

            except Exception as e:
                logger.warning(f"Batch embedding error (attempt {attempt + 1}): {e}")
                time.sleep(3)

        time.sleep(0.5)

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
