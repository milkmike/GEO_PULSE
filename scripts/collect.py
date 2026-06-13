"""RSS/Web collector script. Runs in a loop or one-shot."""
import argparse
import logging
import os
import re
import time
from datetime import datetime, timezone

from sqlalchemy import text

from src.collectors.rss import collect_rss_status
from src.collectors.scraper import scrape_web_status
from src.config import load_sources
from src.db import get_session, wait_for_db, Source, Article
from src.pipeline.dedup import normalize_title, find_duplicate
from src.pipeline.title_cleaner import clean_title

# Language detection helpers
import unicodedata
_CYRILLIC_RE = re.compile(r'[Ѐ-ӿ]')
_LATIN_RE = re.compile(r'[A-Za-zÀ-ÿ]')
_RO_CHARS = set('ăâîșțĂÂÎȘȚ')
_UZ_MARKERS = re.compile(r"(o'z|O'z|bilan|haqida|uchun|bo'yicha)", re.IGNORECASE)
_TK_MARKERS = re.compile(r'[ňžäýöüŇŽÄÝÖÜ]|(barada|döwlet|türkmen)', re.IGNORECASE)

def _detect_language(title):
    if not title or len(title.strip()) < 3:
        return 'ru'
    cyrillic_count = len(_CYRILLIC_RE.findall(title))
    latin_count = len(_LATIN_RE.findall(title))
    total = cyrillic_count + latin_count
    if total == 0:
        return 'ru'
    if latin_count / total >= 0.5 and cyrillic_count <= 2:
        if any(c in _RO_CHARS for c in title):
            return 'ro'
        if _UZ_MARKERS.search(title):
            return 'uz'
        if _TK_MARKERS.search(title):
            return 'tk'
        return 'en'
    return 'ru'


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("collector")

SKIP_YAML_SYNC = os.environ.get("SKIP_YAML_SYNC", "0") == "1"

# Articles older than this threshold are marked as backfill
BACKFILL_HOURS = int(os.environ.get("BACKFILL_HOURS", "48"))

# Auto-quarantine: disable a source after N consecutive failed fetches.
# 0 (default) = never auto-disable — only record status (instrumentation only).
QUARANTINE_AFTER = int(os.environ.get("SOURCE_QUARANTINE_AFTER", "0"))

# Redis integration (optional, graceful fallback)
_redis_available = False
try:
    from src.queue import enqueue, Q_RAW_ARTICLES, get_redis
    _redis_available = True
except ImportError:
    logger.info("Redis queue module not available, running without queue")


def _enqueue_article(article_id: int, country_code: str):
    """Enqueue article to Redis queue. Silently fails if Redis is down."""
    if not _redis_available:
        return
    try:
        enqueue(Q_RAW_ARTICLES, {"article_id": article_id, "country_code": country_code})
    except Exception as e:
        logger.warning(f"Failed to enqueue article {article_id} to Redis: {e}")


def _record_fetch(source_id: int, status: str, error: str):
    """Persist the fetch outcome on the source (why it's quiet) + optional quarantine.

    Runs in its own session and never raises — instrumentation must not break
    collection (e.g. on a DB that predates migration 015)."""
    try:
        with get_session() as session:
            if status == "ok":
                session.execute(
                    text("""UPDATE sources SET last_fetch_at = NOW(), last_status = 'ok',
                                last_error = NULL, consecutive_failures = 0
                            WHERE id = :id"""),
                    {"id": source_id},
                )
            else:
                session.execute(
                    text("""UPDATE sources SET last_fetch_at = NOW(), last_status = :st,
                                last_error = :err,
                                consecutive_failures = consecutive_failures + 1
                            WHERE id = :id"""),
                    {"id": source_id, "st": status[:24], "err": (error or "")[:1000]},
                )
                if QUARANTINE_AFTER > 0:
                    res = session.execute(
                        text("""UPDATE sources SET active = FALSE
                                WHERE id = :id AND active = TRUE
                                  AND consecutive_failures >= :n"""),
                        {"id": source_id, "n": QUARANTINE_AFTER},
                    )
                    if res.rowcount:
                        logger.warning(
                            f"  Quarantined source {source_id}: {status} "
                            f"≥{QUARANTINE_AFTER} consecutive failures"
                        )
    except Exception as e:
        logger.warning(f"Could not record fetch status for source {source_id}: {e}")


def _update_collector_stats():
    """Update collector stats in Redis. Silently fails if Redis is down."""
    if not _redis_available:
        return
    try:
        r = get_redis()
        r.set("stats:collector:last_run", datetime.now(timezone.utc).isoformat())
    except Exception as e:
        logger.warning(f"Failed to update collector stats in Redis: {e}")


def ensure_sources_in_db():
    """Sync sources from YAML config to database."""
    if SKIP_YAML_SYNC:

        return

    config = load_sources()
    
    with get_session() as session:
        for country_code, country_data in config["countries"].items():
            for src in country_data["sources"]:
                # Check if source exists
                existing = session.execute(
                    text("SELECT id FROM sources WHERE url = :url AND country_code = :cc"),
                    {"url": src["url"], "cc": country_code},
                ).fetchone()

                if not existing:
                    source = Source(
                        name=src["name"],
                        url=src["url"],
                        country_code=country_code,
                        source_type=src["type"],
                        weight=src.get("weight", 1.0),
                        language=src.get("language", "ru"),
                        config=src.get("config", {}),
                        tier=src.get("tier", "mainstream"),
                        state_affiliated=src.get("state_affiliated", False),
                        propaganda_risk=src.get("propaganda_risk", "low"),
                    )
                    session.add(source)
                    session.flush()
                    logger.info(f"Added source: {src['name']} ({country_code})")

    logger.info("Sources synced to database")


def collect_all():
    """Collect articles from all active sources."""
    with get_session() as session:
        sources = session.execute(
            text("SELECT id, name, url, country_code, source_type, weight FROM sources WHERE active = true")
        ).fetchall()

    total_new = 0
    total_skipped = 0
    total_dupes = 0

    for source in sources:
        logger.info(f"Collecting from {source.name} ({source.country_code}, {source.source_type})...")

        if source.source_type == "rss":
            articles, fetch_status, fetch_error = collect_rss_status(source.url, source.name)
        elif source.source_type == "web":
            articles, fetch_status, fetch_error = scrape_web_status(source.url, source.name)
        else:
            logger.warning(f"Unknown source type: {source.source_type}")
            continue

        # Record why this source is quiet (or that it's healthy) + optional quarantine.
        _record_fetch(source.id, fetch_status, fetch_error)

        # Save articles
        new_count = 0
        dupe_count = 0
        with get_session() as session:
            for art in articles:
                # Check for exact duplicate (same source + external_id)
                existing = session.execute(
                    text("SELECT id FROM articles WHERE source_id = :sid AND external_id = :eid"),
                    {"sid": source.id, "eid": art["external_id"]},
                ).fetchone()

                if existing:
                    total_skipped += 1
                    continue

                # Clean and validate title
                cleaned_title = clean_title(art.get("title"))
                if cleaned_title is None:
                    logger.debug(f"  [{source.country_code}] Skipped garbage title: {art.get('title', '')[:60]}")
                    total_skipped += 1
                    continue

                # Normalize title for fuzzy dedup
                title_norm = normalize_title(cleaned_title)
                published_at = art["published_at"]

                # Check for cross-source duplicate
                parent_id = None
                if title_norm and len(title_norm) >= 10:
                    parent_id = find_duplicate(
                        session, title_norm, source.country_code, published_at
                    )

                # Mark as backfill if article is older than threshold
                age_hours = (datetime.now(timezone.utc) - published_at).total_seconds() / 3600
                is_backfill = age_hours > BACKFILL_HOURS

                try:
                    with session.begin_nested():
                        article = Article(
                            source_id=source.id,
                            external_id=art["external_id"],
                            title=cleaned_title,
                            body=art.get("body", ""),
                            url=art.get("url", ""),
                            published_at=published_at,
                            language=_detect_language(art['title']),
                            title_normalized=title_norm,
                            is_duplicate=parent_id is not None,
                            duplicate_of=parent_id,
                            is_backfill=is_backfill,
                        )
                        session.add(article)
                        session.flush()  # Get article.id for Redis enqueue

                        if parent_id:
                            # Increment reprint_count on the parent article
                            session.execute(
                                text("UPDATE articles SET reprint_count = reprint_count + 1 WHERE id = :pid"),
                                {"pid": parent_id},
                            )
                            dupe_count += 1
                            logger.info(
                                f"  [{source.country_code}] DUPE: \"{art['title'][:60]}...\" → parent #{parent_id}"
                            )
                        else:
                            new_count += 1
                            # Enqueue new non-duplicate articles to Redis for analysis
                            _enqueue_article(article.id, source.country_code)
                except Exception as e:
                    total_skipped += 1
                    logger.warning(
                        f"  [{source.country_code}] Failed to persist article '{cleaned_title[:80]}': {e}"
                    )
                    continue

        total_new += new_count
        total_dupes += dupe_count
        logger.info(
            f"  → {new_count} new, {dupe_count} dupes "
            f"(skipped {len(articles) - new_count - dupe_count} exact duplicates)"
        )

    logger.info(
        f"Collection complete: {total_new} new articles, {total_dupes} cross-source dupes, "
        f"{total_skipped} exact duplicates skipped"
    )

    # Update Redis stats after collection run
    _update_collector_stats()

    return total_new


def main():
    parser = argparse.ArgumentParser(description="CIS Thermometer Collector")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=1800, help="Loop interval in seconds")
    args = parser.parse_args()

    wait_for_db()
    ensure_sources_in_db()

    if args.loop:
        logger.info(f"Starting collector loop (interval: {args.interval}s)")
        while True:
            try:
                collect_all()
            except Exception as e:
                logger.error(f"Collection error: {e}", exc_info=True)
            logger.info(f"Sleeping {args.interval}s until next collection...")
            time.sleep(args.interval)
    else:
        collect_all()


if __name__ == "__main__":
    main()
