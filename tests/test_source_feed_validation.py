from datetime import datetime, timezone
import socket

import httpx
import pytest

from src import source_feed_validation as feeds

NOW = datetime(2026, 10, 12, tzinfo=timezone.utc)
RSS = b'''<rss version="2.0"><channel><title>Daily</title>
<item><title>One</title><link>https://daily.ad/one</link><pubDate>Sun, 11 Oct 2026 12:00:00 GMT</pubDate></item>
<item><title>Two</title><link>https://daily.ad/two</link><pubDate>Sat, 10 Oct 2026 12:00:00 GMT</pubDate></item>
</channel></rss>'''


def test_feed_requires_multiple_dated_recent_same_domain_entries():
    assert feeds.validate_feed(RSS, "daily.ad", now=NOW)
    assert not feeds.validate_feed(RSS.replace(b"https://daily.ad/two", b"https://other.ad/two"), "daily.ad", now=NOW)
    assert not feeds.validate_feed(RSS.replace(b"2026", b"2020"), "daily.ad", now=NOW)
    assert not feeds.validate_feed(RSS.split(b"<item>")[0] + b"</channel></rss>", "daily.ad", now=NOW)


def test_pinned_fetch_blocks_private_dns_and_redirect(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))])
    with pytest.raises(feeds.FeedUnsafe):
        feeds.safe_fetch("https://daily.ad/feed")

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        with pytest.raises(feeds.FeedUnsafe):
            feeds.safe_fetch("https://daily.ad/feed", client=client)
    assert seen[0].headers["host"] == "daily.ad"
    assert seen[0].extensions["sni_hostname"] == "daily.ad"


def test_homepage_unavailable_still_probes_standard_feed(monkeypatch):
    calls = []
    def fetch(url, **kw):
        calls.append(url)
        if url == "https://daily.ad/":
            raise feeds.FeedUnavailable("http_status")
        return RSS
    monkeypatch.setattr(feeds, "safe_fetch", fetch)
    assert feeds.find_valid_feed("https://daily.ad/", now=NOW) == "https://daily.ad/feed"
    assert calls[:2] == ["https://daily.ad/", "https://daily.ad/feed"]


def test_claimed_http_website_is_probed_only_via_same_host_https(monkeypatch):
    calls = []
    def fetch(url, **kw):
        calls.append(url)
        return b"" if url.endswith("/") else RSS
    monkeypatch.setattr(feeds, "safe_fetch", fetch)
    assert feeds.find_valid_feed("http://daily.ad:80/", now=NOW) == "https://daily.ad/feed"
    assert calls[0] == "https://daily.ad/"
    assert all(url.startswith("https://daily.ad/") for url in calls)


def test_www_redirect_is_same_publisher_but_downgrade_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    def handler(request):
        if request.headers["host"] == "daily.ad":
            return httpx.Response(302, headers={"location": "https://www.daily.ad/feed"})
        return httpx.Response(200, content=RSS)
    with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
        assert feeds.safe_fetch("https://daily.ad/feed", client=client) == RSS
    def downgrade(request):
        return httpx.Response(302, headers={"location": "http://daily.ad/feed"})
    with httpx.Client(transport=httpx.MockTransport(downgrade), trust_env=False) as client:
        with pytest.raises(feeds.FeedUnsafe):
            feeds.safe_fetch("https://daily.ad/feed", client=client)


def test_discover_same_host_alternate_and_reject_bad_ports():
    html = b'<html><head><link rel="alternate" type="application/rss+xml" href="/rss.xml"></head></html>'
    assert feeds.feed_urls("https://daily.ad/", html) == ["https://daily.ad/rss.xml", "https://daily.ad/feed", "https://daily.ad/rss", "https://daily.ad/feed.xml", "https://daily.ad/rss.xml"][:4]
    with pytest.raises(feeds.FeedUnsafe):
        feeds.safe_fetch("https://daily.ad:8080/feed")


def test_trickling_body_is_checked_per_raw_chunk_and_compression_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))])
    state = {"tick": 0, "chunks": 0}
    monkeypatch.setattr(feeds.time, "monotonic", lambda: state["tick"])
    class Trickle(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(10):
                state["chunks"] += 1
                if state["chunks"] == 2:
                    state["tick"] = 11
                yield b"x"
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=Trickle())), trust_env=False) as client:
        with pytest.raises(feeds.FeedUnavailable):
            feeds.safe_fetch("https://daily.ad/feed", client=client, deadline=10)
    assert state["chunks"] == 2
    state["tick"] = 0
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"compressed", headers={"content-encoding": "gzip"})), trust_env=False) as client:
        with pytest.raises(feeds.FeedUnavailable):
            feeds.safe_fetch("https://daily.ad/feed", client=client, deadline=10)


def test_dns_timeout_budget_includes_semaphore_wait(monkeypatch):
    state = {"tick": 0, "queue_timeout": None}
    monkeypatch.setattr(feeds.time, "monotonic", lambda: state["tick"])
    class Slot:
        def acquire(self, timeout):
            state["tick"] = 0.4
            return True
        def release(self):
            pass
    monkeypatch.setattr(feeds, "_DNS_SLOTS", Slot())
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: [])
    def get(self, timeout):
        state["queue_timeout"] = timeout
        return [], None
    monkeypatch.setattr(feeds.queue.Queue, "get", get)
    feeds._resolve("daily.ad", 443, 1)
    assert state["queue_timeout"] == pytest.approx(0.6)
