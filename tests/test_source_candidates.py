from pathlib import Path

from src import config
from src.collectors.source_candidates import (
    candidate_to_catalog_source,
    load_source_candidates,
)


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
