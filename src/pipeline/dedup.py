"""Deduplication utilities for articles."""
import re
import logging
from datetime import timedelta

from sqlalchemy import text

logger = logging.getLogger(__name__)


def normalize_title(title: str) -> str:
    """Normalize a title for fuzzy comparison."""
    if not title:
        return ""
    t = title.lower()
    # Remove punctuation
    t = re.sub(r'[^\w\s]', '', t)
    # Collapse whitespace
    t = re.sub(r'\s+', ' ', t).strip()
    return t


def find_duplicate(session, title_normalized: str, country_code: str,
                   published_at, threshold: float = 0.85):
    """Find a duplicate article in the DB within ±48 hours of the same country.

    Returns the parent article id or None.
    """
    if not title_normalized or len(title_normalized) < 10:
        return None

    from_date = published_at - timedelta(hours=48)
    to_date = published_at + timedelta(hours=48)

    result = session.execute(text("""
        SELECT a.id,
               similarity(a.title_normalized, :title) AS sim
        FROM articles a
        JOIN sources s ON a.source_id = s.id
        WHERE s.country_code = :country
          AND a.published_at BETWEEN :from_date AND :to_date
          AND a.is_duplicate = FALSE
          AND a.title_normalized IS NOT NULL
          AND a.title_normalized != ''
          AND similarity(a.title_normalized, :title) > :threshold
        ORDER BY sim DESC
        LIMIT 1
    """), {
        "title": title_normalized,
        "country": country_code,
        "from_date": from_date,
        "to_date": to_date,
        "threshold": threshold,
    }).fetchone()

    return result.id if result else None
