"""Validate RSS/Atom (and web/telegram) source candidates before adding them.

A gate for source onboarding & cleanup: checks a URL is reachable, parses as
RSS/Atom (rss), carries ≥1 item, and is fresh (newest entry within
--max-age-days). Use it to vet native-language feeds before adding them to
sources.yaml / sources_world.yaml, and to flag existing feeds gone empty/stale.

Usage:
    python scripts/validate_feed.py <url> [--type rss|web] [--max-age-days 30]
    python scripts/validate_feed.py --yaml [--json]   # validate every configured source
Exit 0 = usable, 1 = rejected (single-URL mode).
"""
import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

logger = logging.getLogger("validate-feed")

USER_AGENT = "Mozilla/5.0 (GEO PULSE feed check)"
DEFAULT_MAX_AGE_DAYS = 30


def validate_url(url: str, source_type: str = "rss", name: str = "",
                 max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> dict:
    """Return {ok, reason, http_status, n_items, newest_age_days}."""
    out = {"url": url, "name": name, "type": source_type, "ok": False,
           "reason": "", "http_status": None, "n_items": 0, "newest_age_days": None}

    if source_type == "telegram":
        out["reason"] = "telegram (requires Telethon session; not validated here)"
        return out

    if source_type == "web":
        try:
            from src.collectors.scraper import scrape_web
            arts = scrape_web(url, name)
            out["n_items"] = len(arts)
            out["ok"] = len(arts) > 0
            out["reason"] = f"ok ({len(arts)} articles)" if arts else "scrape_web returned 0 articles"
        except Exception as e:  # noqa: BLE001
            out["reason"] = f"scrape error: {type(e).__name__}: {str(e)[:120]}"
        return out

    # rss / atom
    try:
        resp = httpx.get(url, timeout=20, follow_redirects=True,
                         headers={"User-Agent": USER_AGENT})
        out["http_status"] = resp.status_code
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"fetch failed: {type(e).__name__}: {str(e)[:120]}"
        return out
    if resp.status_code != 200:
        out["reason"] = f"HTTP {resp.status_code}"
        return out

    feed = feedparser.parse(resp.content)
    out["n_items"] = len(feed.entries)
    if feed.bozo and not feed.entries:
        out["reason"] = f"parse error: {str(feed.bozo_exception)[:100]}"
        return out
    if not feed.entries:
        out["reason"] = "feed has no entries"
        return out

    newest = None
    for e in feed.entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        if t:
            dt = datetime(*t[:6], tzinfo=timezone.utc)
            newest = max(newest, dt) if newest else dt
    if newest is not None:
        age = (datetime.now(timezone.utc) - newest).total_seconds() / 86400
        out["newest_age_days"] = round(age, 1)
        if timedelta(days=age) > timedelta(days=max_age_days):
            out["reason"] = f"stale: newest entry {newest.date()} (> {max_age_days}d)"
            return out

    out["ok"] = True
    title = feed.feed.get("title", "(no title)")
    out["reason"] = f"ok ({len(feed.entries)} entries, newest {out['newest_age_days']}d): {title}"
    return out


def validate_all(max_age_days: int) -> list[dict]:
    from src.config import load_sources
    cfg = load_sources()
    results = []
    for cc, data in cfg["countries"].items():
        for s in data.get("sources", []):
            r = validate_url(s.get("url", ""), s.get("type", "rss"),
                             s.get("name", ""), max_age_days)
            r["country_code"] = cc
            results.append(r)
            logger.info("%s [%s] %-28s %s", "OK " if r["ok"] else "BAD", cc,
                        (s.get("name") or "")[:28], r["reason"][:80])
    return results


def main():
    p = argparse.ArgumentParser(description="Validate feed candidates")
    p.add_argument("url", nargs="?")
    p.add_argument("--type", default="rss", choices=["rss", "web", "telegram"])
    p.add_argument("--name", default="")
    p.add_argument("--yaml", action="store_true", help="validate every configured source")
    p.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    if args.yaml:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
        results = validate_all(args.max_age_days)
        ok = sum(1 for r in results if r["ok"])
        logger.info("=== %d/%d valid, %d need attention ===", ok, len(results), len(results) - ok)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    if not args.url:
        p.error("provide a URL or use --yaml")

    res = validate_url(args.url, args.type, args.name, args.max_age_days)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(("OK: " if res["ok"] else "REJECT: ") + res["reason"])
    sys.exit(0 if res["ok"] else 1)


if __name__ == "__main__":
    main()
