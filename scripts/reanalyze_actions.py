"""Re-analyze existing articles to determine action_level."""
import logging
import time

from sqlalchemy import text

from src.config import OPENROUTER_API_KEY, COUNTRY_NAMES
from src.db import get_session, wait_for_db
from src.pipeline.sentiment import analyze_action_level

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("reanalyze")


def reanalyze_action_levels():
    """Re-analyze articles that have action_level = 1 or NULL."""
    wait_for_db()
    
    if not OPENROUTER_API_KEY:
        logger.error("OPENROUTER_API_KEY not set!")
        return
    
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT an.id as analysis_id, ar.title, ar.body, s.country_code
                FROM analysis an
                JOIN articles ar ON an.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE an.is_relevant = true
                  AND an.sentiment IS NOT NULL
                  AND (an.action_level IS NULL OR an.action_level = 1)
                ORDER BY ar.published_at DESC
            """),
        ).fetchall()

    if not rows:
        logger.info("No articles to reanalyze")
        return

    logger.info(f"Reanalyzing {len(rows)} articles for action_level...")

    updated = 0
    for i, row in enumerate(rows):
        try:
            title = row.title or ""
            body = row.body or ""
            
            action_level = analyze_action_level(
                title=title,
                body=body,
                country_code=row.country_code,
            )
            
            with get_session() as session:
                session.execute(
                    text("UPDATE analysis SET action_level = :al WHERE id = :id"),
                    {"al": action_level, "id": row.analysis_id},
                )
            
            country = COUNTRY_NAMES.get(row.country_code, row.country_code)
            logger.info(f"  [{i+1}/{len(rows)}] [{country}] {title[:60]}... → action_level={action_level}")
            updated += 1
            
            time.sleep(0.5)  # rate limit
            
        except Exception as e:
            logger.error(f"  Error reanalyzing {row.analysis_id}: {e}")

    logger.info(f"Reanalyzed {updated}/{len(rows)} articles")


if __name__ == "__main__":
    reanalyze_action_levels()
