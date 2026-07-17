from dataclasses import replace
from datetime import datetime, timezone

import pytest
import yaml

from src import config
from src.collectors.source_candidates import (
    candidate_to_catalog_source,
    configured_publisher_sources,
    load_source_candidates,
    validate_candidate_metadata,
    validate_feed_document,
)


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def _candidate(**overrides):
    candidate = {
        "name": "Example News",
        "country_code": "IE",
        "type": "rss",
        "feed_url": "https://example.test/feed.xml",
        "publisher_url": "https://publisher.example",
        "canonical_domain": "publisher.example",
        "domain_aliases": [],
        "language": "en",
        "tier": "independent",
        "state_affiliated": False,
        "propaganda_risk": "low",
        "syndication_risk": "low",
        "ownership_evidence_url": "https://publisher.example/about",
        "research_date": "2026-07-17",
        "status": "researched",
        "wave": "2026-07-17-rss-1",
    }
    candidate.update(overrides)
    return candidate


def _load_document(tmp_path, document):
    path = tmp_path / "source_candidates.yaml"
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    return load_source_candidates(path)


def _feed(*links: str, published="Thu, 16 Jul 2026 10:00:00 GMT") -> bytes:
    items = "".join(
        f"<item><title>Story {index}</title><link>{link}</link>"
        f"<pubDate>{published}</pubDate></item>"
        for index, link in enumerate(links)
    )
    return (
        f"<rss version='2.0'><channel><title>News</title>{items}</channel></rss>"
    ).encode()


def _atom(*links: str, published="2026-07-16T10:00:00Z") -> bytes:
    entries = "".join(
        f"<entry><title>Story {index}</title><link href='{link}'/>"
        f"<updated>{published}</updated></entry>"
        for index, link in enumerate(links)
    )
    return (
        "<feed xmlns='http://www.w3.org/2005/Atom'><title>News</title>"
        f"<updated>{published}</updated>{entries}</feed>"
    ).encode()


def test_metadata_rejects_loaded_duplicate_and_google_news():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    duplicate = validate_candidate_metadata(
        candidate,
        configured_publishers={
            candidate.canonical_domain: {("ZZ", "https://duplicate.example/feed")},
        },
    )
    assert duplicate.ok is False
    assert duplicate.reasons == ("duplicate_publisher_domain",)

    google = validate_candidate_metadata(
        replace(
            candidate,
            feed_url="https://news.google.com/rss/search?q=site:rtsh.al",
            canonical_domain="news.google.com",
        ),
        configured_publishers={},
    )
    assert "aggregator_domain" in google.reasons


def test_configured_publishers_preserve_country_and_feed_identity():
    publishers = configured_publisher_sources(config.load_sources())
    assert "news.google.com" not in publishers
    assert all(domain == domain.lower() for domain in publishers)
    assert all(
        len(country) == 2 and url.startswith(("http://", "https://"))
        for identities in publishers.values()
        for country, url in identities
    )


def test_configured_publishers_normalize_domains_and_exclude_discovery_feeds():
    publishers = configured_publisher_sources(
        {
            "countries": {
                "ie": {
                    "sources": [
                        {
                            "url": "https://feeds.example.test/latest.xml",
                            "config": {"publisher_domain": "HTTPS://WWW.PUBLISHER.EXAMPLE/"},
                        },
                        {
                            "url": "https://news.google.com/rss/search?q=Ireland&gl=IE",
                            "config": {
                                "feed_mode": "publisher_discovery",
                                "publisher_domain": "should-not-register.example",
                            },
                        },
                    ],
                },
            },
        },
    )

    assert publishers == {
        "publisher.example": {
            ("IE", "https://feeds.example.test/latest.xml"),
        },
    }


def test_promoted_candidate_allows_only_its_exact_catalog_identity():
    candidate = replace(
        load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0],
        status="promoted",
    )
    identity = (candidate.country_code, candidate.feed_url)

    exact = validate_candidate_metadata(
        candidate,
        configured_publishers={candidate.canonical_domain: {identity}},
    )
    conflicting = validate_candidate_metadata(
        candidate,
        configured_publishers={
            candidate.canonical_domain: {
                identity,
                (candidate.country_code, "https://duplicate.example/feed"),
            },
        },
    )

    assert exact.ok is True
    assert "duplicate_publisher_domain" in conflicting.reasons


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"country_code": "ZZ"}, "unknown_country"),
        ({"source_type": "atom"}, "wave1_requires_rss"),
        ({"tier": "social"}, "invalid_tier"),
        ({"propaganda_risk": "severe"}, "invalid_propaganda_risk"),
        ({"syndication_risk": "severe"}, "invalid_syndication_risk"),
        ({"status": "pending"}, "invalid_status"),
        (
            {"ownership_evidence_url": "http://publisher.example/about"},
            "ownership_evidence_must_be_https",
        ),
    ],
)
def test_metadata_rejects_invalid_curated_values(overrides, reason):
    candidate = replace(
        load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0],
        **overrides,
    )

    result = validate_candidate_metadata(candidate, configured_publishers={})

    assert reason in result.reasons


def test_metadata_rejects_aggregator_feed_even_with_publisher_canonical_domain():
    candidate = replace(
        load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0],
        feed_url="https://news.google.com/rss/search?q=site:rtsh.al&gl=AL",
    )

    result = validate_candidate_metadata(candidate, configured_publishers={})

    assert result.ok is False
    assert "aggregator_domain" in result.reasons


def test_feed_validation_accepts_valid_xml_even_with_html_content_type():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    result = validate_feed_document(
        candidate,
        _feed("https://rtsh.al/a", "https://rtsh.al/b", "https://rtsh.al/c"),
        now=NOW,
    )
    assert result.ok is True
    assert result.item_count == 3
    assert result.newest_age_days == 1.1
    assert result.domain_ratio == 1.0


def test_feed_validation_rejects_stale_and_off_domain_entries():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    stale = validate_feed_document(
        candidate,
        _feed(
            "https://rtsh.al/a",
            "https://rtsh.al/b",
            "https://rtsh.al/c",
            published="Thu, 01 Jan 2026 10:00:00 GMT",
        ),
        now=NOW,
    )
    assert "stale_feed" in stale.reasons

    foreign = validate_feed_document(
        candidate,
        _feed(
            "https://rtsh.al/a",
            "https://example.com/b",
            "https://example.com/c",
        ),
        now=NOW,
    )
    assert "publisher_domain_ratio_below_0_8" in foreign.reasons


def test_feed_validation_accepts_atom_aliases_at_exact_ratio_boundary():
    candidate = replace(
        load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0],
        domain_aliases=("alias.rtsh.example",),
    )
    result = validate_feed_document(
        candidate,
        _atom(
            "https://rtsh.al/a",
            "https://news.rtsh.al/b",
            "https://alias.rtsh.example/c",
            "https://m.alias.rtsh.example/d",
            "https://example.com/e",
        ),
        now=NOW,
    )

    assert result.ok is True
    assert result.item_count == 5
    assert result.newest_age_days == 1.1
    assert result.domain_ratio == 0.8


def test_feed_validation_requires_entries_and_publication_dates():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    malformed = validate_feed_document(candidate, b"<html>not a feed</html>", now=NOW)
    undated = validate_feed_document(
        candidate,
        (
            "<rss><channel><title>News</title>"
            "<item><link>https://rtsh.al/a</link></item>"
            "<item><link>https://rtsh.al/b</link></item>"
            "<item><link>https://rtsh.al/c</link></item>"
            "</channel></rss>"
        ).encode(),
        now=NOW,
    )

    assert "malformed_feed" in malformed.reasons
    assert "fewer_than_3_entries" in malformed.reasons
    assert "missing_entry_dates" in undated.reasons


def test_feed_validation_uses_longer_freshness_window_for_independent_sources():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    body = _feed(
        "https://rtsh.al/a",
        "https://rtsh.al/b",
        "https://rtsh.al/c",
        published="Mon, 29 Jun 2026 12:00:00 GMT",
    )

    mainstream = validate_feed_document(
        replace(candidate, tier="mainstream"),
        body,
        now=NOW,
    )
    independent = validate_feed_document(
        replace(candidate, tier="independent"),
        body,
        now=NOW,
    )

    assert "stale_feed" in mainstream.reasons
    assert independent.ok is True


def test_wave1_candidate_registry_is_complete_and_not_loaded():
    candidates = load_source_candidates(config.SOURCE_CANDIDATES_PATH)
    assert len(candidates) == 17
    assert len({item.feed_url for item in candidates}) == 17
    assert {item.country_code for item in candidates} == {
        "AL", "CY", "DK", "IE", "ME", "MK", "PT", "SI", "SG",
    }

    production_urls = {
        source["url"]
        for country in config.load_sources()["countries"].values()
        for source in country.get("sources", [])
    }
    assert production_urls.isdisjoint({item.feed_url for item in candidates})


def test_candidate_conversion_preserves_curated_domain_evidence():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    source = candidate_to_catalog_source(candidate)
    assert source == {
        "name": candidate.name,
        "type": "rss",
        "url": candidate.feed_url,
        "weight": 1.0,
        "language": candidate.language,
        "tier": candidate.tier,
        "state_affiliated": candidate.state_affiliated,
        "propaganda_risk": candidate.propaganda_risk,
        "config": {
            "publisher_domain": candidate.canonical_domain,
            "publisher_domain_aliases": list(candidate.domain_aliases),
            "source_expansion_wave": "2026-07-17-rss-1",
        },
    }


@pytest.mark.parametrize("root", [None, [], "not a registry"])
def test_loader_rejects_non_mapping_registry_roots(tmp_path, root):
    with pytest.raises(ValueError, match="registry root must be a mapping"):
        _load_document(tmp_path, root)


def test_loader_rejects_invalid_yaml(tmp_path):
    path = tmp_path / "source_candidates.yaml"
    path.write_text("version: [1\ncandidates:")

    with pytest.raises(ValueError, match="registry must be valid YAML"):
        load_source_candidates(path)


def test_loader_requires_exact_root_schema(tmp_path):
    document = {"version": 1, "candidates": [], "unexpected": True}

    with pytest.raises(ValueError, match="registry fields must be exactly"):
        _load_document(tmp_path, document)


def test_loader_rejects_non_list_candidates(tmp_path):
    document = {"version": 1, "candidates": {"name": "not a list"}}

    with pytest.raises(ValueError, match="candidates must be a list"):
        _load_document(tmp_path, document)


def test_loader_rejects_non_mapping_candidate_records(tmp_path):
    document = {"version": 1, "candidates": ["not a record"]}

    with pytest.raises(ValueError, match="candidate 1 must be a mapping"):
        _load_document(tmp_path, document)


@pytest.mark.parametrize(
    "record",
    [
        pytest.param(
            {key: value for key, value in _candidate().items() if key != "wave"},
            id="missing-field",
        ),
        pytest.param({**_candidate(), "unexpected": True}, id="unknown-field"),
    ],
)
def test_loader_requires_exact_candidate_schema(tmp_path, record):
    with pytest.raises(ValueError, match="candidate 1 fields must be exactly"):
        _load_document(tmp_path, {"version": 1, "candidates": [record]})


def test_loader_rejects_boolean_registry_version(tmp_path):
    with pytest.raises(ValueError, match="registry version must be integer 1"):
        _load_document(tmp_path, {"version": True, "candidates": []})


@pytest.mark.parametrize(
    "field",
    [
        "name",
        "country_code",
        "type",
        "feed_url",
        "publisher_url",
        "canonical_domain",
        "language",
        "tier",
        "propaganda_risk",
        "syndication_risk",
        "ownership_evidence_url",
        "research_date",
        "status",
        "wave",
    ],
)
def test_loader_requires_non_empty_string_fields(tmp_path, field):
    record = _candidate(**{field: 42})

    with pytest.raises(ValueError, match=rf"candidate 1 {field} must be a non-empty string"):
        _load_document(tmp_path, {"version": 1, "candidates": [record]})


def test_loader_validates_country_code_against_country_registry(tmp_path):
    record = _candidate(country_code="ZZ")

    with pytest.raises(ValueError, match="candidate 1 country_code is not supported"):
        _load_document(tmp_path, {"version": 1, "candidates": [record]})


def test_loader_only_accepts_rss_source_type(tmp_path):
    record = _candidate(**{"type": "atom"})

    with pytest.raises(ValueError, match="candidate 1 type must be rss"):
        _load_document(tmp_path, {"version": 1, "candidates": [record]})


@pytest.mark.parametrize("value", ["false", 0, 1, None])
def test_loader_requires_a_real_boolean_for_state_affiliated(tmp_path, value):
    record = _candidate(state_affiliated=value)

    with pytest.raises(ValueError, match="candidate 1 state_affiliated must be a boolean"):
        _load_document(tmp_path, {"version": 1, "candidates": [record]})


@pytest.mark.parametrize("value", ["alias.example", {}, None])
def test_loader_requires_domain_aliases_to_be_a_list(tmp_path, value):
    record = _candidate(domain_aliases=value)

    with pytest.raises(ValueError, match="candidate 1 domain_aliases must be a list"):
        _load_document(tmp_path, {"version": 1, "candidates": [record]})


def test_loader_normalizes_canonical_and_alias_domains(tmp_path):
    record = _candidate(
        canonical_domain="HTTPS://WWW.BÜCHER.Example./about",
        domain_aliases=["RSS.ALIAS.Example."],
    )

    [candidate] = _load_document(
        tmp_path,
        {"version": 1, "candidates": [record]},
    )

    assert candidate.canonical_domain == "xn--bcher-kva.example"
    assert candidate.domain_aliases == ("alias.example",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("canonical_domain", "localhost"),
        ("domain_aliases", ["bad_domain.example"]),
    ],
)
def test_loader_rejects_invalid_publisher_domains(tmp_path, field, value):
    record = _candidate(**{field: value})

    with pytest.raises(ValueError, match=rf"candidate 1 {field} contains an invalid domain"):
        _load_document(tmp_path, {"version": 1, "candidates": [record]})


def test_loader_rejects_normalized_duplicate_feed_urls(tmp_path):
    first = _candidate(
        name="First",
        feed_url="HTTPS://WWW.Example.test/feed/?b=2&a=1#top",
        canonical_domain="first.example",
    )
    second = _candidate(
        name="Second",
        feed_url="https://example.test/feed?a=1&b=2",
        canonical_domain="second.example",
    )

    with pytest.raises(ValueError, match="candidate 2 duplicates feed_url from candidate 1"):
        _load_document(tmp_path, {"version": 1, "candidates": [first, second]})


def test_loader_rejects_overlapping_canonical_and_alias_domain_families(tmp_path):
    first = _candidate(
        name="First",
        feed_url="https://first.example/feed",
        canonical_domain="first.example",
        domain_aliases=["www.shared.example"],
    )
    second = _candidate(
        name="Second",
        feed_url="https://second.example/feed",
        canonical_domain="RSS.SHARED.EXAMPLE.",
    )

    with pytest.raises(
        ValueError,
        match="candidate 2 publisher domain shared.example overlaps candidate 1",
    ):
        _load_document(tmp_path, {"version": 1, "candidates": [first, second]})
