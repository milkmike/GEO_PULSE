"""Validate an RSS/Atom feed candidate before adding it to the registry.

Usage: python scripts/validate_feed.py <url> [--max-age-days 30]
Exit 0 = usable, 1 = rejected (reason printed).
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

import feedparser
import httpx


def main():
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--max-age-days", type=int, default=30)
    args = p.parse_args()

    try:
        resp = httpx.get(args.url, timeout=15, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (GEO PULSE feed check)"})
    except Exception as e:
        print(f"REJECT: fetch failed: {e}")
        sys.exit(1)
    if resp.status_code != 200:
        print(f"REJECT: HTTP {resp.status_code}")
        sys.exit(1)

    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        print(f"REJECT: parse error: {feed.bozo_exception}")
        sys.exit(1)
    if not feed.entries:
        print("REJECT: feed has no entries")
        sys.exit(1)

    newest = None
    for e in feed.entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        if t:
            dt = datetime(*t[:6], tzinfo=timezone.utc)
            newest = max(newest, dt) if newest else dt
    if newest is None:
        print("WARN: no dates in feed; accepting on entry count")
    elif datetime.now(timezone.utc) - newest > timedelta(days=args.max_age_days):
        print(f"REJECT: newest entry {newest.date()} older than {args.max_age_days}d")
        sys.exit(1)

    print(f"OK: {len(feed.entries)} entries, newest {newest.date() if newest else 'n/a'}: "
          f"{feed.feed.get('title', '(no title)')}")
    sys.exit(0)


if __name__ == "__main__":
    main()
