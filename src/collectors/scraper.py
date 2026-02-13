"""Generic web scraper using trafilatura + Firecrawl fallback for JS-heavy sites."""
import html
import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError

import httpx
import trafilatura

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

# Firecrawl config (local instance on same VPS)
FIRECRAWL_URL = os.environ.get("FIRECRAWL_URL", "http://firecrawl-api-1:3002")
FIRECRAWL_KEY = os.environ.get("FIRECRAWL_KEY", "fc-test")

# Track which sources fail with trafilatura → auto-use Firecrawl next time
_firecrawl_preferred: set[str] = set()

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


# ═══════════════════════════════════════════════
# Firecrawl integration
# ═══════════════════════════════════════════════

def _firecrawl_available() -> bool:
    """Check if Firecrawl is reachable."""
    try:
        resp = httpx.get(f"{FIRECRAWL_URL}/", timeout=3.0)
        return resp.status_code < 500
    except Exception:
        return False


def _firecrawl_scrape(url: str) -> dict | None:
    """Scrape a single URL via Firecrawl. Returns {title, body, url, published_at} or None."""
    try:
        resp = httpx.post(
            f"{FIRECRAWL_URL}/v1/scrape",
            json={"url": url, "formats": ["markdown"]},
            headers={
                "Authorization": f"Bearer {FIRECRAWL_KEY}",
                "Content-Type": "application/json",
            },
            timeout=45.0,
        )
        data = resp.json()
        if not data.get("success"):
            return None

        content = data.get("data", {})
        markdown = content.get("markdown", "").strip()
        metadata = content.get("metadata", {})

        # Skip CAPTCHA/protection pages
        if len(markdown) < 100 or "security verification" in markdown.lower():
            return None

        title = html.unescape(metadata.get("title", "").strip() or metadata.get("ogTitle", "").strip())
        # Extract first heading as title if metadata is empty
        if not title and markdown:
            for line in markdown.split("\n"):
                line = line.strip()
                if line.startswith("# "):
                    title = line[2:].strip()
                    break

        if not title:
            return None

        # Clean markdown → plain text (strip markdown syntax)
        body = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', markdown)  # [text](url) → text
        body = re.sub(r'#{1,6}\s+', '', body)  # headers
        body = re.sub(r'\*{1,2}([^*]+)\*{1,2}', r'\1', body)  # bold/italic
        body = re.sub(r'\n{3,}', '\n\n', body)  # extra newlines

        published_at = None
        pub_str = metadata.get("publishedTime") or metadata.get("modifiedTime")
        if pub_str:
            try:
                published_at = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
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
        logger.debug(f"Firecrawl scrape failed for {url}: {e}")
        return None


def _firecrawl_discover(base_url: str, max_articles: int = 20) -> list[str]:
    """Discover article URLs using Firecrawl scrape (extract links from rendered page)."""
    try:
        resp = httpx.post(
            f"{FIRECRAWL_URL}/v1/scrape",
            json={"url": base_url, "formats": ["links"]},
            headers={
                "Authorization": f"Bearer {FIRECRAWL_KEY}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        data = resp.json()
        if not data.get("success"):
            return []

        links = data.get("data", {}).get("links", [])
        parsed_base = urlparse(base_url)
        base_domain = parsed_base.netloc

        # Filter to same-domain article-like URLs
        article_urls = []
        for link in links:
            if not isinstance(link, str):
                continue
            parsed = urlparse(link)
            if base_domain not in parsed.netloc:
                continue
            if SKIP_PATTERNS.search(link):
                continue
            # Heuristic: article URLs tend to have path depth > 1
            path = parsed.path.strip("/")
            if path and "/" in path or len(path) > 20:
                article_urls.append(link)

        return article_urls[:max_articles]
    except Exception as e:
        logger.debug(f"Firecrawl discover failed for {base_url}: {e}")
        return []


# ═══════════════════════════════════════════════
# Original trafilatura functions
# ═══════════════════════════════════════════════

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
        title = html.unescape(data.get("title", "").strip())
        body = html.unescape(data.get("text", "").strip())

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


# ═══════════════════════════════════════════════
# Main scrape function with Firecrawl fallback
# ═══════════════════════════════════════════════

def scrape_web(source_url: str, source_name: str = "") -> list[dict]:
    """
    Scrape news articles from a website.
    
    Strategy:
    1. Try trafilatura (fast, lightweight)
    2. If trafilatura returns 0 articles → fallback to Firecrawl (JS rendering)
    3. Track which sources need Firecrawl → use it first next time
    """
    articles = []
    used_firecrawl = False
    domain = urlparse(source_url).netloc

    # Check if this source previously needed Firecrawl
    prefer_firecrawl = domain in _firecrawl_preferred

    try:
        if not prefer_firecrawl:
            # ── Phase 1: Trafilatura (default) ──
            candidate_urls = discover_articles(source_url, max_articles=20)

            if candidate_urls:
                logger.info(f"[{source_name}] Found {len(candidate_urls)} candidate URLs, extracting...")

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

            if articles:
                logger.info(f"[{source_name}] Extracted {len(articles)} articles from web")
                return articles

            # Trafilatura returned 0 → try Firecrawl
            logger.info(f"[{source_name}] Trafilatura returned 0 articles, trying Firecrawl...")

        # ── Phase 2: Firecrawl fallback ──
        if not _firecrawl_available():
            if prefer_firecrawl:
                logger.warning(f"[{source_name}] Firecrawl unavailable, skipping")
            return articles

        used_firecrawl = True

        # Step 2a: Discover URLs via Firecrawl (JS-rendered page)
        fc_urls = _firecrawl_discover(source_url, max_articles=15)

        if not fc_urls:
            # If no URLs from Firecrawl discover, try scraping the main page directly
            main_article = _firecrawl_scrape(source_url)
            if main_article:
                articles.append(main_article)
            logger.info(f"[{source_name}] Firecrawl: {len(articles)} articles (main page only)")
            return articles

        logger.info(f"[{source_name}] Firecrawl found {len(fc_urls)} URLs, extracting...")

        # Step 2b: Extract each URL via Firecrawl
        for url in fc_urls:
            article = _firecrawl_scrape(url)
            if article and article["title"]:
                articles.append(article)

        if articles:
            # Remember that this source needs Firecrawl
            _firecrawl_preferred.add(domain)
            logger.info(
                f"[{source_name}] 🔥 Firecrawl extracted {len(articles)} articles "
                f"(source added to Firecrawl-preferred list)"
            )
        else:
            logger.warning(f"[{source_name}] Firecrawl also returned 0 articles")

    except httpx.HTTPError as e:
        logger.error(f"[{source_name}] HTTP error scraping {source_url}: {e}")
    except Exception as e:
        logger.error(f"[{source_name}] Error scraping {source_url}: {e}")

    return articles
