"""Retrospective deduplication of existing articles.

Run once to normalize titles and mark duplicates among existing articles.
"""
import logging
import sys
import time

from sqlalchemy import text

from src.db import get_session, wait_for_db
from src.pipeline.dedup import normalize_title

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("dedup_existing")


def normalize_all_titles():
    """Fill title_normalized for all articles that don't have it yet."""
    with get_session() as session:
        rows = session.execute(text(
            "SELECT id, title FROM articles WHERE title_normalized IS NULL AND title IS NOT NULL"
        )).fetchall()

    logger.info(f"Normalizing {len(rows)} article titles...")
    batch = []
    for i, row in enumerate(rows):
        norm = normalize_title(row.title)
        batch.append({"aid": row.id, "norm": norm})

        if len(batch) >= 500:
            with get_session() as session:
                for b in batch:
                    session.execute(
                        text("UPDATE articles SET title_normalized = :norm WHERE id = :aid"),
                        b,
                    )
            batch = []
            logger.info(f"  Normalized {i + 1}/{len(rows)}...")

    if batch:
        with get_session() as session:
            for b in batch:
                session.execute(
                    text("UPDATE articles SET title_normalized = :norm WHERE id = :aid"),
                    b,
                )

    logger.info(f"Done normalizing {len(rows)} titles.")


def find_and_mark_duplicates():
    """Find duplicates among existing articles using pg_trgm similarity."""
    with get_session() as session:
        # Get all non-duplicate articles grouped by country
        countries = session.execute(text(
            "SELECT DISTINCT s.country_code FROM sources s"
        )).fetchall()

    total_dupes = 0

    for (country_code,) in countries:
        logger.info(f"Processing country: {country_code}")

        with get_session() as session:
            # Get all articles for this country, ordered by published_at ASC
            # so the earliest article becomes the parent
            articles = session.execute(text("""
                SELECT a.id, a.title_normalized, a.published_at
                FROM articles a
                JOIN sources s ON a.source_id = s.id
                WHERE s.country_code = :cc
                  AND a.title_normalized IS NOT NULL
                  AND a.title_normalized != ''
                  AND a.is_duplicate = FALSE
                ORDER BY a.published_at ASC
            """), {"cc": country_code}).fetchall()

        logger.info(f"  {len(articles)} articles to check in {country_code}")

        # For each article, check if there's an earlier one that's very similar
        dupe_count = 0
        for i, art in enumerate(articles):
            if i == 0:
                continue

            with get_session() as session:
                # Check if already marked
                check = session.execute(
                    text("SELECT is_duplicate FROM articles WHERE id = :id"),
                    {"id": art.id}
                ).fetchone()
                if check and check.is_duplicate:
                    continue

                # Find duplicate among earlier articles (that are not themselves dupes)
                result = session.execute(text("""
                    SELECT a.id,
                           similarity(a.title_normalized, :title) AS sim
                    FROM articles a
                    JOIN sources s ON a.source_id = s.id
                    WHERE s.country_code = :country
                      AND a.id != :current_id
                      AND a.published_at <= :pub_date
                      AND a.is_duplicate = FALSE
                      AND a.title_normalized IS NOT NULL
                      AND a.title_normalized != ''
                      AND similarity(a.title_normalized, :title) > 0.85
                    ORDER BY a.published_at ASC
                    LIMIT 1
                """), {
                    "title": art.title_normalized,
                    "country": country_code,
                    "current_id": art.id,
                    "pub_date": art.published_at,
                }).fetchone()

                if result:
                    parent_id = result.id
                    session.execute(text(
                        "UPDATE articles SET is_duplicate = TRUE, duplicate_of = :pid WHERE id = :aid"
                    ), {"pid": parent_id, "aid": art.id})
                    session.execute(text(
                        "UPDATE articles SET reprint_count = reprint_count + 1 WHERE id = :pid"
                    ), {"pid": parent_id})
                    dupe_count += 1

            if (i + 1) % 100 == 0:
                logger.info(f"  Checked {i + 1}/{len(articles)}, found {dupe_count} dupes so far...")

        total_dupes += dupe_count
        logger.info(f"  {country_code}: found {dupe_count} duplicates")

    logger.info(f"Total duplicates found: {total_dupes}")
    return total_dupes


def main():
    wait_for_db()

    logger.info("=== Step 1: Normalize all titles ===")
    normalize_all_titles()

    logger.info("=== Step 2: Find and mark duplicates ===")
    total = find_and_mark_duplicates()

    logger.info(f"=== Done! {total} duplicates marked ===")


if __name__ == "__main__":
    main()
