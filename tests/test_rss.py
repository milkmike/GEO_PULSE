import feedparser

from src.collectors.rss import _parse_entries


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
