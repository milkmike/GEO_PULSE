"""Bounded Wikidata discovery of unverified local publisher leads.

This module never fetches returned websites, infers feeds or ownership, or
activates a source. Wikidata claims are leads for editorial verification.
"""
from __future__ import annotations

import ipaddress
import json
import re
from time import monotonic
from urllib.parse import urlsplit

import httpx

from src.api.public_urls import safe_public_url
from src.monitoring_registry import MONITORING_COUNTRIES


_ENDPOINT = "https://www.wikidata.org/w/api.php"
_USER_AGENT = "GEO_PULSE/1.0 (+https://massaraksh.tech)"
_ENTITY = re.compile(r"Q[1-9][0-9]{0,15}\Z")
_MEDIA_TYPES = frozenset({"Q11032", "Q192283"})  # newspaper; news agency
_MAX_CALLS = 4
_MAX_BODY = 1_000_000
_MAX_PUBLISHER_HITS = 20
_LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".internal", ".home", ".onion", ".invalid")


class DiscoveryUnavailable(Exception):
    """Content-free retry classification for a bounded discovery attempt."""

    def __init__(self, reason: str):
        if reason not in {"limited", "rate_limited", "unavailable", "ambiguous_country"}:
            raise ValueError("invalid discovery reason")
        self.reason = reason
        super().__init__(reason)


class _Requester:
    def __init__(self, client: httpx.Client, deadline: float):
        self.client = client
        self.deadline = deadline
        self.calls = 0

    def get(self, **params):
        if self.calls >= _MAX_CALLS:
            raise DiscoveryUnavailable("limited")
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise DiscoveryUnavailable("limited")
        self.calls += 1
        params = {"format": "json", "formatversion": "2", "maxlag": "5", **params}
        try:
            timeout = min(4.0, remaining)
            # Build independently of client defaults: caller-supplied clients
            # must not append parameters, credentials, or cookies to Wikidata.
            request = httpx.Request(
                "GET", _ENDPOINT, params=params,
                headers={"User-Agent": _USER_AGENT},
                extensions={"timeout": {"connect": timeout, "read": timeout,
                                        "write": timeout, "pool": timeout}},
            )
            response = self.client.send(request, stream=True, follow_redirects=False, auth=None)
            try:
                if response.status_code in (429, 503) or response.headers.get("Retry-After"):
                    raise DiscoveryUnavailable("rate_limited")
                if response.status_code != 200:
                    raise DiscoveryUnavailable("unavailable")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=8192):
                    body.extend(chunk)
                    if len(body) > _MAX_BODY or monotonic() >= self.deadline:
                        raise DiscoveryUnavailable("limited")
            finally:
                response.close()
        except DiscoveryUnavailable:
            raise
        except httpx.TimeoutException as exc:
            raise DiscoveryUnavailable("limited") from exc
        except httpx.HTTPError as exc:
            raise DiscoveryUnavailable("unavailable") from exc
        try:
            data = json.loads(body)
        except (UnicodeDecodeError, ValueError) as exc:
            raise DiscoveryUnavailable("unavailable") from exc
        if not isinstance(data, dict):
            raise DiscoveryUnavailable("unavailable")
        error = data.get("error")
        if error:
            code = error.get("code", "") if isinstance(error, dict) else ""
            raise DiscoveryUnavailable("rate_limited" if code in ("maxlag", "ratelimited")
                                       else "unavailable")
        return data


def _search_ids(requester, query: str, limit: int) -> list[str]:
    data = requester.get(action="query", list="search", srsearch=query,
                         srlimit=str(limit), srnamespace="0")
    search = data.get("query", {}).get("search") if isinstance(data.get("query"), dict) else None
    if not isinstance(search, list) or len(search) > limit:
        raise DiscoveryUnavailable("unavailable")
    result = []
    for item in search:
        qid = item.get("title") if isinstance(item, dict) else None
        if isinstance(qid, str) and _ENTITY.fullmatch(qid) and qid not in result:
            result.append(qid)
    return result


def _entities(requester, ids: list[str]) -> dict:
    if not ids or len(ids) > _MAX_PUBLISHER_HITS:
        raise DiscoveryUnavailable("limited")
    data = requester.get(action="wbgetentities", ids="|".join(ids),
                         props="claims|labels|info", languages="en|ru", redirects="no")
    entities = data.get("entities")
    if not isinstance(entities, dict):
        raise DiscoveryUnavailable("unavailable")
    return entities


def _claim_values(item, prop):
    claims = item.get("claims") if isinstance(item, dict) else None
    entries = claims.get(prop, []) if isinstance(claims, dict) else []
    if not isinstance(entries, list):
        return []
    values = []
    for statement in entries:
        if not isinstance(statement, dict) or statement.get("rank") == "deprecated":
            continue
        snak = statement.get("mainsnak")
        if not isinstance(snak, dict) or snak.get("snaktype") != "value":
            continue
        data = snak.get("datavalue")
        if isinstance(data, dict):
            values.append(data.get("value"))
    return values


def _claim_entities(item, prop):
    return {value.get("id") for value in _claim_values(item, prop)
            if isinstance(value, dict) and isinstance(value.get("id"), str)
            and _ENTITY.fullmatch(value["id"])}


def _website(value):
    if safe_public_url(value) is None:
        return None
    try:
        host = urlsplit(value).hostname
        if not host:
            return None
        canonical = host.rstrip(".").casefold()
        if canonical == "localhost" or canonical.endswith(_LOCAL_SUFFIXES) or "." not in canonical:
            return None
        try:
            ip = ipaddress.ip_address(canonical)
        except ValueError:
            return value
        return value if ip.is_global else None
    except ValueError:
        return None


def _lead(qid: str, item: dict, country_qid: str, code: str):
    if not isinstance(item, dict) or item.get("id") != qid or item.get("type") != "item":
        return None
    if country_qid not in _claim_entities(item, "P17"):
        return None
    if not (_MEDIA_TYPES & _claim_entities(item, "P31")):
        return None
    labels = item.get("labels")
    if not isinstance(labels, dict):
        return None
    label = labels.get("en") or labels.get("ru")
    name = label.get("value") if isinstance(label, dict) else None
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        return None
    website = next((safe for value in _claim_values(item, "P856")
                    if (safe := _website(value)) is not None), None)
    if website is None:
        return None
    result = {"provider": "wikidata", "entity_id": qid, "name": name,
              "website": website, "provenance_url": f"https://www.wikidata.org/wiki/{qid}",
              "country_entity": country_qid, "claimed_country": code}
    revision = item.get("lastrevid")
    if type(revision) is int and revision > 0:
        result["revision"] = revision
    return result


def discover_publishers(code: str, *, client: httpx.Client | None = None,
                        max_leads: int = 5, timeout_seconds: float = 15) -> list[dict]:
    """Discover at most five unverified publisher entities for one foreign area.

    Four sequential requests maximum: country search and claims, then one
    combined media search and claims. Failure has a fixed non-sensitive reason.
    """
    if (not isinstance(code, str) or code not in MONITORING_COUNTRIES
            or type(max_leads) is not int or not 1 <= max_leads <= 5
            or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 30):
        raise ValueError("invalid discovery bounds or country")
    deadline = monotonic() + timeout_seconds
    owned = client is None
    if owned:
        client = httpx.Client(follow_redirects=False, trust_env=False)
    try:
        requester = _Requester(client, deadline)
        countries = _search_ids(requester, f"haswbstatement:P297={code}", 3)
        if not countries:
            raise DiscoveryUnavailable("ambiguous_country")
        items = _entities(requester, countries)
        verified = [qid for qid in countries
                    if isinstance(items.get(qid), dict)
                    and items[qid].get("id") == qid
                    and code in _claim_values(items[qid], "P297")]
        if len(verified) != 1:
            raise DiscoveryUnavailable("ambiguous_country")
        country_qid = verified[0]
        query = (f"haswbstatement:P17={country_qid} "
                 "haswbstatement:P31=Q11032|P31=Q192283")
        publisher_ids = _search_ids(requester, query, _MAX_PUBLISHER_HITS)
        if not publisher_ids:
            return []
        publishers = _entities(requester, publisher_ids)
        leads = []
        for qid in publisher_ids:
            lead = _lead(qid, publishers.get(qid), country_qid, code)
            if lead:
                leads.append(lead)
                if len(leads) >= max_leads:
                    break
        return leads
    finally:
        if owned:
            client.close()
