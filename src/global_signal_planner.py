"""Pure, conservative nomination of source-bound global signal contexts.

Lexical matches are retrieval suggestions, not assertions of a shared event,
causation, corroboration, or a Russian opportunity. No I/O or model calls.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import timedelta
import re

from src.early_signals import source_key
from src.monitoring_registry import MONITORING_COUNTRIES
from src.signal_hypotheses import MAX_PROMPT_BYTES, prepare_prompt
from src.signal_workbench import article_snapshot, instant, request_hash


MAX_NOMINATIONS = 80
MAX_CONTEXT_ARTICLES = 6
_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)
_GENERIC = {
    "about", "across", "after", "again", "agreed", "announced", "approved",
    "begins", "between", "change", "changes", "company", "council", "country",
    "decision", "development", "discussed", "education", "energy",
    "government", "industry", "institute", "infrastructure",
    "international", "local", "meeting", "minister", "ministry", "modern",
    "national", "official", "officials", "policy", "program", "programme",
    "project", "public", "refinery", "region", "regional", "report", "reported",
    "russia", "russian", "school", "schools", "service",
    "services", "state", "statement", "strategic", "training", "university",
    "visit", "vocational", "works", "approved", "proposed", "launched",
    "новости", "проект", "регион", "россия", "российский",
    "министерство", "программа", "образование", "решение",
}
_COUNTRY_NAMES = {word.casefold() for entry in MONITORING_COUNTRIES.values()
                  for field in ("name_en", "name_ru")
                  for word in _WORDS.findall(entry[field])}
# Covers common English adjectival forms (Serbia -> Serbian, Ethiopia ->
# Ethiopian) without a hand-maintained shortlist of favored countries.
_COUNTRY_NAMES.update(word + "n" for word in tuple(_COUNTRY_NAMES) if word.endswith("a"))


def _tokens(title):
    result = set()
    for match in _WORDS.finditer(title):
        raw = match.group()
        word = raw.casefold()
        if word in _GENERIC or word in _COUNTRY_NAMES:
            continue
        if len(word) >= 6 or (3 <= len(word) <= 8 and raw.isupper()):
            result.add(word)
    return result


def _link_tokens(left, right):
    common = _tokens(left) & _tokens(right)
    # One named acronym can identify a project. Otherwise require two specific
    # title terms; a country, ministry, policy, or sector alone cannot link news.
    acronyms = {word for word in common if len(word) <= 8 and word.upper() in left and word.upper() in right}
    return sorted(common) if len(common) >= 2 or acronyms else []


def _validated(records, latest):
    result = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("article"), dict):
            raise ValueError("invalid screening record")
        article = record["article"]
        if record.get("source_key") != source_key(article):
            raise ValueError("screening source changed")
        if record.get("snapshot_hash") != request_hash(article):
            raise ValueError("screening snapshot changed")
        if article["id"] in latest and source_key(latest[article["id"]]) != record["source_key"]:
            continue  # A corrected source requires a new screening.
        classification = record.get("classification")
        if not isinstance(classification, dict) or classification.get("status") != "needs_review":
            continue
        if classification.get("signal") not in ("change", "uncertain"):
            continue
        if classification.get("mechanism") in (None, "unknown", "other"):
            continue
        published, collected = instant(article["published_at"]), instant(article["collected_at"])
        if collected < published:
            raise ValueError("article collected before publication")
        result.append((record, published, collected))
    return result


def _fair_order(leads, defer_keys):
    """Round robin event countries, exhausting unseen before queued versions."""
    ranked = sorted(leads, key=lambda item: (
        item[0]["classification"]["signal"] != "change",
        item[0]["classification"]["stage"] != "decision",
        -item[1].timestamp(), item[0]["article"]["id"], item[0]["source_key"]))
    ordered = []
    for known in (False, True):
        by_country = defaultdict(deque)
        for item in ranked:
            record = item[0]
            if (record["source_key"] in defer_keys) != known:
                continue
            code = record["classification"].get("country")
            by_country[code if code in MONITORING_COUNTRIES else "unknown"].append(item)
        while by_country:
            for code in sorted(tuple(by_country)):
                ordered.append(by_country[code].popleft())
                if not by_country[code]:
                    del by_country[code]
    return ordered


def plan_candidates(screenings, articles=None, *, as_of, limit=MAX_NOMINATIONS,
                    defer_keys=None):
    """Return at most 80 provisional leads with optional, zero-cost contexts.

    ``screenings`` are persisted records with source/snapshot hashes. ``articles``
    may supply current source rows: if an article changed since screening, its
    old decision is skipped. Geography comes exclusively from the screening.
    """
    now = instant(as_of)
    if (not isinstance(screenings, list) or articles is not None and not isinstance(articles, list)
            or type(limit) is not int or not 0 <= limit <= MAX_NOMINATIONS
            or defer_keys is not None and (not isinstance(defer_keys, set)
                                           or any(not isinstance(key, str) for key in defer_keys))):
        raise ValueError("invalid planner input")
    defer_keys = defer_keys or set()
    latest = {}
    for article in articles or []:
        if not isinstance(article, dict) or type(article.get("id")) is not int:
            raise ValueError("invalid current article")
        latest[article["id"]] = article
    valid = [(record, published) for record, published, collected in _validated(screenings, latest)
             if now-timedelta(days=90) <= published <= collected <= now]
    leads = [(record, published) for record, published in valid
             if published >= now-timedelta(days=7)]
    leads = _fair_order(leads, defer_keys)
    result = []
    for lead, _ in leads[:limit]:
        label = lead["classification"]
        event_country = label.get("country")
        anchor = article_snapshot(lead["article"], screened=True)
        item = {"source_key": lead["source_key"], "country_code": event_country,
                "anchor": anchor, "status": "needs_context", "context": None,
                "retrieval_links": []}
        if event_country not in MONITORING_COUNTRIES:
            item["status"] = "needs_geography"
            result.append(item)
            continue
        # A malformed anchor cannot yield a usable writer prompt; surface the
        # unresolved lead rather than silently repairing screened evidence.
        try:
            prepare_prompt([anchor], as_of=now)
        except ValueError:
            result.append(item)
            continue
        related = []
        for other, published in valid:
            if other["source_key"] == lead["source_key"] or other["article"]["id"] == anchor["id"]:
                continue
            other_label = other["classification"]
            if (other_label.get("country") != event_country
                    or other_label.get("mechanism") != label["mechanism"]):
                continue
            shared = _link_tokens(anchor["title"], other["article"]["title"])
            if shared:
                related.append((other, published, shared))
        related.sort(key=lambda row: (-len(row[2]), -row[1].timestamp(), row[0]["article"]["id"]))
        evidence, ids, urls = [anchor], {anchor["id"]}, {anchor["url"]}
        for other, _, shared in related:
            if len(evidence) >= MAX_CONTEXT_ARTICLES:
                break
            snap = article_snapshot(other["article"], screened=True)
            if snap["id"] in ids or snap["url"] in urls:
                continue
            try:
                prompt = prepare_prompt(evidence + [snap], as_of=now)
            except ValueError:
                continue
            if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
                continue
            evidence.append(snap)
            ids.add(snap["id"])
            urls.add(snap["url"])
            item["retrieval_links"].append({"article_id": snap["id"], "relation": "retrieval_only",
                                            "shared_title_tokens": shared})
        if len(evidence) >= 2:
            item["status"] = "context_ready"
            item["context"] = {
                "as_of": now.isoformat(), "articles": evidence, "screening": label,
                "prompt": prepare_prompt(evidence, as_of=now),
                "note": "Automatic lexical retrieval only; event identity, independence and any Russia link need review.",
            }
        result.append(item)
    return result
