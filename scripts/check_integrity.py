"""Data integrity guard for thread links.

Checks and optionally repairs:
- dangling thread_articles links
- threads without linked articles
- mismatched threads.article_count vs actual links
"""
import argparse
import logging
import time

from sqlalchemy import text

from src.db import get_session, wait_for_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("integrity")


def get_snapshot() -> dict:
    with get_session() as session:
        row = session.execute(text("""
            SELECT
                (SELECT COUNT(*) FROM thread_articles) AS total_links,
                (SELECT COUNT(*)
                 FROM thread_articles ta
                 LEFT JOIN articles a ON a.id = ta.article_id
                 WHERE a.id IS NULL) AS broken_article_links,
                (SELECT COUNT(*)
                 FROM thread_articles ta
                 LEFT JOIN threads t ON t.id = ta.thread_id
                 WHERE t.id IS NULL) AS broken_thread_links,
                (SELECT COUNT(*)
                 FROM threads t
                 WHERE NOT EXISTS (
                     SELECT 1 FROM thread_articles ta WHERE ta.thread_id = t.id
                 )) AS empty_threads,
                (SELECT COUNT(*)
                 FROM (
                     SELECT t.id, COALESCE(cnt.c, 0) AS actual_count, t.article_count AS stored_count
                     FROM threads t
                     LEFT JOIN (
                         SELECT thread_id, COUNT(*) AS c
                         FROM thread_articles
                         GROUP BY thread_id
                     ) cnt ON cnt.thread_id = t.id
                 ) q
                 WHERE COALESCE(stored_count, 0) != actual_count) AS count_mismatches
        """)).fetchone()

    return {
        "total_links": int(row.total_links or 0),
        "broken_article_links": int(row.broken_article_links or 0),
        "broken_thread_links": int(row.broken_thread_links or 0),
        "empty_threads": int(row.empty_threads or 0),
        "count_mismatches": int(row.count_mismatches or 0),
    }


def apply_repairs() -> dict:
    with get_session() as session:
        deleted_links = session.execute(text("""
            DELETE FROM thread_articles ta
            WHERE NOT EXISTS (SELECT 1 FROM articles a WHERE a.id = ta.article_id)
               OR NOT EXISTS (SELECT 1 FROM threads t WHERE t.id = ta.thread_id)
        """)).rowcount

        # Recalculate counters from actual links
        session.execute(text("""
            UPDATE threads t
            SET article_count = COALESCE(ta.cnt, 0)
            FROM (
                SELECT thread_id, COUNT(*) AS cnt
                FROM thread_articles
                GROUP BY thread_id
            ) ta
            WHERE t.id = ta.thread_id
        """))

        session.execute(text("""
            UPDATE threads t
            SET article_count = 0
            WHERE NOT EXISTS (SELECT 1 FROM thread_articles ta WHERE ta.thread_id = t.id)
        """))

        deleted_threads = session.execute(text("""
            DELETE FROM threads t
            WHERE NOT EXISTS (SELECT 1 FROM thread_articles ta WHERE ta.thread_id = t.id)
        """)).rowcount

    return {
        "deleted_links": int(deleted_links or 0),
        "deleted_threads": int(deleted_threads or 0),
    }


def run_once(apply: bool):
    before = get_snapshot()
    logger.info(f"Integrity snapshot(before): {before}")

    if apply and (
        before["broken_article_links"] > 0
        or before["broken_thread_links"] > 0
        or before["empty_threads"] > 0
        or before["count_mismatches"] > 0
    ):
        repaired = apply_repairs()
        logger.info(f"Repairs applied: {repaired}")

    after = get_snapshot()
    logger.info(f"Integrity snapshot(after): {after}")


def main():
    parser = argparse.ArgumentParser(description="GeoPulse integrity checker")
    parser.add_argument("--apply", action="store_true", help="Apply repairs")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=86400, help="Loop interval seconds")
    args = parser.parse_args()

    wait_for_db()

    if args.loop:
        logger.info(f"Starting integrity guard loop (interval: {args.interval}s, apply={args.apply})")
        while True:
            try:
                run_once(apply=args.apply)
            except Exception as e:
                logger.error(f"Integrity check failed: {e}", exc_info=True)
            time.sleep(args.interval)
    else:
        run_once(apply=args.apply)


if __name__ == "__main__":
    main()
