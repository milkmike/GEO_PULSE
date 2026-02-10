"""Redis-based job queue for the pipeline."""
import json
import logging
import os
from datetime import datetime, timezone

import redis

logger = logging.getLogger(__name__)

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")

_pool = None


def get_redis():
    global _pool
    if _pool is None:
        _pool = redis.ConnectionPool.from_url(REDIS_URL)
    return redis.Redis(connection_pool=_pool)


# Queue names
Q_RAW_ARTICLES = "queue:raw_articles"
Q_ANALYZED = "queue:analyzed"
Q_DEAD_LETTER = "queue:dead_letter"


def enqueue(queue_name: str, data: dict):
    """Push a job to the queue."""
    r = get_redis()
    payload = json.dumps(data, default=str)
    r.lpush(queue_name, payload)


def dequeue(queue_name: str, timeout: int = 5) -> dict | None:
    """Pop a job from the queue (blocking)."""
    r = get_redis()
    result = r.brpop(queue_name, timeout=timeout)
    if result:
        _, payload = result
        return json.loads(payload)
    return None


def queue_length(queue_name: str) -> int:
    r = get_redis()
    return r.llen(queue_name)


def get_pipeline_stats() -> dict:
    """Get current pipeline queue stats."""
    r = get_redis()
    return {
        "raw_articles_pending": r.llen(Q_RAW_ARTICLES),
        "analyzed_pending": r.llen(Q_ANALYZED),
        "dead_letter": r.llen(Q_DEAD_LETTER),
        "collector_last_run": (r.get("stats:collector:last_run") or b"").decode(),
        "analyzer_last_run": (r.get("stats:analyzer:last_run") or b"").decode(),
        "analyzer_today_count": int(r.get("stats:analyzer:today_count") or 0),
        "analyzer_today_cost_usd": float(r.get("stats:analyzer:today_cost") or 0),
    }
