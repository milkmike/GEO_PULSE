"""Title validation and cleanup for collected articles.

Ensures article titles are meaningful, clean, and informative.
Runs at collection time (before DB insert) — no need for API-level hacks.
"""
import html
import re
import logging

logger = logging.getLogger(__name__)

# ── Garbage patterns: titles that are site names, error messages, or non-articles ──
GARBAGE_PATTERNS = [
    re.compile(r'^The IControllerFactory', re.IGNORECASE),
    re.compile(r'^Информация\s*\|', re.IGNORECASE),
    re.compile(r'^Новости\s+\S+\s+на\s+(сайте|")', re.IGNORECASE),
    re.compile(r'^События\s+\S+\s+в\s+объективе', re.IGNORECASE),
    re.compile(r'Белсат онлайн', re.IGNORECASE),
    re.compile(r'^Министерство иностранных дел\s+Республики', re.IGNORECASE),
    re.compile(r'^Министерство\s+\S+\s+дел$', re.IGNORECASE),
    re.compile(r'^(Error|404|500|Not Found|Access Denied|Forbidden|Captcha)', re.IGNORECASE),
    re.compile(r'^(Главная|Homepage|Home|Index)\s*[\|—-]?', re.IGNORECASE),
    re.compile(r'^https?://', re.IGNORECASE),  # URL as title
]

# ── Source-name suffixes to strip (e.g. "...| Министерство ... Таджикистан") ──
STRIP_SUFFIXES = [
    re.compile(r'\s*\|\s*Министерство\s+иностранных\s+дел.*$'),
    re.compile(r'\s*\|\s*Белсат.*$', re.IGNORECASE),
    re.compile(r'\s*[-–—]\s*Радио\s+Азаттык\s*$', re.IGNORECASE),
    re.compile(r'\s*[-–—]\s*Азаттык\s+Азия\s*$', re.IGNORECASE),
    re.compile(r'\s*[-–—]\s*Радио Свобода\s*$', re.IGNORECASE),
    re.compile(r'\s*\|\s*РИА Новости\s*$', re.IGNORECASE),
]


def clean_title(title: str | None) -> str | None:
    """Clean and validate an article title.
    
    Returns cleaned title, or None if title is garbage and should be skipped.
    """
    if not title:
        return None

    # 1. Decode HTML entities (&nbsp; &laquo; etc.)
    t = html.unescape(title)

    # 2. Normalize whitespace
    t = re.sub(r'\s+', ' ', t).strip()

    # 3. Remove leading emoji/markdown formatting artifacts
    t = re.sub(r'^[\s\u200b\ufeff]+', '', t)  # zero-width spaces

    # 4. Check minimum length (too short = probably not a real headline)
    if len(t) < 10:
        logger.debug(f"Title too short, skipping: {t!r}")
        return None

    # 5. Check garbage patterns
    for pattern in GARBAGE_PATTERNS:
        if pattern.search(t):
            logger.debug(f"Garbage title detected, skipping: {t!r}")
            return None

    # 6. Strip source-name suffixes
    for pattern in STRIP_SUFFIXES:
        t = pattern.sub('', t).strip()

    # 7. After stripping, re-check minimum length
    if len(t) < 10:
        logger.debug(f"Title too short after cleanup, skipping: {t!r}")
        return None

    return t
