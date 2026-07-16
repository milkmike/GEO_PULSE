from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import yaml

from scripts.add_native_feeds import render_yaml
from src.collectors.gnews import native_feed_url, site_wrapper_url
from src.collectors.publisher_attribution import (
    PublisherMatch,
    PublisherMeta,
    candidate_country_from_domain,
    expected_site_domain,
    feed_mode,
    normalize_publisher_domain,
)


ROOT = Path(__file__).resolve().parents[1]
NATIVE_SOURCES = ROOT / "src" / "collectors" / "sources_native.yaml"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("HTTPS://WWW.BÜCHER.Example./rss", "xn--bcher-kva.example"),
        ("https://rss.example.com/feed.xml", "example.com"),
        ("https://feeds.example.com/feed.xml", "example.com"),
        ("https://feed.example.com/feed.xml", "example.com"),
        ("https://amp.example.com/story", "example.com"),
        ("https://m.example.com/story", "example.com"),
        ("https://en.example.com/rss", "en.example.com"),
        ("example.com", "example.com"),
        ("/relative/feed.xml", None),
        ("", None),
    ],
)
def test_normalize_publisher_domain_is_exact_and_deterministic(url, expected):
    assert normalize_publisher_domain(url) == expected


def test_native_google_market_is_discovery_not_country_evidence():
    url = native_feed_url("ES", "es")

    assert feed_mode(url, {}) == "publisher_discovery"
    assert feed_mode(url, {"feed_mode": "publisher"}) == "publisher_discovery"
    assert expected_site_domain(url) is None


def test_site_wrapper_has_one_expected_publisher_domain():
    url = site_wrapper_url("https://www.elpais.com/rss", "es")

    assert feed_mode(url, {}) == "site_wrapper"
    assert expected_site_domain(url) == "elpais.com"


def test_ambiguous_site_wrapper_does_not_choose_a_domain():
    url = (
        "https://news.google.com/rss/search?"
        "q=site:one.example+OR+site:two.example&hl=en-US&gl=US&ceid=US:en"
    )

    assert feed_mode(url, {}) == "publisher_discovery"
    assert expected_site_domain(url) is None


def test_country_code_tld_does_not_create_verified_registry_entry():
    assert candidate_country_from_domain("example.me") is None
    assert candidate_country_from_domain("example.es") is None
    assert candidate_country_from_domain("example.ru") is None


def test_attribution_value_objects_are_frozen():
    meta = PublisherMeta(name="El País", home_url="https://elpais.com", domain="elpais.com")
    match = PublisherMatch(
        status="verified",
        publisher_source_id=7,
        country_code="ES",
        method="catalog",
        confidence=1.0,
        reason="exact curated domain",
    )

    with pytest.raises(FrozenInstanceError):
        meta.domain = "other.example"
    with pytest.raises(FrozenInstanceError):
        match.status = "unknown"


def test_native_feed_renderer_emits_explicit_discovery_metadata():
    rendered = yaml.safe_load(
        render_yaml(
            {
                "ES": {
                    "lang": "es",
                    "url": native_feed_url("ES", "es"),
                    "items": 100,
                }
            }
        )
    )
    source = rendered["countries"]["ES"]["sources"][0]

    assert source["config"] == {
        "feed_mode": "publisher_discovery",
        "discovery_country": "ES",
        "provider": "google_news",
    }


def test_generated_native_catalog_marks_all_85_broad_feeds_as_discovery():
    catalog = yaml.safe_load(NATIVE_SOURCES.read_text())
    sources = [
        source
        for country in catalog["countries"].values()
        for source in country.get("sources", [])
    ]

    assert len(sources) == 85
    assert all(source.get("config", {}).get("feed_mode") == "publisher_discovery" for source in sources)
    assert all(source["config"].get("provider") == "google_news" for source in sources)

