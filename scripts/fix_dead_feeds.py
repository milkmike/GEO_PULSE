"""Resurrect broken RSS feeds by routing them through Google News.

Many direct RSS feeds are dead for the collector: major outlets return 403/451
to datacenter IPs, others 404 or stopped serving valid RSS (see
docs/research/dead-sources-2026-06-13.md). Google News `site:` search RSS is the
worldmonitor-style universal adapter — it is not geoblocked, always returns a
valid feed, and as a bonus pre-filters to Russia-relevant items (which is all
the pipeline keeps anyway). Several sources here already use it (ISW, Carnegie).

This tool probes every configured rss source, and for the ones that fail with a
*hard* error (HTTP 4xx/5xx, unparseable, empty) it builds a Google News wrapper
from the feed's domain + language, validates the wrapper actually returns items,
and — unless --dry-run — rewrites the url in the YAML by exact string match
(comments/formatting preserved; urls are unique per file).

Usage:
    python scripts/fix_dead_feeds.py --dry-run     # report what would change
    python scripts/fix_dead_feeds.py               # apply
"""
import argparse
import logging
from urllib.parse import urlparse

from src.config import SOURCES_PATH, WORLD_SOURCES_PATH, load_sources
from scripts.validate_feed import validate_url

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("fix-dead-feeds")

# Russia search term + Google News locale per source language.
RUSSIA_TERM = {
    "en": "russia", "ru": "россия", "de": "russland", "fr": "russie",
    "es": "rusia", "pt": "rússia", "it": "russia", "tr": "rusya",
    "ar": "روسيا", "fa": "روسیه", "uk": "росія", "pl": "rosja",
    "ro": "rusia", "el": "ρωσία", "sr": "русија", "ko": "러시아",
    "ja": "ロシア", "zh": "俄罗斯", "hi": "रूस", "id": "rusia",
}
LOCALE = {
    "en": ("en-US", "US", "US:en"), "de": ("de", "DE", "DE:de"),
    "fr": ("fr", "FR", "FR:fr"), "es": ("es-419", "US", "US:es-419"),
    "pt": ("pt-BR", "BR", "BR:pt-419"), "it": ("it", "IT", "IT:it"),
    "tr": ("tr", "TR", "TR:tr"), "ar": ("ar", "EG", "EG:ar"),
    "fa": ("fa", "IR", "IR:fa"), "uk": ("uk", "UA", "UA:uk"),
    "pl": ("pl", "PL", "PL:pl"), "ro": ("ro", "RO", "RO:ro"),
    "el": ("el", "GR", "GR:el"), "sr": ("sr", "RS", "RS:sr"),
    "ko": ("ko", "KR", "KR:ko"), "ja": ("ja", "JP", "JP:ja"),
    "zh": ("zh-CN", "CN", "CN:zh-Hans"), "hi": ("hi", "IN", "IN:hi"),
    "id": ("id", "ID", "ID:id"),
}

# Reason fragments that mean "the URL is broken" (vs merely stale/quiet).
HARD_FAILURE_MARKERS = ("HTTP 4", "HTTP 5", "fetch failed", "parse error",
                        "no entries", "0 entries")


def base_domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for p in ("rss.", "www.", "feeds.", "feed.", "en.", "amp.", "m."):
        if host.startswith(p):
            host = host[len(p):]
    return host


def gnews_wrapper(url: str, lang: str) -> str:
    domain = base_domain(url)
    term = RUSSIA_TERM.get(lang, "russia")
    hl, gl, ceid = LOCALE.get(lang, ("en-US", "US", "US:en"))
    return (f"https://news.google.com/rss/search?q=site:{domain}+{term}"
            f"&hl={hl}&gl={gl}&ceid={ceid}")


def is_already_gnews(url: str) -> bool:
    return "news.google.com/rss/search" in url


def main():
    ap = argparse.ArgumentParser(description="Route broken feeds through Google News")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = load_sources()
    # Map each url to its language for wrapper construction.
    rss = []
    for cc, data in cfg["countries"].items():
        for s in data.get("sources", []):
            if s.get("type") == "rss" and not is_already_gnews(s.get("url", "")):
                rss.append((cc, s.get("name", ""), s["url"], s.get("language", "en")))

    logger.info("Probing %d direct (non-Google-News) RSS feeds...", len(rss))
    replacements: dict[str, str] = {}  # old_url -> new_url
    for cc, name, url, lang in rss:
        res = validate_url(url, "rss")
        if res["ok"]:
            continue
        if not any(m in res["reason"] for m in HARD_FAILURE_MARKERS):
            logger.info("SKIP  [%s] %-26s soft: %s", cc, name[:26], res["reason"][:50])
            continue
        new = gnews_wrapper(url, lang)
        chk = validate_url(new, "rss")
        if not chk["ok"]:
            logger.warning("KEEP  [%s] %-26s wrapper also failed: %s", cc, name[:26], chk["reason"][:50])
            continue
        replacements[url] = new
        logger.info("FIX   [%s] %-26s %s -> gnews(%s) [%d items]",
                    cc, name[:26], res["reason"][:24], base_domain(url), chk["n_items"])

    logger.info("=== %d feeds to rewrite ===", len(replacements))
    if not replacements or args.dry_run:
        if args.dry_run:
            logger.info("dry-run: no files changed")
        return

    for path in (SOURCES_PATH, WORLD_SOURCES_PATH):
        if not path.exists():
            continue
        text = path.read_text()
        n = 0
        for old, new in replacements.items():
            if old in text:
                text = text.replace(old, new)
                n += 1
        path.write_text(text)
        logger.info("Rewrote %d urls in %s", n, path.name)


if __name__ == "__main__":
    main()
