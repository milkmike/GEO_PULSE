"""NLP analysis pipeline. Processes unanalyzed articles with parallel requests."""
import argparse
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from src.config import OPENROUTER_API_KEY
from src.db import get_session, wait_for_db, Analysis
from src.embeddings import generate_embeddings_batch, prepare_embedding_text
from src.entities import match_entities
from src.knowledge import upsert_analysis_mentions
from src.pipeline.filter import is_relevant
from src.pipeline.sentiment import analyze_sentiment

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("analyzer")

MAX_WORKERS = 5
QUEUE_PROCESSED = "processed"
QUEUE_EMPTY = "empty"
QUEUE_UNAVAILABLE = "unavailable"
EMPTY_QUEUE_FALLBACK_INTERVAL = timedelta(hours=1)
_last_empty_queue_fallback_at: datetime | None = None

# Redis integration (optional, graceful fallback)
_redis_available = False
try:
    from src.queue import dequeue, enqueue, Q_RAW_ARTICLES, Q_DEAD_LETTER, get_redis
    _redis_available = True
except ImportError:
    logger.info("Redis queue module not available, running without queue")


def _save_analysis(session, result: dict) -> Analysis:
    """Flush one analysis and dual-write mentions in the same transaction."""

    analysis = Analysis(**result)
    session.add(analysis)
    session.flush()
    try:
        upsert_analysis_mentions(
            session,
            analysis.id,
            analysis.article_id,
            analysis.entities,
        )
    except Exception:
        logger.exception(
            "Canonical mention live-write failed",
            extra={
                "analysis_id": analysis.id,
                "article_id": analysis.article_id,
            },
        )
        raise
    return analysis


def _analyze_one(row) -> dict | None:
    """Analyze a single article. Returns dict to save or None."""
    title = row.title or ""
    body = row.body or ""

    # Step 1: Keyword filter (free)
    relevant = is_relevant(title, body)

    if not relevant:
        return {
            "article_id": row.id,
            "is_relevant": False,
            "relevance_score": 0.0,
        }

    # Step 2: LLM sentiment analysis
    if OPENROUTER_API_KEY:
        try:
            result = analyze_sentiment(
                title=title,
                body=body,
                source_name=row.source_name,
                country_code=row.country_code,
            )
            if result:
                # LLM said not relevant
                if result.get("is_relevant") is False:
                    logger.info(f"  [{row.country_code}] {title[:60]}... → LLM: not relevant")
                    return {
                        "article_id": row.id,
                        "is_relevant": False,
                        "relevance_score": 0.0,
                        "model_used": result.get("model_used", ""),
                        "prompt_version": result.get("prompt_version", ""),
                        "raw_response": result.get("raw_response"),
                    }
                logger.info(
                    f"  [{row.country_code}] {title[:60]}... → "
                    f"sentiment={result['sentiment']}, type={result['event_type']}, "
                    f"action={result['action_level']}"
                )
                time.sleep(0.3)  # rate limit
                return {
                    "article_id": row.id,
                    "is_relevant": True,
                    "relevance_score": 1.0,
                    "sentiment": result["sentiment"],
                    "sentiment_confidence": result["confidence"],
                    "event_type": result["event_type"],
                    "topics": result.get("topics") or None,
                    "entities": match_entities(title, body) or None,
                    "action_level": result["action_level"],
                    "model_used": result["model_used"],
                    "prompt_version": result["prompt_version"],
                    "event_key": result.get("event_key", ""),
                    "raw_response": result["raw_response"],
                }
        except Exception as e:
            logger.error(f"  LLM analysis failed for article {row.id}: {e}")

    # Fallback
    return {
        "article_id": row.id,
        "is_relevant": True,
        "relevance_score": 0.8,
        "model_used": "keyword_filter",
        "prompt_version": "v1.1",
    }


def _analyze_article_by_id(article_id: int) -> bool:
    """Analyze a single article by ID (for Redis queue mode)."""
    with get_session() as session:
        row = session.execute(
            text("""
                SELECT ar.id, ar.title, ar.body, ar.source_id,
                       source.name as source_name, source.country_code, source.weight
                FROM articles ar
                JOIN article_country_facts source ON source.article_id = ar.id
                LEFT JOIN analysis an ON an.article_id = ar.id
                WHERE ar.id = :aid AND an.id IS NULL AND ar.is_duplicate = FALSE
            """),
            {"aid": article_id},
        ).fetchone()

    if not row:
        logger.debug(f"Article {article_id} already analyzed or is duplicate, skipping")
        return True  # Not an error, just already done

    result = _analyze_one(row)
    if result:
        with get_session() as session:
            _save_analysis(session, result)
        return True
    return False


def process_from_queue() -> str:
    """Process one Redis job and report whether its transport had work."""
    if not _redis_available:
        return QUEUE_UNAVAILABLE

    try:
        job = dequeue(Q_RAW_ARTICLES, timeout=5)
    except Exception as e:
        logger.warning(f"Redis dequeue failed: {e}")
        return QUEUE_UNAVAILABLE

    if not job:
        return QUEUE_EMPTY

    article_id = job["article_id"]
    try:
        _analyze_article_by_id(article_id)
        # Update stats
        try:
            r = get_redis()
            r.incr("stats:analyzer:today_count")
            r.set("stats:analyzer:last_run", datetime.now(timezone.utc).isoformat())
        except Exception:
            pass  # Stats update is non-critical
        return QUEUE_PROCESSED
    except Exception as e:
        logger.error(f"Failed to analyze article {article_id}: {e}")
        try:
            enqueue(Q_DEAD_LETTER, {**job, "error": str(e)})
        except Exception:
            pass
        # A dequeued job means the queue was not empty, even if its work failed.
        return QUEUE_PROCESSED


def load_unanalyzed_candidate_ids(
    session,
    *,
    batch_size: int,
    scan_limit: int,
) -> list[int]:
    """Return a bounded newest-first ID window without touching analysis history."""
    if batch_size < 1 or scan_limit < batch_size:
        raise ValueError("scan_limit must be at least batch_size and both must be positive")
    rows = session.execute(
        text(f"""
            WITH candidate_ids AS MATERIALIZED (
                SELECT ar.id, ar.collected_at
                FROM articles ar
                WHERE ar.is_duplicate = FALSE
                  AND ar.geo_country_code IS NOT NULL
                  AND ar.geo_status IN ('source_verified', 'publisher_verified', 'publisher_reassigned')
                ORDER BY ar.collected_at DESC, ar.id DESC
                LIMIT :scan_limit
            )
            SELECT candidate_ids.id
            FROM candidate_ids
            WHERE NOT EXISTS (
                SELECT 1 FROM analysis an WHERE an.article_id = candidate_ids.id
            )
            ORDER BY candidate_ids.collected_at DESC, candidate_ids.id DESC
            LIMIT {batch_size}
        """),
        {"scan_limit": scan_limit},
    ).fetchall()
    return [int(row.id if hasattr(row, "id") else row[0]) for row in rows]


def load_unanalyzed_rows(session, article_ids: list[int]) -> list:
    """Load selected IDs only after the bounded scan, with canonical attribution."""
    if not article_ids:
        return []
    return session.execute(
        text("""
            SELECT ar.id, ar.title, ar.body, ar.source_id,
                   source.name AS source_name, source.country_code, source.weight
            FROM articles ar
            JOIN article_country_facts source ON source.article_id = ar.id
            WHERE ar.id = ANY(CAST(:article_ids AS integer[]))
              AND NOT EXISTS (SELECT 1 FROM analysis an WHERE an.article_id = ar.id)
            ORDER BY ar.collected_at DESC, ar.id DESC
        """),
        {"article_ids": article_ids},
    ).fetchall()


def analyze_new_articles(batch_size: int = 100):
    """Find and analyze only a bounded recent window of unprocessed articles."""
    scan_limit = max(batch_size * 20, 1000)
    with get_session() as session:
        candidate_ids = load_unanalyzed_candidate_ids(
            session,
            batch_size=batch_size,
            scan_limit=scan_limit,
        )
        rows = load_unanalyzed_rows(session, candidate_ids)

    if not rows:
        logger.info("No new articles to analyze")
        return 0

    logger.info(f"Analyzing {len(rows)} new articles (parallel workers: {MAX_WORKERS})...")

    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_row = {executor.submit(_analyze_one, row): row for row in rows}
        for future in as_completed(future_to_row):
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception as e:
                row = future_to_row[future]
                logger.error(f"  Worker error for article {row.id}: {e}")

    # Save all results
    saved = 0
    saved_article_ids = []
    with get_session() as session:
        for r in results:
            try:
                _save_analysis(session, r)
                saved += 1
                if r.get("is_relevant"):
                    saved_article_ids.append(r["article_id"])
            except Exception as e:
                logger.error(f"  Save error for article {r['article_id']}: {e}")
                raise

    logger.info(f"Analyzed {saved}/{len(rows)} articles")

    # Generate embeddings for relevant articles
    if saved_article_ids:
        _generate_embeddings_for_articles(saved_article_ids, rows)

    # Update Redis stats
    if _redis_available:
        try:
            redis_client = get_redis()
            redis_client.set("stats:analyzer:last_run", datetime.now(timezone.utc).isoformat())
            redis_client.incrby("stats:analyzer:today_count", saved)
        except Exception:
            pass

    return saved


def run_loop_iteration(batch_size: int, *, now: datetime | None = None) -> int | None:
    """Use the bounded DB fallback only after a confirmed, rate-limited empty queue."""
    global _last_empty_queue_fallback_at

    outcome = process_from_queue()
    if outcome == QUEUE_PROCESSED:
        return 1
    if outcome == QUEUE_UNAVAILABLE:
        logger.warning("Redis queue unavailable; skipping database fallback")
        return None

    now = now or datetime.now(timezone.utc)
    if (
        _last_empty_queue_fallback_at is not None
        and now - _last_empty_queue_fallback_at < EMPTY_QUEUE_FALLBACK_INTERVAL
    ):
        logger.debug("Queue empty; bounded database fallback is rate-limited")
        return None
    _last_empty_queue_fallback_at = now
    return analyze_new_articles(batch_size)


def _generate_embeddings_for_articles(article_ids: list[int], rows: list):
    """Generate and save embeddings for a batch of articles."""
    # Build id→row lookup
    row_map = {r.id: r for r in rows}

    texts = []
    valid_ids = []
    for aid in article_ids:
        row = row_map.get(aid)
        if row:
            text_for_embed = prepare_embedding_text(
                title=row.title or "",
                body=row.body or "",
                summary="",  # summary not always available at this point
            )
            if text_for_embed.strip():
                texts.append(text_for_embed)
                valid_ids.append(aid)

    if not texts:
        return

    logger.info(f"Generating embeddings for {len(texts)} articles...")
    embeddings = generate_embeddings_batch(texts)

    embedded_count = 0
    with get_session() as session:
        for aid, emb in zip(valid_ids, embeddings):
            if emb is not None:
                # pgvector expects a list/string representation
                emb_str = "[" + ",".join(str(x) for x in emb) + "]"
                session.execute(
                    text("UPDATE analysis SET embedding = :emb WHERE article_id = :aid"),
                    {"emb": emb_str, "aid": aid},
                )
                embedded_count += 1

    logger.info(f"Saved {embedded_count}/{len(texts)} embeddings")


def main():
    parser = argparse.ArgumentParser(description="CIS Thermometer Analyzer")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=60, help="Loop interval in seconds")
    parser.add_argument("--batch", type=int, default=100, help="Batch size")
    args = parser.parse_args()

    wait_for_db()

    if not OPENROUTER_API_KEY:
        logger.warning("⚠️  OPENROUTER_API_KEY not set! Sentiment analysis will be skipped.")

    if args.loop:
        logger.info(f"Starting analyzer loop (interval: {args.interval}s, batch: {args.batch})")
        while True:
            try:
                batch_processed = run_loop_iteration(args.batch)
                if batch_processed == 1:
                    time.sleep(0.5)
                    continue
                # If we processed a full batch, there might be more - run again quickly
                if batch_processed is not None and batch_processed >= args.batch:
                    logger.info("Full batch processed, running again immediately...")
                    continue
            except Exception as e:
                logger.error(f"Analysis error: {e}", exc_info=True)
            time.sleep(args.interval)
    else:
        analyze_new_articles(args.batch)


if __name__ == "__main__":
    main()
