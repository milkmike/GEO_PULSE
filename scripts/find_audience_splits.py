#!/usr/bin/env python3
"""Find bilingual article pairs from the same source with different sentiment.

Uses title_normalized pg_trgm similarity for matching (fast, no embeddings needed).
When embeddings are available, uses cosine similarity for better accuracy.
"""
import argparse
import logging
import sys

sys.path.insert(0, "/opt/cis-thermometer")

from sqlalchemy import text as sql_text
from src.db import get_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def find_pairs(days: int = 90, min_sentiment_delta: float = 1.0, use_embeddings: bool = False):
    """Find bilingual article pairs with divergent sentiment."""

    with get_session() as session:
        # Get bilingual sources (sources that have articles in >1 language)
        bilingual_sources = session.execute(
            sql_text("""
                SELECT s.id, s.name, s.country_code,
                       array_agg(DISTINCT a.language) as langs,
                       COUNT(DISTINCT a.language) as lang_count
                FROM sources s
                JOIN articles a ON a.source_id = s.id
                WHERE a.language IS NOT NULL
                  AND a.published_at > NOW() - make_interval(days => :days)
                GROUP BY s.id, s.name, s.country_code
                HAVING COUNT(DISTINCT a.language) >= 2
                ORDER BY s.name
            """),
            {"days": days},
        ).fetchall()

        logger.info(f"Found {len(bilingual_sources)} bilingual sources")

        total_pairs = 0

        for src in bilingual_sources:
            logger.info(f"Processing {src.name} ({src.country_code}), langs: {src.langs}")

            if use_embeddings:
                # Use embedding cosine similarity (more accurate but heavier)
                pairs = session.execute(
                    sql_text("""
                        WITH bilingual AS (
                            SELECT a.id, a.title, a.language, a.published_at,
                                   an.sentiment, an.embedding
                            FROM articles a
                            JOIN analysis an ON an.article_id = a.id
                            WHERE a.source_id = :sid
                              AND a.language IS NOT NULL
                              AND an.sentiment IS NOT NULL
                              AND an.embedding IS NOT NULL
                              AND a.is_duplicate = false
                              AND a.published_at > NOW() - make_interval(days => :days)
                        )
                        SELECT a1.id as id1, a2.id as id2,
                               a1.title as title1, a2.title as title2,
                               a1.language as lang1, a2.language as lang2,
                               a1.published_at as pub1, a2.published_at as pub2,
                               a1.sentiment as sent1, a2.sentiment as sent2,
                               1 - (a1.embedding <=> a2.embedding) as sim,
                               ABS(a1.sentiment - a2.sentiment) as delta
                        FROM bilingual a1
                        CROSS JOIN bilingual a2
                        WHERE a1.id < a2.id
                          AND a1.language != a2.language
                          AND DATE(a1.published_at) = DATE(a2.published_at)
                          AND 1 - (a1.embedding <=> a2.embedding) > 0.80
                          AND ABS(a1.sentiment - a2.sentiment) >= :min_delta
                        ORDER BY delta DESC
                        LIMIT 100
                    """),
                    {"sid": src.id, "days": days, "min_delta": min_sentiment_delta},
                ).fetchall()
            else:
                # Use pg_trgm title similarity (faster, works without embeddings)
                pairs = session.execute(
                    sql_text("""
                        WITH bilingual AS (
                            SELECT a.id, a.title, a.title_normalized, a.language,
                                   a.published_at, an.sentiment
                            FROM articles a
                            JOIN analysis an ON an.article_id = a.id
                            WHERE a.source_id = :sid
                              AND a.language IS NOT NULL
                              AND a.title_normalized IS NOT NULL
                              AND a.title_normalized != ''
                              AND an.sentiment IS NOT NULL
                              AND a.is_duplicate = false
                              AND a.published_at > NOW() - make_interval(days => :days)
                        )
                        SELECT a1.id as id1, a2.id as id2,
                               a1.title as title1, a2.title as title2,
                               a1.language as lang1, a2.language as lang2,
                               a1.published_at as pub1, a2.published_at as pub2,
                               a1.sentiment as sent1, a2.sentiment as sent2,
                               similarity(a1.title_normalized, a2.title_normalized) as sim,
                               ABS(a1.sentiment - a2.sentiment) as delta
                        FROM bilingual a1
                        CROSS JOIN bilingual a2
                        WHERE a1.id < a2.id
                          AND a1.language != a2.language
                          AND ABS(EXTRACT(EPOCH FROM a1.published_at - a2.published_at)) < 172800
                          AND similarity(a1.title_normalized, a2.title_normalized) > 0.3
                          AND ABS(a1.sentiment - a2.sentiment) >= :min_delta
                        ORDER BY delta DESC
                        LIMIT 200
                    """),
                    {"sid": src.id, "days": days, "min_delta": min_sentiment_delta},
                ).fetchall()

            for p in pairs:
                # Insert pair (ON CONFLICT skip)
                session.execute(
                    sql_text("""
                        INSERT INTO article_pairs
                            (article_id_1, article_id_2, source_id, similarity, 
                             sentiment_delta, lang_1, lang_2)
                        VALUES (:a1, :a2, :sid, :sim, :delta, :l1, :l2)
                        ON CONFLICT (article_id_1, article_id_2) DO UPDATE
                        SET similarity = :sim, sentiment_delta = :delta
                    """),
                    {
                        "a1": p.id1, "a2": p.id2, "sid": src.id,
                        "sim": round(float(p.sim), 3),
                        "delta": round(float(p.delta), 1),
                        "l1": p.lang1, "l2": p.lang2,
                    },
                )
                logger.info(
                    f"  PAIR: [{p.lang1}] \"{p.title1[:50]}\" (sent={p.sent1}) ↔ "
                    f"[{p.lang2}] \"{p.title2[:50]}\" (sent={p.sent2}) "
                    f"Δ={float(p.delta):.1f} sim={float(p.sim):.3f}"
                )

            total_pairs += len(pairs)
            logger.info(f"  → {len(pairs)} pairs found for {src.name}")

        session.commit()

    logger.info(f"\nTotal pairs found: {total_pairs}")

    # Also find pairs with no sentiment delta threshold — just similar titles in different languages
    # These are useful even if sentiment is the same (validates bilingual coverage)
    with get_session() as session:
        stats = session.execute(
            sql_text("""
                SELECT COUNT(*) as total_pairs,
                       COUNT(DISTINCT source_id) as sources_with_pairs,
                       AVG(sentiment_delta) as avg_delta,
                       MAX(sentiment_delta) as max_delta
                FROM article_pairs
            """)
        ).fetchone()
        logger.info(f"\nSummary: {stats.total_pairs} total pairs across "
                     f"{stats.sources_with_pairs} sources, "
                     f"avg Δ={float(stats.avg_delta or 0):.2f}, "
                     f"max Δ={float(stats.max_delta or 0):.1f}")


def main():
    parser = argparse.ArgumentParser(description="Find audience split pairs")
    parser.add_argument("--days", type=int, default=90, help="Look back N days")
    parser.add_argument("--min-delta", type=float, default=1.0, help="Minimum sentiment delta")
    parser.add_argument("--embeddings", action="store_true", help="Use embedding similarity")
    args = parser.parse_args()

    find_pairs(days=args.days, min_sentiment_delta=args.min_delta, use_embeddings=args.embeddings)


if __name__ == "__main__":
    main()
