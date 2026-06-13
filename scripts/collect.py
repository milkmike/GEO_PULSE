"""RSS/Web collector script. Runs in a loop or one-shot."""
import argparse
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from sqlalchemy import text

from src.collectors.rss import collect_rss_status
from src.collectors.scraper import scrape_web_status
from src.config import load_sources
from src.countries import COUNTRIES
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

    added = updated = skipped = 0
    with get_session() as session:
        for country_code, country_data in config["countries"].items():
            cc = str(country_code).upper()
            # A bad YAML key (e.g. bare `NO`/`ON` → bool under YAML 1.1, or a typo)
            # must not corrupt the sync — skip codes outside the registry.
            if cc not in COUNTRIES:
                logger.warning(f"Skipping sources for unknown country code: {country_code!r}")
                continue
            for src in country_data.get("sources", []):
                # Each source in its own savepoint: one bad row is logged and
                # skipped instead of aborting (and crash-looping) the whole sync.
                try:
                    with session.begin_nested():
                        # Already present at this exact URL → nothing to do.
                        by_url = session.execute(
                            text("SELECT id FROM sources WHERE url = :url AND country_code = :cc"),
                            {"url": src["url"], "cc": cc},
                        ).fetchone()
                        if by_url:
                            continue

                        # Same (country, name) but a different URL → the YAML url
                        # changed (e.g. a geoblocked feed rewritten to a Google
                        # News wrapper). Update in place + reactivate instead of
                        # inserting a duplicate, so feed fixes actually replace the
                        # dead source on an existing DB.
                        by_name = session.execute(
                            text("""SELECT id FROM sources
                                    WHERE country_code = :cc AND name = :name
                                    ORDER BY id LIMIT 1"""),
                            {"cc": cc, "name": src["name"]},
                        ).fetchone()
                        if by_name:
                            session.execute(
                                text("UPDATE sources SET url = :url, active = TRUE WHERE id = :id"),
                                {"url": src["url"], "id": by_name.id},
                            )
                            updated += 1
                            logger.info(f"Updated source URL: {src['name']} ({cc})")
                            continue

                        lang = src.get("language", "ru")
                        source = Source(
                            name=src["name"],
                            url=src["url"],
                            country_code=cc,
                            source_type=src["type"],
                            weight=src.get("weight", 1.0),
                            language=str(lang) if lang is not None else "ru",
                            config=src.get("config", {}),
                            tier=src.get("tier", "mainstream"),
                            state_affiliated=src.get("state_affiliated", False),
                            propaganda_risk=src.get("propaganda_risk", "low"),
                        )
                        session.add(source)
                        session.flush()
                        added += 1
                        logger.info(f"Added source: {src['name']} ({cc})")
                except Exception as e:
                    skipped += 1
                    logger.warning(f"Skipping source {src.get('name')!r} ({cc}): {e}")

    logger.info(
        f"Sources synced to database ({added} added, {updated} url-updated, {skipped} skipped)"
    )


# Concurrent fetches per collection pass. The pass is network-bound (hundreds of
# feeds, some slow web scrapers); serial collection couldn't keep up with the
# loop interval and starved freshly-added feeds. Fetch in parallel, save serially.
COLLECT_WORKERS = int(os.environ.get("COLLECT_WORKERS", "10"))


def _fetch_source(source) -> tuple[list[dict], str, str]:
    """Fetch one source (network-bound; safe to run in a thread pool)."""
    if source.source_type == "rss":
        return collect_rss_status(source.url, source.name)
    if source.source_type == "web":
        return scrape_web_status(source.url, source.name)
    return [], "unknown_type", f"unknown source_type={source.source_type!r}"


def _save_source(source, articles: list[dict]) -> tuple[int, int, int]:
    """Persist a source's fetched articles. Returns (new, dupes, skipped)."""
    new_count = dupe_count = skipped = 0
    with get_session() as session:
        for art in articles:
            # Exact duplicate (same source + external_id) → skip.
            existing = session.execute(
                text("SELECT id FROM articles WHERE source_id = :sid AND external_id = :eid"),
                {"sid": source.id, "eid": art["external_id"]},
            ).fetchone()
            if existing:
                skipped += 1
                continue

            cleaned_title = clean_title(art.get("title"))
            if cleaned_title is None:
                skipped += 1
                continue

            title_norm = normalize_title(cleaned_title)
            published_at = art["published_at"]

            parent_id = None
            if title_norm and len(title_norm) >= 10:
                parent_id = find_duplicate(session, title_norm, source.country_code, published_at)

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
                        language=_detect_language(art["title"]),
                        title_normalized=title_norm,
                        is_duplicate=parent_id is not None,
                        duplicate_of=parent_id,
                        is_backfill=is_backfill,
                    )
                    session.add(article)
                    session.flush()  # Get article.id for Redis enqueue

                    if parent_id:
                        session.execute(
                            text("UPDATE articles SET reprint_count = reprint_count + 1 WHERE id = :pid"),
                            {"pid": parent_id},
                        )
                        dupe_count += 1
                    else:
                        new_count += 1
                        _enqueue_article(article.id, source.country_code)
            except Exception as e:
                skipped += 1
                logger.warning(
                    f"  [{source.country_code}] Failed to persist '{cleaned_title[:80]}': {e}"
                )
                continue
    return new_count, dupe_count, skipped


def collect_all():
    """Collect from all active sources — fetch concurrently, save serially."""
    with get_session() as session:
        sources = session.execute(
            text("SELECT id, name, url, country_code, source_type, weight FROM sources WHERE active = true")
        ).fetchall()

    total_new = total_skipped = total_dupes = 0
    logger.info(f"Collecting {len(sources)} sources ({COLLECT_WORKERS} workers)...")

    with ThreadPoolExecutor(max_workers=COLLECT_WORKERS) as ex:
        future_map = {ex.submit(_fetch_source, s): s for s in sources}
        for fut in as_completed(future_map):
            source = future_map[fut]
            try:
                articles, fetch_status, fetch_error = fut.result()
            except Exception as e:  # noqa: BLE001
                articles, fetch_status, fetch_error = [], "error", str(e)[:160]

            # Record status (own session) then save serially (own session) —
            # only the network fetch above ran in parallel.
            _record_fetch(source.id, fetch_status, fetch_error)
            new_count, dupe_count, skipped = _save_source(source, articles)
            total_new += new_count
            total_dupes += dupe_count
            total_skipped += skipped
            if new_count or dupe_count:
                logger.info(
                    f"  {source.name} ({source.country_code}): "
                    f"{new_count} new, {dupe_count} dupes [{fetch_status}]"
                )

    logger.info(
        f"Collection complete: {total_new} new articles, {total_dupes} cross-source dupes, "
        f"{total_skipped} skipped"
    )

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
