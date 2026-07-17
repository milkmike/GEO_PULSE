from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class SourceCandidate:
    name: str
    country_code: str
    source_type: str
    feed_url: str
    publisher_url: str
    canonical_domain: str
    domain_aliases: tuple[str, ...]
    language: str
    tier: str
    state_affiliated: bool
    propaganda_risk: str
    syndication_risk: str
    ownership_evidence_url: str
    research_date: str
    status: str
    wave: str


def load_source_candidates(path: Path) -> list[SourceCandidate]:
    raw = yaml.safe_load(path.read_text()) or {}
    if raw.get("version") != 1:
        raise ValueError("source candidate registry version must be 1")
    return [
        SourceCandidate(
            name=item["name"],
            country_code=str(item["country_code"]).upper(),
            source_type=item["type"],
            feed_url=item["feed_url"],
            publisher_url=item["publisher_url"],
            canonical_domain=item["canonical_domain"],
            domain_aliases=tuple(item.get("domain_aliases", [])),
            language=item["language"],
            tier=item["tier"],
            state_affiliated=bool(item["state_affiliated"]),
            propaganda_risk=item["propaganda_risk"],
            syndication_risk=item["syndication_risk"],
            ownership_evidence_url=item["ownership_evidence_url"],
            research_date=str(item["research_date"]),
            status=item["status"],
            wave=item["wave"],
        )
        for item in raw.get("candidates", [])
    ]


def candidate_to_catalog_source(candidate: SourceCandidate) -> dict:
    return {
        "name": candidate.name,
        "type": candidate.source_type,
        "url": candidate.feed_url,
        "weight": 1.0,
        "language": candidate.language,
        "tier": candidate.tier,
        "state_affiliated": candidate.state_affiliated,
        "propaganda_risk": candidate.propaganda_risk,
        "config": {
            "publisher_domain": candidate.canonical_domain,
            "publisher_domain_aliases": list(candidate.domain_aliases),
            "source_expansion_wave": candidate.wave,
        },
    }
