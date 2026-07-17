from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import feedparser
import yaml

from src.collectors.publisher_attribution import (
    expected_site_domain,
    feed_mode,
    is_aggregator_domain,
    normalize_publisher_domain,
)
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
_ALLOWED_TIERS = {
    "official",
    "mainstream",
    "independent",
    "domestic_opposition",
    "analytics",
}
_ALLOWED_RISKS = {"low", "medium", "high"}
_ALLOWED_STATUSES = {"researched", "promoted", "rejected", "adapter_required"}
_MIN_ENTRY_DATE_RATIO = 0.8
_MAX_FUTURE_SKEW = timedelta(hours=2)
_RTCG_RAW_DATE_FORMATS = (
    ("%d.%m.%YT%H:%M:%S %z", False),
    ("%d.%m.%Y. %H:%M", True),
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


@dataclass(frozen=True)
class CandidateValidation:
    name: str
    country_code: str
    ok: bool
    reasons: tuple[str, ...]
    item_count: int = 0
    newest_age_days: float | None = None
    domain_ratio: float | None = None


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


def _is_absolute_https_url(value: str) -> bool:
    try:
        parsed = urlsplit(value.strip())
        _ = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
    )


def _is_iso_date(value: str) -> bool:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return value == parsed.isoformat()


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
    for field in ("publisher_url", "ownership_evidence_url"):
        if not _is_absolute_https_url(values[field]):
            raise ValueError(
                f"{label} {field} must be an absolute HTTPS URL with a hostname"
            )
    if not _is_iso_date(values["research_date"]):
        raise ValueError(f"{label} research_date must be an ISO date (YYYY-MM-DD)")

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


def configured_publisher_sources(
    config: dict,
) -> dict[str, set[tuple[str, str]]]:
    publishers: dict[str, set[tuple[str, str]]] = {}
    for country_code, country in config.get("countries", {}).items():
        for source in country.get("sources", []):
            source_config = source.get("config") or {}
            source_url = source.get("url")
            mode = feed_mode(source_url, source_config)
            if mode == "publisher_discovery" or not isinstance(source_url, str):
                continue
            explicit_domain = normalize_publisher_domain(
                source_config.get("publisher_domain")
            )
            domain = explicit_domain
            if domain is None and mode == "site_wrapper":
                domain = expected_site_domain(source_url)
            if domain is None:
                domain = normalize_publisher_domain(source_url)
            if domain is None or is_aggregator_domain(domain):
                continue

            raw_aliases = source_config.get("publisher_domain_aliases") or ()
            if isinstance(raw_aliases, str):
                raw_aliases = (raw_aliases,)
            if not isinstance(raw_aliases, (list, tuple, set)):
                raw_aliases = ()
            family = {
                normalized
                for value in (domain, *raw_aliases)
                if isinstance(value, str)
                for normalized in (normalize_publisher_domain(value),)
                if normalized and not is_aggregator_domain(normalized)
            }
            for family_domain in family:
                publishers.setdefault(family_domain, set()).add(
                    (str(country_code).upper(), source_url)
                )
    return publishers


def validate_candidate_metadata(
    candidate: SourceCandidate,
    *,
    configured_publishers: dict[str, set[tuple[str, str]]],
) -> CandidateValidation:
    reasons: list[str] = []
    canonical = normalize_publisher_domain(candidate.canonical_domain)
    feed_domain = normalize_publisher_domain(candidate.feed_url)
    candidate_family = {
        domain
        for value in (candidate.canonical_domain, *candidate.domain_aliases)
        for domain in (normalize_publisher_domain(value),)
        if domain
    }

    if candidate.country_code not in COUNTRIES:
        reasons.append("unknown_country")
    if candidate.source_type != "rss":
        reasons.append("wave1_requires_rss")
    if (
        not canonical
        or any(is_aggregator_domain(domain) for domain in candidate_family)
        or is_aggregator_domain(feed_domain)
    ):
        reasons.append("aggregator_domain")

    existing = {
        identity
        for domain in candidate_family
        for identity in configured_publishers.get(domain, set())
    }
    promoted_self = (
        {(candidate.country_code, candidate.feed_url)}
        if candidate.status == "promoted"
        else set()
    )
    if existing and existing != promoted_self:
        reasons.append("duplicate_publisher_domain")

    if candidate.tier not in _ALLOWED_TIERS:
        reasons.append("invalid_tier")
    if candidate.propaganda_risk not in _ALLOWED_RISKS:
        reasons.append("invalid_propaganda_risk")
    if candidate.syndication_risk not in _ALLOWED_RISKS:
        reasons.append("invalid_syndication_risk")
    if candidate.status not in _ALLOWED_STATUSES:
        reasons.append("invalid_status")

    if not _is_absolute_https_url(candidate.publisher_url):
        reasons.append("publisher_url_must_be_https")
    if not _is_absolute_https_url(candidate.ownership_evidence_url):
        reasons.append("ownership_evidence_must_be_https")
    if not _is_iso_date(candidate.research_date):
        reasons.append("invalid_research_date")

    return CandidateValidation(
        name=candidate.name,
        country_code=candidate.country_code,
        ok=not reasons,
        reasons=tuple(reasons),
    )


def _domain_matches(actual: str | None, expected: set[str]) -> bool:
    return bool(actual) and any(
        actual == domain or actual.endswith(f".{domain}") for domain in expected
    )


def _utc_datetime(stamp: object) -> datetime | None:
    try:
        parts = stamp[:6]  # type: ignore[index]
        return datetime(*parts, tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _rtcg_raw_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    for date_format, assume_utc in _RTCG_RAW_DATE_FORMATS:
        try:
            parsed = datetime.strptime(value, date_format)
        except ValueError:
            continue
        if parsed.strftime(date_format) != value:
            continue
        if assume_utc:
            # The legacy RTCG form has no offset; UTC avoids host-local guessing.
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _entry_datetime(entry: Mapping, *, allow_rtcg_fallback: bool) -> datetime | None:
    raw_entry = dict(entry)
    stamp = raw_entry.get("published_parsed") or raw_entry.get("updated_parsed")
    parsed_date = _utc_datetime(stamp) if stamp else None
    if parsed_date is not None or not allow_rtcg_fallback:
        return parsed_date
    return _rtcg_raw_datetime(
        raw_entry.get("published") or raw_entry.get("updated")
    )


def validate_feed_document(
    candidate: SourceCandidate,
    body: bytes,
    *,
    now: datetime | None = None,
) -> CandidateValidation:
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)

    parsed = feedparser.parse(body)
    entries = list(parsed.entries)
    reasons: list[str] = []
    if not parsed.version or parsed.bozo:
        reasons.append("malformed_feed")
    if len(entries) < 3:
        reasons.append("fewer_than_3_entries")

    expected = {
        domain
        for value in (candidate.canonical_domain, *candidate.domain_aliases)
        for domain in (normalize_publisher_domain(value),)
        if domain
    }
    domains = [normalize_publisher_domain(entry.get("link")) for entry in entries]
    matched = sum(_domain_matches(domain, expected) for domain in domains)
    ratio = matched / len(domains) if domains else 0.0
    if ratio < 0.8:
        reasons.append("publisher_domain_ratio_below_0_8")

    dated: list[datetime] = []
    allow_rtcg_fallback = "rtcg.me" in expected
    for entry in entries:
        parsed_date = _entry_datetime(
            entry,
            allow_rtcg_fallback=allow_rtcg_fallback,
        )
        if parsed_date is not None:
            dated.append(parsed_date)

    newest_age = None
    date_ratio = len(dated) / len(entries) if entries else 0.0
    if date_ratio < _MIN_ENTRY_DATE_RATIO:
        reasons.append("entry_date_ratio_below_0_8")
    if not dated:
        reasons.append("missing_entry_dates")
    else:
        newest = max(dated)
        newest_age = (now - newest).total_seconds() / 86400
        if newest > now + _MAX_FUTURE_SKEW:
            reasons.append("future_entry_date")
        max_age = 30 if candidate.tier in {"independent", "analytics"} else 14
        if newest_age > max_age:
            reasons.append("stale_feed")

    return CandidateValidation(
        name=candidate.name,
        country_code=candidate.country_code,
        ok=not reasons,
        reasons=tuple(reasons),
        item_count=len(entries),
        newest_age_days=round(newest_age, 1) if newest_age is not None else None,
        domain_ratio=round(ratio, 3),
    )
