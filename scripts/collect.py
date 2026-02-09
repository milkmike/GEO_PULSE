"""RSS/Web collector script. Runs in a loop or one-shot."""
import argparse
import logging
import time
from datetime import datetime, timezone

from sqlalchemy import text

from src.collectors.rss import collect_rss
from src.collectors.scraper import scrape_web
from src.config import load_sources
from src.db import get_session, wait_for_db, Source, Article
from src.pipeline.dedup import normalize_title, find_duplicate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("collector")


def ensure_sources_in_db():
    """Sync sources from YAML config to database."""
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
                    )
                    session.add(source)
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
            articles = collect_rss(source.url, source.name)
        elif source.source_type == "web":
            articles = scrape_web(source.url, source.name)
        else:
            logger.warning(f"Unknown source type: {source.source_type}")
            continue

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

                # Normalize title for fuzzy dedup
                title_norm = normalize_title(art["title"])
                published_at = art["published_at"]

                # Check for cross-source duplicate
                parent_id = None
                if title_norm and len(title_norm) >= 10:
                    parent_id = find_duplicate(
                        session, title_norm, source.country_code, published_at
                    )

                article = Article(
                    source_id=source.id,
                    external_id=art["external_id"],
                    title=art["title"],
                    body=art.get("body", ""),
                    url=art.get("url", ""),
                    published_at=published_at,
                    language=source.country_code.lower() if hasattr(source, 'language') else "ru",
                    title_normalized=title_norm,
                    is_duplicate=parent_id is not None,
                    duplicate_of=parent_id,
                )
                session.add(article)

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
