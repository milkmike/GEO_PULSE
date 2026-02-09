"""Backfill historical articles from official/mainstream CIS sources.

Strategy:
1. Fetch sitemap index, discover ALL sub-sitemaps
2. Process sub-sitemaps with date filtering (2022+)
3. Extract articles with trafilatura
4. Filter by Russia-relevance keywords
5. Save to DB with dedup
"""
import argparse
import gzip
import json
import logging
import random
import re
import socket
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup
from sqlalchemy import text

from src.db import get_session, wait_for_db
from src.pipeline.dedup import normalize_title, find_duplicate
from src.pipeline.filter import is_relevant

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [backfill] %(levelname)s: %(message)s",
)
logger = logging.getLogger("backfill")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
FETCH_TIMEOUT = httpx.Timeout(10.0, connect=5.0)

FROM_DATE = datetime(2022, 1, 1, tzinfo=timezone.utc)
TO_DATE = datetime(2026, 2, 9, tzinfo=timezone.utc)
SOURCE_TIMEOUT = 900  # 15 min max per source

STATS = {"discovered": 0, "extracted": 0, "saved": 0, "duplicates": 0,
         "skipped_irrelevant": 0, "skipped_notext": 0, "skipped_date": 0, "errors": 0}


# URL patterns that suggest Russia-related content
URL_KEYWORDS = re.compile(
    r'rossi|russia|putin|kreml|lavrov|moskv|moscow|eaeu|eaes|csto|odkb|sng|cis|'
    r'nato|sankcii|sanction|gazprom|rosneft|rosatom|shos|sco',
    re.IGNORECASE
)


def url_might_be_relevant(url: str) -> bool:
    """Quick check if URL path hints at Russia-related content."""
    # If URL contains relevant keywords, definitely process
    if URL_KEYWORDS.search(url):
        return True
    # For general news URLs, we can't tell from URL alone — process them
    return True  # Can't filter by URL for most CIS sites


def is_reachable(url: str, timeout: float = 5.0) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc
    port = 443 if parsed.scheme == 'https' else 80
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except Exception:
        return False


def get_base_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def fetch_url(url: str, client: httpx.Client) -> str | None:
    try:
        resp = client.get(url, timeout=FETCH_TIMEOUT)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        logger.debug(f"  Fetch failed: {url}: {e}")
    return None


def fetch_bytes(url: str, client: httpx.Client) -> bytes | None:
    try:
        resp = client.get(url, timeout=FETCH_TIMEOUT)
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None


def parse_sitemap_xml(content: str) -> tuple[list[str], list[str]]:
    """Parse sitemap XML. Returns (article_urls, sub_sitemap_urls)."""
    urls = []
    sub_sitemaps = []
    try:
        content = re.sub(r'\sxmlns="[^"]+"', '', content)
        root = ET.fromstring(content)

        for sitemap in root.findall('.//sitemap'):
            loc = sitemap.find('loc')
            if loc is not None and loc.text:
                sub_sitemaps.append(loc.text.strip())

        for url_elem in root.findall('.//url'):
            loc = url_elem.find('loc')
            if loc is None or not loc.text:
                continue
            url = loc.text.strip()

            # Filter by lastmod if available
            lastmod = url_elem.find('lastmod')
            if lastmod is not None and lastmod.text:
                try:
                    date_str = lastmod.text.strip()[:10]
                    dt = datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)
                    if dt < FROM_DATE:
                        continue
                except (ValueError, TypeError):
                    pass
            urls.append(url)
    except ET.ParseError:
        pass
    return urls, sub_sitemaps


def should_process_subsitemap(url: str) -> bool:
    """Filter sub-sitemaps to only process news/articles from 2022+."""
    url_lower = url.lower()
    
    # Skip non-news sitemaps
    skip_patterns = ['video', 'photo', 'tag', 'category', 'opinion', 'press', 'suzet',
                     'weather', 'other']
    for pat in skip_patterns:
        if pat in url_lower:
            return False
    
    # Check for year patterns — skip if before 2022
    year_match = re.search(r'[_-](\d{4})[_.-]', url)
    if year_match:
        year = int(year_match.group(1))
        if year < 2022:
            return False
    
    # Check for date patterns like materials-ru-2021-05-01
    date_match = re.search(r'(\d{4})-(\d{2})-(\d{2})', url)
    if date_match:
        year = int(date_match.group(1))
        if year < 2022:
            return False
    
    return True


def discover_all_sitemap_urls(base_url: str, client: httpx.Client) -> list[str]:
    """Discover ALL article URLs from sitemaps, filtering for 2022+."""
    all_urls = []

    # Find sitemaps from robots.txt
    sitemap_candidates = []
    robots = fetch_url(f"{base_url}/robots.txt", client)
    if robots:
        for line in robots.split('\n'):
            if line.lower().strip().startswith('sitemap:'):
                sm_url = line.split(':', 1)[1].strip()
                if sm_url:
                    sitemap_candidates.append(sm_url)

    # Fallback candidates
    if not sitemap_candidates:
        sitemap_candidates = [
            f"{base_url}/sitemap.xml",
            f"{base_url}/sitemap_index.xml",
            f"{base_url}/sitemap-index.xml",
        ]

    visited = set()
    to_process = list(sitemap_candidates)
    subsitemap_count = 0

    while to_process:
        sm_url = to_process.pop(0)
        if sm_url in visited:
            continue
        visited.add(sm_url)

        # Fetch sitemap
        if sm_url.endswith('.gz'):
            raw = fetch_bytes(sm_url, client)
            if not raw:
                continue
            try:
                content = gzip.decompress(raw).decode('utf-8')
            except Exception:
                continue
        else:
            content = fetch_url(sm_url, client)
            if not content:
                continue

        urls, sub_sitemaps = parse_sitemap_xml(content)
        all_urls.extend(urls)

        # Process sub-sitemaps that match our date criteria
        for sub in sub_sitemaps:
            if sub not in visited and should_process_subsitemap(sub):
                to_process.append(sub)
                subsitemap_count += 1

    logger.info(f"  Processed {len(visited)} sitemaps ({subsitemap_count} sub-sitemaps), found {len(all_urls)} URLs")
    return all_urls


def discover_homepage_urls(base_url: str, client: httpx.Client, max_urls: int = 100) -> list[str]:
    urls = set()
    parsed_base = urlparse(base_url)
    html = fetch_url(base_url, client)
    if not html:
        return []
    soup = BeautifulSoup(html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        title_text = a.get_text(strip=True)
        if len(title_text) < 15:
            continue
        if href.startswith("/"):
            href = urljoin(base_url, href)
        elif not href.startswith("http"):
            continue
        parsed = urlparse(href)
        if parsed_base.netloc not in parsed.netloc:
            continue
        skip = re.compile(r'/tag/|/tags/|/category/|/author/|/login|/search|/about|/contact|\.pdf$|\.jpg$|\.mp4$')
        if skip.search(href):
            continue
        urls.add(href)
    return list(urls)[:max_urls]


def extract_article(url: str) -> dict | None:
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
        title = (data.get("title") or "").strip()
        body = (data.get("text") or "").strip()
        if not title or len(body) < 50:
            return None
        published_at = None
        date_str = data.get("date")
        if date_str:
            try:
                published_at = datetime.fromisoformat(date_str)
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass
        return {
            "title": title[:500],
            "body": body[:10000],
            "url": url,
            "published_at": published_at,
        }
    except Exception as e:
        logger.debug(f"Extraction failed for {url}: {e}")
        return None


def backfill_source(source_id: int, source_url: str, source_name: str,
                    country_code: str, max_articles: int = 500) -> int:
    logger.info(f"{'='*60}")
    logger.info(f"Backfilling: {source_name} ({country_code}) - {source_url}")
    start_time = time.time()
    base_url = get_base_url(source_url)

    if not is_reachable(base_url):
        logger.warning(f"  {source_name}: site unreachable, skipping")
        return 0

    client = httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        timeout=FETCH_TIMEOUT,
    )

    try:
        # 1. Discover URLs
        urls = discover_all_sitemap_urls(base_url, client)

        # 2. Fallback to homepage
        if len(urls) < 10:
            homepage_urls = discover_homepage_urls(source_url, client)
            existing_set = set(urls)
            for u in homepage_urls:
                if u not in existing_set:
                    urls.append(u)
            logger.info(f"  + Homepage: {len(homepage_urls)} URLs, total: {len(urls)}")

        if not urls:
            logger.warning(f"  No URLs found for {source_name}")
            return 0

        STATS["discovered"] += len(urls)

        # 3. Get existing external_ids
        with get_session() as session:
            existing = set(r[0] for r in session.execute(
                text("SELECT external_id FROM articles WHERE source_id = :sid"),
                {"sid": source_id}
            ).fetchall())

        new_urls = [u for u in urls if u not in existing]
        logger.info(f"  New URLs: {len(new_urls)} (skipped {len(urls) - len(new_urls)} existing)")

        if not new_urls:
            return 0

        # Shuffle to spread across dates
        random.shuffle(new_urls)
        new_urls = new_urls[:max_articles]

        # 4. Extract and save
        saved = 0
        processed = 0
        for i, url in enumerate(new_urls):
            if time.time() - start_time > SOURCE_TIMEOUT:
                logger.warning(f"  [{source_name}] Source timeout ({SOURCE_TIMEOUT}s)")
                break

            try:
                article = extract_article(url)
                if not article:
                    STATS["skipped_notext"] += 1
                    continue

                processed += 1
                STATS["extracted"] += 1

                pub = article["published_at"]
                if pub and (pub < FROM_DATE or pub > TO_DATE):
                    STATS["skipped_date"] += 1
                    continue
                if not pub:
                    STATS["skipped_date"] += 1
                    continue

                title = article["title"]
                body = article["body"]

                if not is_relevant(title, body):
                    STATS["skipped_irrelevant"] += 1
                    # Don't sleep long for irrelevant articles
                    time.sleep(0.2)
                    continue

                title_norm = normalize_title(title)

                with get_session() as session:
                    parent_id = None
                    if title_norm and len(title_norm) >= 10:
                        try:
                            parent_id = find_duplicate(session, title_norm, country_code, pub)
                        except Exception:
                            parent_id = None
                    if parent_id:
                        STATS["duplicates"] += 1

                    session.execute(text("""
                        INSERT INTO articles
                            (source_id, external_id, title, body, url, published_at,
                             collected_at, title_normalized, is_duplicate, duplicate_of)
                        VALUES
                            (:sid, :eid, :title, :body, :url, :pub,
                             NOW(), :tnorm, :is_dup, :dup_of)
                        ON CONFLICT (source_id, external_id) DO NOTHING
                    """), {
                        "sid": source_id, "eid": url, "title": title,
                        "body": body, "url": url, "pub": pub,
                        "tnorm": title_norm, "is_dup": parent_id is not None,
                        "dup_of": parent_id,
                    })

                saved += 1
                STATS["saved"] += 1

                if saved % 10 == 0:
                    logger.info(f"  [{source_name}] {saved} saved / {processed} extracted / {i+1} attempted")

                time.sleep(0.5)

            except Exception as e:
                STATS["errors"] += 1
                logger.error(f"  Error: {url}: {e}")
                continue

        elapsed = int(time.time() - start_time)
        logger.info(f"  [{source_name}] Done: {saved} articles in {elapsed}s")
        return saved
    finally:
        client.close()


def main():
    parser = argparse.ArgumentParser(description="Backfill historical articles")
    parser.add_argument("--country", help="Country code (e.g. KZ)")
    parser.add_argument("--tier", default="official", help="Source tier")
    parser.add_argument("--max-per-source", type=int, default=500)
    parser.add_argument("--source-id", type=int, help="Single source ID")
    args = parser.parse_args()

    wait_for_db()

    with get_session() as session:
        if args.source_id:
            query = "SELECT id, url, name, country_code FROM sources WHERE id = :sid AND active = true"
            params = {"sid": args.source_id}
        else:
            query = "SELECT id, url, name, country_code FROM sources WHERE active = true AND tier = :tier"
            params = {"tier": args.tier}
            if args.country:
                query += " AND country_code = :cc"
                params["cc"] = args.country

        sources = session.execute(text(query), params).fetchall()

    logger.info(f"Found {len(sources)} sources (tier={args.tier})")

    total = 0
    source_stats = []
    for src in sources:
        count = backfill_source(src.id, src.url, src.name, src.country_code, args.max_per_source)
        total += count
        if count > 0:
            source_stats.append((src.name, src.country_code, count))

    logger.info(f"\n{'='*60}")
    logger.info(f"BACKFILL COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"Total new articles: {total}")
    logger.info(f"URLs discovered: {STATS['discovered']}")
    logger.info(f"Extracted: {STATS['extracted']}")
    logger.info(f"Saved: {STATS['saved']}")
    logger.info(f"Duplicates: {STATS['duplicates']}")
    logger.info(f"Skipped irrelevant: {STATS['skipped_irrelevant']}")
    logger.info(f"Skipped no text/date: {STATS['skipped_notext']}")
    logger.info(f"Skipped wrong date: {STATS['skipped_date']}")
    logger.info(f"Errors: {STATS['errors']}")
    if source_stats:
        logger.info(f"\nPer source:")
        for name, cc, count in sorted(source_stats, key=lambda x: -x[2]):
            logger.info(f"  {cc} | {name}: {count}")


if __name__ == "__main__":
    main()
