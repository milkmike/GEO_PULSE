"""API usage tracker — logs every external API call to the api_usage table.

Usage:
    from src.api_tracker import track_api_call

    track_api_call(
        service="openrouter",
        endpoint="/chat/completions",
        model="anthropic/claude-sonnet-4",
        script="analyze.py",
        tokens_in=1500,
        tokens_out=300,
        cost=None,          # auto-calculated if None
        status="ok",
        error=None,
        duration_ms=1200,
    )
"""
import logging
import os
import time
from contextlib import contextmanager
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)

# ── Cost tables (per 1M tokens) ────────────────────────

COST_TABLE = {
    # service -> model -> (input_per_1M, output_per_1M) in USD
    "openrouter": {
        "anthropic/claude-sonnet-4": (3.0, 15.0),
        "anthropic/claude-3.5-sonnet": (3.0, 15.0),
        # Cheap heavy/structured models (HEAVY_MODEL) — see src/config.py
        "xiaomi/mimo-v2.5-pro": (0.435, 0.87),
        "xiaomi/mimo-v2.5": (0.14, 0.28),
        "deepseek/deepseek-v3.2": (0.23, 0.34),
        "deepseek/deepseek-v4-flash": (0.09, 0.18),
        "moonshotai/kimi-k2.6": (0.68, 3.41),
        # Main analyzer/briefs chain (src/llm.py DEFAULT_MODELS)
        "google/gemini-3-flash-preview": (0.50, 3.00),
        "google/gemini-2.0-flash-001": (0.10, 0.40),
        "meta-llama/llama-3.3-70b-instruct": (0.12, 0.30),
    },
    "openai": {
        "text-embedding-3-small": (0.02, 0.0),
    },
    "jina": {
        "jina-embeddings-v3": (0.0, 0.0),  # free tier
    },
    "comtrade": {
        "": (0.0, 0.0),  # free
    },
}


def calculate_cost(
    service: str, model: str, tokens_in: int, tokens_out: int
) -> Decimal:
    """Calculate USD cost from token counts."""
    svc_costs = COST_TABLE.get(service, {})
    rates = svc_costs.get(model)
    if not rates:
        # Try partial match
        for m, r in svc_costs.items():
            if m and m in (model or ""):
                rates = r
                break
    if not rates:
        return Decimal("0")

    input_cost = Decimal(str(tokens_in)) * Decimal(str(rates[0])) / Decimal("1000000")
    output_cost = Decimal(str(tokens_out)) * Decimal(str(rates[1])) / Decimal("1000000")
    return input_cost + output_cost


def _get_db_url() -> str:
    return os.environ.get(
        "DATABASE_URL",
        "postgresql://thermo:thermo@localhost:5432/cis_thermometer",
    )


def track_api_call(
    service: str,
    endpoint: str = "",
    model: str = "",
    script: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost: Optional[float] = None,
    status: str = "ok",
    error: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> None:
    """Log an API call to the api_usage table. Never raises — fails silently."""
    try:
        if cost is None:
            cost_decimal = calculate_cost(service, model or "", tokens_in, tokens_out)
        else:
            cost_decimal = Decimal(str(cost))

        # Use raw psycopg2 to avoid importing the heavy SQLAlchemy session
        # (this function is called from many scripts, some use psycopg2 directly)
        import psycopg2

        db_url = _get_db_url()
        # Parse URL
        import re
        m = re.match(r"postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)", db_url)
        if not m:
            logger.warning(f"Cannot parse DATABASE_URL for api_tracker")
            return

        conn = psycopg2.connect(
            host=m.group(3), port=int(m.group(4)),
            user=m.group(1), password=m.group(2), dbname=m.group(5),
        )
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO api_usage
                   (service, endpoint, model, script, tokens_in, tokens_out,
                    cost_usd, status, error_message, duration_ms)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    service, endpoint, model, script,
                    tokens_in, tokens_out, float(cost_decimal),
                    status, error, duration_ms,
                ),
            )
            conn.commit()
            cur.close()
        finally:
            conn.close()
    except Exception as e:
        # Never break the main flow
        logger.debug(f"api_tracker: failed to log call: {e}")


@contextmanager
def track_duration():
    """Context manager to measure duration in ms.

    Usage:
        with track_duration() as t:
            ... do work ...
        duration_ms = t.ms
    """
    class Timer:
        def __init__(self):
            self.start = time.time()
            self.ms = 0
        def stop(self):
            self.ms = int((time.time() - self.start) * 1000)

    timer = Timer()
    try:
        yield timer
    finally:
        timer.stop()
