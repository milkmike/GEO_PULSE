"""Deterministic publisher-domain attribution for configured news feeds.

Discovery-market metadata (Google News ``gl``, ``hl`` and ``ceid``) is never
publisher-country evidence.  The only verified mappings produced here come
from exact domains in the curated source catalog or from an explicit alias.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import json
import re
from typing import Literal
from urllib.parse import parse_qs, urlparse

from sqlalchemy import select, text

FeedMode = Literal["publisher", "site_wrapper", "publisher_discovery"]
_FEED_MODES = {"publisher", "site_wrapper", "publisher_discovery"}
_PRESENTATION_PREFIXES = ("www.", "rss.", "feeds.", "feed.", "amp.", "m.")
_GOOGLE_NEWS_DOMAIN = "news.google.com"
_DISALLOWED_PUBLISHER_DOMAIN_FAMILIES = {
    _GOOGLE_NEWS_DOMAIN,
    "allafrica.com",
    "feedburner.com",
    "feedly.com",
    "news.yahoo.com",
    "ria.ru",
    "rt.com",
    "sputnikglobe.com",
    "sputniknews.com",
    "tass.com",
    "tass.ru",
}
_SITE_FILTER_RE = re.compile(r"(?<![-\w])site:([^\s+]+)", re.IGNORECASE)
_DNS_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


@dataclass(frozen=True)
class PublisherMeta:
    name: str | None
    home_url: str | None
    domain: str | None


@dataclass(frozen=True)
class PublisherMatch:
    status: Literal["verified", "reassigned", "unknown", "blocked"]
    publisher_source_id: int | None
    country_code: str | None
    method: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class RegistrySyncReport:
    scanned: int
    verified: int
    blocked: int
    skipped: int


def normalize_publisher_domain(url: str | None) -> str | None:
    """Return an exact ASCII hostname with only presentation prefixes removed.

    This deliberately does not collapse subdomains to a registrable domain.
    """
    if not url or not isinstance(url, str):
        return None
    value = url.strip()
    if not value:
        return None
    parsed_value = value if "://" in value or value.startswith("//") else f"//{value}"
    try:
        host = urlparse(parsed_value).hostname
    except ValueError:
        return None
    if not host:
        return None
    host = host.rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    while True:
        for prefix in _PRESENTATION_PREFIXES:
            if host.startswith(prefix):
                host = host[len(prefix):]
                break
        else:
            break
    if len(host) > 253 or "." not in host:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return None
    labels = host.split(".")
    if any(not _DNS_LABEL_RE.fullmatch(label) for label in labels):
        return None
    return host


def expected_site_domain(url: str | None) -> str | None:
    """Return the one exact publisher domain in a Google News ``site:`` feed."""
    if normalize_publisher_domain(url) != _GOOGLE_NEWS_DOMAIN:
        return None
    try:
        query_values = parse_qs(urlparse(url or "").query).get("q", [])
    except ValueError:
        return None
    domains: set[str] = set()
    match_count = 0
    for query in query_values:
        for raw_domain in _SITE_FILTER_RE.findall(query):
            match_count += 1
            domain = normalize_publisher_domain(raw_domain.rstrip(".,;:)"))
            if domain:
                domains.add(domain)
    if match_count != 1 or len(domains) != 1:
        return None
    return next(iter(domains))


def feed_mode(url: str | None, config: Mapping | None) -> FeedMode:
    """Classify a source without treating locale metadata as country proof."""
    domain = normalize_publisher_domain(url)
    if domain == _GOOGLE_NEWS_DOMAIN:
        return "site_wrapper" if expected_site_domain(url) else "publisher_discovery"
    configured = config.get("feed_mode") if isinstance(config, Mapping) else None
    if configured in _FEED_MODES:
        return configured
    return "publisher"


def candidate_country_from_domain(domain: str | None) -> str | None:
    """Never infer publisher country from ccTLD or any other domain suffix."""
    return None


def direct_source_domain(url: str | None) -> str | None:
    return normalize_publisher_domain(url)


def is_aggregator_domain(domain: str | None) -> bool:
    normalized = normalize_publisher_domain(domain)
    return bool(normalized) and any(
        normalized == blocked or normalized.endswith(f".{blocked}")
        for blocked in _DISALLOWED_PUBLISHER_DOMAIN_FAMILIES
    )


def _source_config(value) -> dict:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _explicit_domain(config: Mapping) -> str | None:
    value = config.get("publisher_domain") or config.get("expected_publisher_domain")
    return normalize_publisher_domain(value) if isinstance(value, str) else None


def _explicit_aliases(config: Mapping) -> tuple[str, ...]:
    values = config.get("publisher_domain_aliases", config.get("publisher_aliases", ()))
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return ()
    aliases = {
        alias
        for value in values
        if isinstance(value, str)
        for alias in (normalize_publisher_domain(value),)
        if alias and not is_aggregator_domain(alias)
    }
    return tuple(sorted(aliases))


def classify_article(session, discovery_source, article: Mapping) -> PublisherMatch:
    """Classify one fetched item before it can reach either deduplication pass."""
    mode = feed_mode(
        getattr(discovery_source, "url", None),
        _source_config(getattr(discovery_source, "config", None)),
    )
    source_country = str(discovery_source.country_code).upper()

    if mode == "publisher":
        return PublisherMatch(
            status="verified",
            publisher_source_id=None,
            country_code=source_country,
            method="source_catalog",
            confidence=1.0,
            reason="direct publisher source",
        )

    domain = normalize_publisher_domain(
        article.get("publisher_domain") or article.get("publisher_url")
    )
    if mode == "site_wrapper":
        expected_domain = expected_site_domain(discovery_source.url)
        if not domain or domain != expected_domain:
            return PublisherMatch(
                status="blocked",
                publisher_source_id=None,
                country_code=None,
                method="site_wrapper",
                confidence=0.0,
                reason=(
                    "site wrapper publisher mismatch: "
                    f"expected {expected_domain or 'one valid site domain'}, "
                    f"got {domain or 'no publisher domain'}"
                ),
            )

    if not domain:
        return PublisherMatch(
            status="unknown",
            publisher_source_id=None,
            country_code=None,
            method="publisher_domain",
            confidence=0.0,
            reason="RSS item has no valid publisher domain",
        )

    from src.db import PublisherDomain

    registry = session.get(PublisherDomain, domain)
    if registry is None:
        return PublisherMatch(
            status="unknown",
            publisher_source_id=None,
            country_code=None,
            method="publisher_domain",
            confidence=0.0,
            reason=f"publisher domain {domain} is not in the verified registry",
        )
    if registry.status != "verified":
        return PublisherMatch(
            status="blocked",
            publisher_source_id=registry.publisher_source_id,
            country_code=registry.country_code,
            method=registry.method,
            confidence=float(registry.confidence),
            reason=f"publisher domain {domain} is blocked in the registry",
        )

    publisher_country = str(registry.country_code).upper()
    return PublisherMatch(
        status="reassigned" if publisher_country != source_country else "verified",
        publisher_source_id=registry.publisher_source_id,
        country_code=publisher_country,
        method=registry.method,
        confidence=float(registry.confidence),
        reason=f"publisher domain {domain} matched the verified registry",
    )


def upsert_article_discovery(session, discovery_source, article: Mapping,
                             match: PublisherMatch):
    """Persist a quarantined discovery without promoting or enqueueing it."""
    from src.db import ArticleDiscovery

    discovery = session.scalar(select(ArticleDiscovery).where(
        ArticleDiscovery.discovery_source_id == discovery_source.id,
        ArticleDiscovery.external_id == article["external_id"],
    ))
    if discovery is None:
        discovery = ArticleDiscovery(
            discovery_source_id=discovery_source.id,
            external_id=article["external_id"],
            published_at=article["published_at"],
            feed_country_code=str(discovery_source.country_code).upper(),
        )
        session.add(discovery)

    discovery.title = article.get("title")
    discovery.body = article.get("body", "")
    discovery.google_url = article.get("url", "")
    discovery.published_at = article["published_at"]
    discovery.feed_country_code = str(discovery_source.country_code).upper()
    discovery.publisher_name = article.get("publisher_name")
    discovery.publisher_url = article.get("publisher_url")
    discovery.publisher_domain = normalize_publisher_domain(
        article.get("publisher_domain") or article.get("publisher_url")
    )
    discovery.geo_status = "blocked" if match.status == "blocked" else "unverified"
    discovery.reason = match.reason
    discovery.raw_metadata = {
        "source": article.get("raw_source") or {},
        "classification": {
            "status": match.status,
            "method": match.method,
            "confidence": match.confidence,
        },
    }
    session.flush()
    return discovery


def _candidate(row: Mapping, domain: str, method: str, *, alias: bool = False) -> dict:
    return {
        "domain": domain,
        "source_id": int(row["id"]),
        "source_name": row["name"],
        "source_url": row["url"],
        "country_code": str(row["country_code"]).upper(),
        "method": method,
        "explicit_alias": alias,
    }


def _existing_candidates(existing, domain: str) -> list[dict]:
    """Recover unresolved registry evidence so a blocked row stays fail-closed."""
    evidence = existing.evidence if isinstance(existing.evidence, Mapping) else {}
    raw_candidates = (
        evidence.get("candidates", ())
        if existing.status == "blocked"
        else (evidence.get("candidate"),)
    )
    recovered = []
    for raw in raw_candidates:
        if not isinstance(raw, Mapping):
            continue
        try:
            source_id = int(raw["source_id"])
            country_code = str(raw["country_code"]).upper()
        except (KeyError, TypeError, ValueError):
            continue
        recovered.append(
            {
                "domain": domain,
                "source_id": source_id,
                "source_name": raw.get("source_name"),
                "source_url": raw.get("source_url"),
                "country_code": country_code,
                "method": raw.get("method") or existing.method,
                "explicit_alias": bool(raw.get("explicit_alias", False)),
                "existing_registry": True,
            }
        )
    if recovered:
        return recovered
    return [
        {
            "domain": domain,
            "source_id": existing.publisher_source_id,
            "source_name": None,
            "source_url": None,
            "country_code": existing.country_code,
            "method": existing.method,
            "explicit_alias": False,
            "existing_registry": True,
        }
    ]


def sync_publisher_domains(session) -> RegistrySyncReport:
    """Seed/update the verified registry from deterministic catalog evidence.

    Existing rows not represented by the catalog are preserved.  Conflicting
    catalog evidence is upserted as ``blocked`` rather than resolved by order.
    """
    from src.db import PublisherDomain

    rows = session.execute(text("""
        SELECT id, name, url, country_code, config
        FROM sources
        ORDER BY id
    """)).mappings().all()
    grouped: dict[str, list[dict]] = defaultdict(list)
    skipped = 0

    for row in rows:
        config = _source_config(row["config"])
        mode = feed_mode(row["url"], config)
        if mode == "publisher_discovery":
            skipped += 1
            continue
        explicit_domain = _explicit_domain(config)
        domain = explicit_domain or expected_site_domain(row["url"]) or direct_source_domain(row["url"])
        if not domain or is_aggregator_domain(domain):
            skipped += 1
            continue
        method = "site_wrapper" if mode == "site_wrapper" else "catalog"
        grouped[domain].append(_candidate(row, domain, method))
        for alias in _explicit_aliases(config):
            grouped[alias].append(_candidate(row, alias, method, alias=True))

    verified = blocked = 0
    now = datetime.now(timezone.utc)
    for domain in sorted(grouped):
        existing = session.get(PublisherDomain, domain)
        candidates = list(grouped[domain])
        if existing is not None and existing.status in {"verified", "blocked"}:
            candidates.extend(_existing_candidates(existing, domain))
        candidates = sorted(
            candidates,
            key=lambda candidate: (candidate["source_id"], candidate["country_code"]),
        )
        unique_candidates = []
        seen_identities: set[tuple[int, str]] = set()
        for candidate in candidates:
            identity = (candidate["source_id"], candidate["country_code"])
            if identity not in seen_identities:
                unique_candidates.append(candidate)
                seen_identities.add(identity)

        conflict = len(seen_identities) > 1 or (
            existing is not None and existing.status == "blocked"
        )
        if conflict:
            blocked += 1
            if existing is not None and existing.status in {"verified", "blocked"}:
                chosen_source_id = existing.publisher_source_id
                chosen_country = existing.country_code
            else:
                chosen_source_id = unique_candidates[0]["source_id"]
                chosen_country = unique_candidates[0]["country_code"]
            status = "blocked"
            method = "conflict"
            confidence = 0.0
            evidence = {
                "reason": "domain maps to multiple curated sources or countries",
                "candidates": unique_candidates,
            }
        else:
            verified += 1
            selected = unique_candidates[0]
            chosen_source_id = selected["source_id"]
            chosen_country = selected["country_code"]
            status = "verified"
            method = selected["method"]
            confidence = 1.0
            evidence = {"candidate": selected}

        if existing is None:
            existing = PublisherDomain(domain=domain)
            session.add(existing)
        existing.publisher_source_id = chosen_source_id
        existing.country_code = chosen_country
        existing.status = status
        existing.method = method
        existing.confidence = confidence
        existing.evidence = evidence
        existing.updated_at = now

    session.flush()
    return RegistrySyncReport(
        scanned=len(rows),
        verified=verified,
        blocked=blocked,
        skipped=skipped,
    )
