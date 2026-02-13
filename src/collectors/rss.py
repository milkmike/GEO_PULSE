"""RSS feed collector."""
import html
import logging
from datetime import datetime, timezone
from time import mktime

import feedparser
import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "CIS-Thermometer/1.0 (research project; +https://github.com/cis-thermometer)"


def collect_rss(source_url: str, source_name: str = "") -> list[dict]:
    """Fetch and parse RSS feed, return list of article dicts."""
    articles = []
    try:
        # Some feeds need a proper User-Agent
        response = httpx.get(
            source_url,
            headers={"User-Agent": USER_AGENT},
            timeout=30.0,
            follow_redirects=True,
        )
        response.raise_for_status()
        feed = feedparser.parse(response.text)

        if feed.bozo and not feed.entries:
            logger.warning(f"[{source_name}] Feed parse error: {feed.bozo_exception}")
            return []

        for entry in feed.entries:
            # Parse published date
            published = None
            for date_field in ("published_parsed", "updated_parsed", "created_parsed"):
                dt = getattr(entry, date_field, None)
                if dt:
                    published = datetime.fromtimestamp(mktime(dt), tz=timezone.utc)
                    break
            if not published:
                published = datetime.now(timezone.utc)

            # Get link
            link = getattr(entry, "link", "") or ""

            # Get body text
            body = ""
            if hasattr(entry, "content") and entry.content:
                body = entry.content[0].get("value", "")
            elif hasattr(entry, "summary"):
                body = entry.summary or ""
            elif hasattr(entry, "description"):
                body = entry.description or ""

            # Strip HTML tags (basic)
            from bs4 import BeautifulSoup
            if body:
                body = html.unescape(BeautifulSoup(body, "lxml").get_text(separator=" ", strip=True))

            title = html.unescape(getattr(entry, "title", "") or "")

            if not title and not body:
                continue

            articles.append({
                "external_id": link or title[:200],
                "title": title,
                "body": body[:10000],  # limit body size
                "url": link,
                "published_at": published,
            })

        logger.info(f"[{source_name}] Collected {len(articles)} articles from RSS")

    except httpx.HTTPError as e:
        logger.error(f"[{source_name}] HTTP error fetching {source_url}: {e}")
    except Exception as e:
        logger.error(f"[{source_name}] Error collecting RSS from {source_url}: {e}")

    return articles
