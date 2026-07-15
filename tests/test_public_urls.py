"""Shared security contract for URLs exposed by public API routes."""

from __future__ import annotations

import pytest

from src.api.routes import entities, investigations, search, signal_detail, stories


PUBLIC_URL_VALIDATORS = (
    search._safe_http_url,
    entities.safe_public_url,
    signal_detail.safe_public_url,
    stories.safe_public_url,
    investigations.safe_public_url,
)


@pytest.mark.parametrize("validator", PUBLIC_URL_VALIDATORS)
@pytest.mark.parametrize(
    "url",
    (
        "https://user@example.com/story",
        "https://user:secret@example.com/story",
        "https://example.com/path\\segment",
        "https://example.com/path%5csegment",
        "https://example.com/path%5Csegment",
        "https://example.com/\x00story",
        "https://example.com/\x1fstory",
        "https://example.com/\x7fstory",
        "https://example.com/%00story",
        "https://example.com/%1fstory",
        "https://example.com/%7Fstory",
        "https://-bad.example/story",
        "https://bad-.example/story",
        "https://bad..example/story",
        "https://example.com../story",
        "https://example.com:not-a-port/story",
        "https://example.com:70000/story",
    ),
)
def test_public_api_url_boundaries_reject_unsafe_urls(validator, url):
    assert validator(url) is None


@pytest.mark.parametrize("validator", PUBLIC_URL_VALIDATORS)
@pytest.mark.parametrize(
    "url",
    (
        "https://news.example/story?id=1#source",
        "http://127.0.0.1:8080/story",
        "https://news.example./story",
        "https://[2001:db8::1]/story",
        "https://пример.рф/новости",
    ),
)
def test_public_api_url_boundaries_preserve_valid_urls(validator, url):
    assert validator(url) == url
