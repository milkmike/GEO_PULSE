"""RSS feed collector."""
import html
import logging
import time
from datetime import datetime, timezone
from time import mktime

import feedparser
import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "CIS-Thermometer/1.0 (research project; +https://github.com/cis-thermometer)"

# Statuses recorded on the source so /health/sources can show WHY a feed is quiet.
# ok | empty_feed | malformed | http_4xx | geoblock | http_5xx | timeout | conn_error


def _parse_entries(feed) -> list[dict]:
    """Map feedparser entries to article dicts."""
    articles = []
    for entry in feed.entries:
        published = None
        for date_field in ("published_parsed", "updated_parsed", "created_parsed"):
            dt = getattr(entry, date_field, None)
            if dt:
                published = datetime.fromtimestamp(mktime(dt), tz=timezone.utc)
                break
        if not published:
            published = datetime.now(timezone.utc)

        link = getattr(entry, "link", "") or ""

        body = ""
        if hasattr(entry, "content") and entry.content:
            body = entry.content[0].get("value", "")
        elif hasattr(entry, "summary"):
            body = entry.summary or ""
        elif hasattr(entry, "description"):
            body = entry.description or ""

        from bs4 import BeautifulSoup
        if body:
            body = html.unescape(BeautifulSoup(body, "lxml").get_text(separator=" ", strip=True))

        title = html.unescape(getattr(entry, "title", "") or "")
        if not title and not body:
            continue

        articles.append({
            "external_id": link or title[:200],
            "title": title,
            "body": body[:10000],
            "url": link,
            "published_at": published,
        })
    return articles


def collect_rss_status(source_url: str, source_name: str = "",
                       retries: int = 1, timeout: float = 15.0) -> tuple[list[dict], str, str]:
    """Fetch and parse an RSS feed, classifying the outcome.

    Returns (articles, status, error_detail). Only genuinely transient server
    errors (5xx, connection errors) are retried once; a *timeout* fails fast
    (no retry) — a feed that hangs past the timeout is almost always dead, and
    retrying it 30s+ at a time starves the sequential collector of hundreds of
    healthy feeds behind it. Hard failures (4xx/geoblock/malformed/empty) return
    immediately.
    """
    last_status, last_error = "conn_error", ""
    for attempt in range(retries + 1):
        try:
            response = httpx.get(
                source_url,
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
                follow_redirects=True,
            )
        except (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.PoolTimeout,
                httpx.TimeoutException) as e:
            # Fail fast — don't burn another timeout window on a hanging feed.
            return [], "timeout", f"{type(e).__name__}: {str(e)[:160]}"
        except httpx.HTTPError as e:
            last_status, last_error = "conn_error", f"{type(e).__name__}: {str(e)[:160]}"
        else:
            code = response.status_code
            if code in (403, 451):
                logger.warning(f"[{source_name}] geoblock/forbidden HTTP {code}")
                return [], "geoblock", f"HTTP {code}"
            if 400 <= code < 500:
                logger.warning(f"[{source_name}] client error HTTP {code}")
                return [], "http_4xx", f"HTTP {code}"
            if code >= 500:
                last_status, last_error = "http_5xx", f"HTTP {code}"
            else:
                feed = feedparser.parse(response.text)
                if feed.bozo and not feed.entries:
                    logger.warning(f"[{source_name}] feed parse error: {feed.bozo_exception}")
                    return [], "malformed", str(feed.bozo_exception)[:200]
                articles = _parse_entries(feed)
                if not articles:
                    return [], "empty_feed", "0 entries"
                logger.info(f"[{source_name}] Collected {len(articles)} articles from RSS")
                return articles, "ok", ""

        # transient server/connection failure — one quick retry
        if attempt < retries:
            time.sleep(1)
            logger.info(f"[{source_name}] retry after {last_status}")

    logger.error(f"[{source_name}] RSS fetch failed ({last_status}): {last_error}")
    return [], last_status, last_error


def collect_rss(source_url: str, source_name: str = "") -> list[dict]:
    """Fetch and parse RSS feed, return list of article dicts (status discarded)."""
    return collect_rss_status(source_url, source_name)[0]
