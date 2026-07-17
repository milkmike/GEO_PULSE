from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import yaml

from src.collectors.publisher_attribution import normalize_publisher_domain
from src.countries import COUNTRIES


_ROOT_FIELDS = frozenset({"version", "candidates"})
_CANDIDATE_FIELDS = frozenset(
    {
        "name",
        "country_code",
        "type",
        "feed_url",
        "publisher_url",
        "canonical_domain",
        "domain_aliases",
        "language",
        "tier",
        "state_affiliated",
        "propaganda_risk",
        "syndication_risk",
        "ownership_evidence_url",
        "research_date",
        "status",
        "wave",
    }
)
_STRING_FIELDS = (
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
)


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


def _require_exact_fields(
    value: Mapping,
    expected: frozenset[str],
    label: str,
) -> None:
    if set(value) != expected:
        fields = ", ".join(sorted(expected))
        raise ValueError(f"{label} fields must be exactly: {fields}")


def _require_non_empty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _normalize_domain(value: str, label: str) -> str:
    normalized = normalize_publisher_domain(value)
    if normalized is None:
        raise ValueError(f"{label} contains an invalid domain")
    return normalized


def _feed_url_identity(value: str, label: str) -> tuple:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} must be an absolute HTTP(S) URL") from exc
    scheme = parsed.scheme.lower()
    domain = normalize_publisher_domain(value)
    if (
        scheme not in {"http", "https"}
        or domain is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{label} must be an absolute HTTP(S) URL")
    if port == (443 if scheme == "https" else 80):
        port = None
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    query = tuple(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return scheme, domain, port, path, query


def _candidate_from_record(item: Mapping, index: int) -> tuple[SourceCandidate, tuple]:
    label = f"candidate {index}"
    _require_exact_fields(item, _CANDIDATE_FIELDS, label)
    values = {
        field: _require_non_empty_string(item[field], f"{label} {field}")
        for field in _STRING_FIELDS
    }

    country_code = values["country_code"].strip().upper()
    if country_code not in COUNTRIES:
        raise ValueError(f"{label} country_code is not supported")
    if values["type"] != "rss":
        raise ValueError(f"{label} type must be rss")
    if type(item["state_affiliated"]) is not bool:
        raise ValueError(f"{label} state_affiliated must be a boolean")

    raw_aliases = item["domain_aliases"]
    if not isinstance(raw_aliases, list):
        raise ValueError(f"{label} domain_aliases must be a list")
    aliases: list[str] = []
    for value in raw_aliases:
        alias = _require_non_empty_string(
            value,
            f"{label} domain_aliases entry",
        )
        aliases.append(_normalize_domain(alias, f"{label} domain_aliases"))

    canonical_domain = _normalize_domain(
        values["canonical_domain"],
        f"{label} canonical_domain",
    )
    domain_family = (canonical_domain, *aliases)
    if len(domain_family) != len(set(domain_family)):
        raise ValueError(f"{label} publisher domain family contains duplicates")

    feed_identity = _feed_url_identity(values["feed_url"], f"{label} feed_url")
    candidate = SourceCandidate(
        name=values["name"],
        country_code=country_code,
        source_type=values["type"],
        feed_url=values["feed_url"],
        publisher_url=values["publisher_url"],
        canonical_domain=canonical_domain,
        domain_aliases=tuple(aliases),
        language=values["language"],
        tier=values["tier"],
        state_affiliated=item["state_affiliated"],
        propaganda_risk=values["propaganda_risk"],
        syndication_risk=values["syndication_risk"],
        ownership_evidence_url=values["ownership_evidence_url"],
        research_date=values["research_date"],
        status=values["status"],
        wave=values["wave"],
    )
    return candidate, feed_identity


def load_source_candidates(path: Path) -> list[SourceCandidate]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("source candidate registry must be valid YAML") from exc
    if not isinstance(raw, Mapping):
        raise ValueError("source candidate registry root must be a mapping")
    _require_exact_fields(raw, _ROOT_FIELDS, "source candidate registry")
    if type(raw["version"]) is not int or raw["version"] != 1:
        raise ValueError("source candidate registry version must be integer 1")
    records = raw["candidates"]
    if not isinstance(records, list):
        raise ValueError("source candidate registry candidates must be a list")

    candidates: list[SourceCandidate] = []
    feed_owners: dict[tuple, int] = {}
    domain_owners: dict[str, int] = {}
    for index, item in enumerate(records, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"source candidate {index} must be a mapping")
        candidate, feed_identity = _candidate_from_record(item, index)

        prior_index = feed_owners.get(feed_identity)
        if prior_index is not None:
            raise ValueError(
                f"candidate {index} duplicates feed_url from candidate {prior_index}"
            )
        feed_owners[feed_identity] = index

        for domain in (candidate.canonical_domain, *candidate.domain_aliases):
            prior_index = domain_owners.get(domain)
            if prior_index is not None:
                raise ValueError(
                    f"candidate {index} publisher domain {domain} "
                    f"overlaps candidate {prior_index}"
                )
            domain_owners[domain] = index
        candidates.append(candidate)
    return candidates


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
