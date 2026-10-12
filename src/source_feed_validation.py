"""Small, unauthenticated, DNS-pinned probes for researched publisher feeds."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
import ipaddress
import queue
import socket
import threading
import time
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx

from src.api.public_urls import safe_public_url

MAX_BYTES = 512_000
MAX_REDIRECTS = 2
STANDARD_PATHS = ("/feed", "/rss", "/feed.xml", "/rss.xml")
_LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".internal", ".home", ".onion", ".invalid")
_DNS_SLOTS = threading.BoundedSemaphore(4)


class FeedUnsafe(Exception):
    pass


class FeedUnavailable(Exception):
    pass


def _resolve(hostname: str, port: int, timeout: float):
    """Bound the caller and number of outstanding blocking OS resolver calls."""
    started = time.monotonic()
    if not _DNS_SLOTS.acquire(timeout=timeout):
        raise FeedUnavailable("dns_busy")
    result = queue.Queue(maxsize=1)
    def work():
        try:
            result.put((socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM), None))
        except OSError as exc:
            result.put((None, exc))
        finally:
            _DNS_SLOTS.release()
    threading.Thread(target=work, name="feed-dns", daemon=True).start()
    try:
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            raise queue.Empty
        addresses, error = result.get(timeout=remaining)
    except queue.Empty as exc:
        raise FeedUnavailable("dns_timeout") from exc
    if error:
        raise FeedUnavailable("dns") from error
    return addresses


def _host_family(host: str) -> set[str]:
    base = host[4:] if host.startswith("www.") else host
    return {base, "www." + base}


def _target(url: str, *, host: str | None = None) -> tuple[str, int]:
    if safe_public_url(url) is None or len(url) > 2048:
        raise FeedUnsafe("invalid_url")
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").lower()
    if (not hostname or hostname == "localhost" or hostname.endswith(_LOCAL_SUFFIXES)
            or "." not in hostname or parsed.port not in (None, 80, 443)
            or parsed.port not in (None, 443 if parsed.scheme == "https" else 80)
            or (host is not None and hostname not in _host_family(host))):
        raise FeedUnsafe("invalid_host")
    try:
        if not ipaddress.ip_address(hostname).is_global:
            raise FeedUnsafe("private_host")
    except ValueError:
        pass
    return hostname, parsed.port or (443 if parsed.scheme == "https" else 80)


def safe_fetch(url: str, *, client: httpx.Client | None = None,
               deadline: float | None = None, host: str | None = None) -> bytes:
    """Pin every hop to a resolved public IP; same original host only."""
    own = client is None
    if own:
        client = httpx.Client(trust_env=False, follow_redirects=False)
    deadline = deadline or time.monotonic() + 8
    try:
        original, _ = _target(url, host=host)
        original_scheme = urlsplit(url).scheme
        for _ in range(MAX_REDIRECTS + 1):
            hostname, port = _target(url, host=original)
            if original_scheme == "https" and urlsplit(url).scheme != "https":
                raise FeedUnsafe("scheme_downgrade")
            if time.monotonic() >= deadline:
                raise FeedUnavailable("timeout")
            remaining_dns = max(0.1, min(2, deadline - time.monotonic()))
            addresses = _resolve(hostname, port, remaining_dns)
            ips = {str(item[4][0]) for item in addresses}
            if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
                raise FeedUnsafe("private_dns")
            ip = sorted(ips)[0]
            parts = urlsplit(url)
            authority = f"[{ip}]" if ":" in ip else ip
            pinned = parts._replace(netloc=authority).geturl()
            remaining = max(0.1, min(2, deadline - time.monotonic()))
            request = httpx.Request("GET", pinned,
                headers={"Host": hostname, "User-Agent": "GEO-PULSE source validator/1.0", "Accept": "application/rss+xml, application/atom+xml, text/html;q=0.5", "Accept-Encoding": "identity"},
                extensions={"sni_hostname": hostname,
                            "timeout": {"connect": remaining, "read": remaining,
                                        "write": remaining, "pool": remaining}})
            try:
                response = client.send(request, stream=True, follow_redirects=False, auth=None)
                try:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise FeedUnavailable("redirect")
                        url = urljoin(url, location)
                        _target(url, host=original)
                        continue
                    if response.status_code != 200:
                        raise FeedUnavailable("http_status")
                    if response.headers.get("content-encoding", "identity").lower() != "identity":
                        raise FeedUnavailable("encoded_payload")
                    length = response.headers.get("content-length")
                    if length and int(length) > MAX_BYTES:
                        raise FeedUnavailable("oversize")
                    if response.is_stream_consumed:
                        # In-memory transports can supply an already buffered
                        # response; live HTTP uses the bounded raw stream below.
                        body = response.content
                        if len(body) > MAX_BYTES or time.monotonic() >= deadline:
                            raise FeedUnavailable("oversize_or_timeout")
                        return body
                    body = bytearray()
                    for chunk in response.iter_raw():
                        body.extend(chunk)
                        if len(body) > MAX_BYTES or time.monotonic() >= deadline:
                            raise FeedUnavailable("oversize_or_timeout")
                    return bytes(body)
                finally:
                    response.close()
            except httpx.HTTPError as exc:
                raise FeedUnavailable("network") from exc
        raise FeedUnavailable("redirect_limit")
    finally:
        if own:
            client.close()


class _Alternates(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "link":
            return
        fields = dict(attrs)
        rel = (fields.get("rel") or "").lower().split()
        kind = (fields.get("type") or "").lower()
        if "alternate" in rel and kind in ("application/rss+xml", "application/atom+xml"):
            if fields.get("href"):
                self.hrefs.append(fields["href"])


def feed_urls(website: str, html: bytes) -> list[str]:
    hostname, _ = _target(website)
    parser = _Alternates()
    parser.feed(html[:MAX_BYTES].decode("utf-8", errors="ignore"))
    root = f"{urlsplit(website).scheme}://{hostname}"
    result: list[str] = []
    for href in [*parser.hrefs[:8], *STANDARD_PATHS]:
        url = urljoin(website if href not in STANDARD_PATHS else root, href)
        try:
            _target(url, host=hostname)
        except FeedUnsafe:
            continue
        if url not in result:
            result.append(url)
        if len(result) >= 4:
            break
    return result


def validate_feed(document: bytes, hostname: str, *, now: datetime) -> bool:
    if len(document) > MAX_BYTES:
        return False
    feed = feedparser.parse(document)
    if not feed.version or not feed.entries:
        return False
    dated = []
    identities = set()
    matched = 0
    for entry in feed.entries[:20]:
        link = entry.get("link")
        if not isinstance(link, str) or not (entry.get("title") or entry.get("summary")):
            continue
        try:
            _target(link, host=hostname)
            matched += 1
        except FeedUnsafe:
            continue
        identity = entry.get("id") or link
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if parsed and identity not in identities:
            identities.add(identity)
            dated.append(datetime(*parsed[:6], tzinfo=timezone.utc))
    count = min(len(feed.entries), 20)
    return (matched / count >= 0.8 and len(dated) >= 1
            and len(dated) / count >= 0.8
            and max(dated) >= now - timedelta(days=14)
            and max(dated) <= now + timedelta(hours=2))


def safe_entry_link(url: object, feed_url: str) -> bool:
    """Check item identity without fetching the article URL."""
    if not isinstance(url, str):
        return False
    try:
        _target(url, host=(urlsplit(feed_url).hostname or ""))
    except FeedUnsafe:
        return False
    return True


def find_valid_feed(website: str, *, now: datetime, deadline: float | None = None,
                    client: httpx.Client | None = None) -> str | None:
    hostname, _ = _target(website)
    if urlsplit(website).scheme == "http":
        # Probe the same claimed website over TLS; never send the HTTP URL.
        website = urlsplit(website)._replace(scheme="https", netloc=hostname).geturl()
    deadline = deadline or time.monotonic() + 20
    try:
        homepage = safe_fetch(website, host=hostname, deadline=deadline, client=client)
    except FeedUnavailable:
        homepage = b""
    for url in feed_urls(website, homepage):
        if time.monotonic() >= deadline:
            break
        if urlsplit(url).scheme != "https":
            continue
        try:
            body = safe_fetch(url, host=hostname, deadline=deadline, client=client)
            if validate_feed(body, hostname, now=now):
                return url
        except (FeedUnavailable, FeedUnsafe):
            continue
    return None
