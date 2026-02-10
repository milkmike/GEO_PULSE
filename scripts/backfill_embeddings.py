#!/usr/bin/env python3
"""Backfill embeddings for existing analyzed articles that don't have them yet.

Usage:
    python scripts/backfill_embeddings.py [--batch 50] [--limit 0]
    
    --batch: number of articles per API call (max 100)
    --limit: max articles to process (0 = all)
"""
import argparse
import logging
import time

from sqlalchemy import text

from src.db import get_session, wait_for_db
from src.embeddings import generate_embeddings_batch, prepare_embedding_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("backfill-embeddings")


def backfill(batch_size: int = 50, limit: int = 0):
    """Backfill embeddings for articles missing them."""
    wait_for_db()

    total_processed = 0
    total_embedded = 0

    while True:
        # Fetch articles with analysis but no embedding
        with get_session() as session:
            query = """
                SELECT an.article_id, ar.title, ar.body, ar.summary
                FROM analysis an
                JOIN articles ar ON ar.id = an.article_id
                WHERE an.embedding IS NULL
                  AND an.is_relevant = TRUE
                ORDER BY ar.published_at DESC
                LIMIT :batch
            """
            rows = session.execute(text(query), {"batch": batch_size}).fetchall()

        if not rows:
            logger.info("No more articles to backfill")
            break

        # Prepare texts
        texts = []
        article_ids = []
        for row in rows:
            t = prepare_embedding_text(
                title=row.title or "",
                body=row.body or "",
                summary=row.summary or "",
            )
            if t.strip():
                texts.append(t)
                article_ids.append(row.article_id)

        if not texts:
            break

        # Generate embeddings
        logger.info(f"Generating embeddings for batch of {len(texts)}...")
        embeddings = generate_embeddings_batch(texts)

        # Save
        embedded = 0
        with get_session() as session:
            for aid, emb in zip(article_ids, embeddings):
                if emb is not None:
                    emb_str = "[" + ",".join(str(x) for x in emb) + "]"
                    session.execute(
                        text("UPDATE analysis SET embedding = :emb WHERE article_id = :aid"),
                        {"emb": emb_str, "aid": aid},
                    )
                    embedded += 1

        total_processed += len(rows)
        total_embedded += embedded
        logger.info(
            f"Batch done: {embedded}/{len(texts)} embedded "
            f"(total: {total_embedded}/{total_processed})"
        )

        if limit and total_processed >= limit:
            logger.info(f"Reached limit of {limit}")
            break

        time.sleep(1)  # Rate limit between batches

    logger.info(f"Backfill complete: {total_embedded} embeddings generated")
    return total_embedded


def main():
    parser = argparse.ArgumentParser(description="Backfill article embeddings")
    parser.add_argument("--batch", type=int, default=50, help="Batch size")
    parser.add_argument("--limit", type=int, default=0, help="Max articles (0=all)")
    args = parser.parse_args()

    backfill(batch_size=args.batch, limit=args.limit)


if __name__ == "__main__":
    main()
