#!/usr/bin/env python3
"""Detect and fix article languages based on title content.

Runs against the CIS Thermometer database. Can be used one-shot or imported.
"""
import re
import logging
import unicodedata

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Romanian-specific characters
RO_CHARS = set("ăâîșțĂÂÎȘȚ")

# Cyrillic range
CYRILLIC_RE = re.compile(r'[\u0400-\u04FF]')
LATIN_RE = re.compile(r'[A-Za-zÀ-ÿ]')

# Uzbek Latin markers (common patterns)
UZ_MARKERS = re.compile(
    r"\b(o'z|O'z|o'|O'|sh|ch|ng|"
    r"oʻz|Oʻz|oʻ|Oʻ|"
    r"bilan|haqida|uchun|bo'yicha|davlat|vazir|prezident"
    r")\b",
    re.IGNORECASE,
)

# Turkmen Latin markers
TK_MARKERS = re.compile(
    r"[ňžäýöüŇŽÄÝÖÜ]|"
    r"\b(barada|döwlet|türkmen|ministr|prezident)\b",
    re.IGNORECASE,
)


def detect_language(title: str) -> str:
    """Detect language from article title using character heuristics.

    Returns: 'en', 'ro', 'uz', 'tk', or 'ru' (default).
    """
    if not title or len(title.strip()) < 3:
        return "ru"

    # Count character types
    cyrillic_count = len(CYRILLIC_RE.findall(title))
    latin_count = len(LATIN_RE.findall(title))
    total_letters = cyrillic_count + latin_count

    if total_letters == 0:
        return "ru"

    latin_ratio = latin_count / total_letters
    has_cyrillic = cyrillic_count > 2

    # If mostly Latin characters
    if latin_ratio >= 0.5 and not has_cyrillic:
        # Check Romanian markers first
        if any(c in RO_CHARS for c in title):
            return "ro"

        # Check Uzbek Latin markers
        if UZ_MARKERS.search(title):
            return "uz"

        # Check Turkmen markers
        if TK_MARKERS.search(title):
            return "tk"

        # Default Latin → English
        return "en"

    # Mostly Cyrillic → Russian (covers ru/kz/kg/tg/by)
    return "ru"


def run_detection(batch_size: int = 1000):
    """Update language for all articles in the database."""
    # Import here so the module can be used standalone
    import sys
    sys.path.insert(0, "/opt/cis-thermometer")
    from sqlalchemy import text as sql_text
    from src.db import get_session

    total_updated = 0

    with get_session() as session:
        # Count total articles
        total = session.execute(sql_text("SELECT COUNT(*) FROM articles")).scalar()
        logger.info(f"Total articles: {total}")

        # Process in batches
        offset = 0
        while offset < total:
            rows = session.execute(
                sql_text("""
                    SELECT id, title, language 
                    FROM articles 
                    ORDER BY id 
                    LIMIT :limit OFFSET :offset
                """),
                {"limit": batch_size, "offset": offset},
            ).fetchall()

            if not rows:
                break

            updates = []
            for row in rows:
                detected = detect_language(row.title or "")
                current = row.language or "ru"
                if detected != current:
                    updates.append((row.id, detected))

            if updates:
                for aid, lang in updates:
                    session.execute(
                        sql_text("UPDATE articles SET language = :lang WHERE id = :id"),
                        {"lang": lang, "id": aid},
                    )
                total_updated += len(updates)

            offset += batch_size
            logger.info(f"  Processed {min(offset, total)}/{total}, updated so far: {total_updated}")

        session.commit()

    logger.info(f"Done! Updated {total_updated} articles")

    # Show distribution
    with get_session() as session:
        rows = session.execute(
            sql_text("SELECT language, COUNT(*) as cnt FROM articles GROUP BY language ORDER BY cnt DESC")
        ).fetchall()
        logger.info("Language distribution:")
        for r in rows:
            logger.info(f"  {r.language or 'NULL'}: {r.cnt}")


if __name__ == "__main__":
    run_detection()
