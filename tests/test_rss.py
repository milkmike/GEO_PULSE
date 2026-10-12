import feedparser
import os
import time
from datetime import datetime, timezone

from src.collectors.rss import _parse_entries
from src.collectors import rss


def test_google_news_entry_preserves_article_url_and_publisher_metadata():
    feed = feedparser.parse("""
        <rss version="2.0">
          <channel>
            <title>Google News</title>
            <item>
              <title>El Gobierno comenta las relaciones con Rusia - EL PAÍS</title>
              <link>https://news.google.com/rss/articles/example</link>
              <pubDate>Wed, 15 Jul 2026 10:00:00 GMT</pubDate>
              <source url="https://elpais.com">EL PAÍS</source>
            </item>
          </channel>
        </rss>
    """)

    [article] = _parse_entries(feed)

    assert article["url"] == "https://news.google.com/rss/articles/example"
    assert article["publisher_name"] == "EL PAÍS"
    assert article["publisher_url"] == "https://elpais.com"
    assert article["publisher_domain"] == "elpais.com"
    assert article["raw_source"] == {
        "href": "https://elpais.com",
        "title": "EL PAÍS",
    }


def test_auto_source_uses_pinned_fetch_for_every_poll(monkeypatch):
    calls = []
    monkeypatch.setattr(rss, "safe_fetch", lambda url, **kw: calls.append(url) or b'''<rss version="2.0"><channel><item><title>One</title><link>https://daily.ad/one</link><pubDate>Sun, 11 Oct 2026 12:00:00 GMT</pubDate></item><item><title>Two</title><link>https://daily.ad/two</link><pubDate>Sat, 10 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>''')
    monkeypatch.setattr(rss.httpx, "get", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("unsafe fetch")))
    config = {"source_research": {"provider": "wikidata"}}
    for _ in range(2):
        articles, status, _ = rss.collect_rss_status("https://daily.ad/feed", config=config)
        assert status == "ok" and len(articles) == 2
    assert calls == ["https://daily.ad/feed", "https://daily.ad/feed"]


def test_auto_source_continues_with_one_valid_new_item_and_filters_unsafe_items(monkeypatch):
    document = b'''<rss version="2.0"><channel>
    <item><title>One</title><link>https://daily.ad/one</link><pubDate>Sun, 11 Oct 2026 12:00:00 GMT</pubDate></item>
    <item><title>Bad</title><link>https://user:pass@daily.ad/private</link><pubDate>Sun, 11 Oct 2026 12:00:00 GMT</pubDate></item>
    <item><title>Undated</title><link>https://daily.ad/undated</link></item>
    </channel></rss>'''
    monkeypatch.setattr(rss, "safe_fetch", lambda *a, **kw: document)
    articles, status, _ = rss.collect_rss_status("https://daily.ad/feed",
        config={"source_research": {"provider": "wikidata"}})
    assert status == "ok" and [item["title"] for item in articles] == ["One"]


def test_rss_dates_are_utc_even_when_collector_timezone_is_not():
    original = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "Asia/Tokyo"
        time.tzset()
        feed = feedparser.parse(b'''<rss version="2.0"><channel><item><title>One</title><link>https://daily.ad/one</link><pubDate>Sun, 11 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>''')
        [article] = _parse_entries(feed)
        assert article["published_at"] == datetime(2026, 10, 11, 12, tzinfo=timezone.utc)
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()
