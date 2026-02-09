"""Generic web scraper using trafilatura for automatic content extraction."""
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import httpx
import trafilatura

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

SKIP_PATTERNS = re.compile(
    r'/tag/|/tags/|/category/|/author/|/user/|/login|/register|'
    r'/search|/about|/contact|/privacy|/terms|/feed|/rss|'
    r'\.pdf$|\.jpg$|\.png$|\.gif$|\.mp4$|\.mp3$|#|javascript:|mailto:',
    re.IGNORECASE,
)


def _find_rss_links(html: str, base_url: str) -> list[str]:
    """Find RSS/Atom feed links in HTML <link> tags."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    feeds = []
    for link in soup.find_all("link", type=re.compile(r"application/(rss|atom)\+xml")):
        href = link.get("href", "")
        if href:
            if href.startswith("/"):
                href = urljoin(base_url, href)
            feeds.append(href)
    return feeds


def _parse_rss_for_urls(feed_url: str) -> list[str]:
    """Parse RSS feed and return article URLs."""
    import feedparser
    try:
        resp = httpx.get(feed_url, headers={"User-Agent": USER_AGENT},
                         timeout=15.0, follow_redirects=True)
        feed = feedparser.parse(resp.text)
        return [getattr(e, "link", "") for e in feed.entries if getattr(e, "link", "")]
    except Exception:
        return []


def discover_articles(base_url: str, max_articles: int = 20) -> list[str]:
    """Find article URLs on a website."""
    urls = set()
    parsed_base = urlparse(base_url)
    base_domain = parsed_base.netloc

    try:
        resp = httpx.get(
            base_url,
            headers={"User-Agent": USER_AGENT},
            timeout=20.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        logger.warning(f"Failed to fetch {base_url}: {e}")
        return []

    # Strategy 1: Check for RSS links in HTML head
    rss_links = _find_rss_links(html, base_url)
    if rss_links:
        for feed_url in rss_links[:2]:
            article_urls = _parse_rss_for_urls(feed_url)
            urls.update(article_urls)
        if urls:
            logger.info(f"Found {len(urls)} URLs from RSS feeds for {base_url}")
            return list(urls)[:max_articles]

    # Strategy 2: Extract links from page
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        title_text = a_tag.get_text(strip=True)

        if len(title_text) < 15:
            continue

        if href.startswith("/"):
            href = urljoin(base_url, href)
        elif not href.startswith("http"):
            continue

        parsed = urlparse(href)
        if base_domain not in parsed.netloc:
            continue

        if SKIP_PATTERNS.search(href):
            continue

        urls.add(href)

    result = list(urls)
    logger.info(f"Found {len(result)} candidate URLs from homepage of {base_url}")
    return result[:max_articles]


def extract_article(url: str) -> dict | None:
    """Extract title and text from a URL using trafilatura."""
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return None

        result = trafilatura.extract(
            downloaded,
            include_comments=False,
            output_format='json',
            with_metadata=True,
            favor_recall=True,
        )

        if not result:
            return None

        data = json.loads(result)
        title = data.get("title", "").strip()
        body = data.get("text", "").strip()

        if not title and not body:
            return None

        published_at = None
        date_str = data.get("date")
        if date_str:
            try:
                published_at = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass
        if not published_at:
            published_at = datetime.now(timezone.utc)

        return {
            "external_id": url,
            "title": title[:500],
            "body": body[:10000],
            "url": url,
            "published_at": published_at,
        }
    except Exception as e:
        logger.debug(f"Article extraction failed for {url}: {e}")
        return None


def scrape_web(source_url: str, source_name: str = "") -> list[dict]:
    """Scrape news articles from a website using trafilatura."""
    articles = []
    try:
        candidate_urls = discover_articles(source_url, max_articles=20)

        if not candidate_urls:
            logger.warning(f"[{source_name}] No article URLs found on {source_url}")
            return []

        logger.info(f"[{source_name}] Found {len(candidate_urls)} candidate URLs, extracting...")

        # Extract articles with a thread pool for timeout protection
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(extract_article, url): url for url in candidate_urls}
            for future in futures:
                try:
                    article = future.result(timeout=30)
                    if article and article["title"]:
                        articles.append(article)
                except FutureTimeoutError:
                    logger.debug(f"[{source_name}] Timeout extracting {futures[future]}")
                except Exception as e:
                    logger.debug(f"[{source_name}] Failed: {e}")

        logger.info(f"[{source_name}] Extracted {len(articles)} articles from web")

    except httpx.HTTPError as e:
        logger.error(f"[{source_name}] HTTP error scraping {source_url}: {e}")
    except Exception as e:
        logger.error(f"[{source_name}] Error scraping {source_url}: {e}")

    return articles
