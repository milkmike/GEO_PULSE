# National Source Expansion Wave 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe candidate-validation pipeline, publisher-domain coverage reporting, and a 17-feed RSS canary for nine currently uncovered countries without changing or deleting historical analytical data.

**Architecture:** Unpromoted publishers live in a staging YAML file that `config.load_sources()` never reads. A pure validation module and thin CLI enforce metadata, duplicate-domain, freshness, and publisher-domain gates; passing entries are then copied into the loaded world catalog. Existing health APIs gain a distinct-publisher coverage view, while a read-only canary audit script records production evidence and protected row counts.

**Tech Stack:** Python 3.11, PyYAML 6, httpx 0.28, feedparser 6, SQLAlchemy 2, FastAPI, pytest 8, PostgreSQL/TimescaleDB.

## Global Constraints

- Do not delete or recompute existing articles, analyses, temperature rows, signals, briefs, stories, embeddings, or publisher-domain history.
- Never infer publisher country from ccTLD or Google News locale; use curated canonical publisher domains and aliases.
- Google News, AllAfrica, generic aggregators, Russian mirrors, and downstream wire republications do not count as direct national publisher diversity.
- Candidate YAML must remain outside `config.load_sources()` until an entry passes validation and is deliberately promoted.
- Wave 1 contains exactly 17 direct RSS/Atom feeds across Albania, Cyprus, Denmark, Ireland, Montenegro, North Macedonia, Portugal, Slovenia, and Singapore.
- No new Python dependency or database migration is allowed for Wave 1.
- Deployment may rebuild only `api` and `collector`; PostgreSQL and Redis must not be restarted or replaced.
- Every implementation task follows red-green-refactor and ends in a focused commit.

---

## Scope Boundary

This plan delivers the first independently useful release: staging, validation,
coverage reporting, the 17-feed RSS canary, and its production verdict. Wave 2
publisher balancing and Wave 3 HTML adapters remain separate execution plans
after the 12-hour canary establishes which validation and collector assumptions
hold in production. The candidate registry and coverage API created here are the
shared foundation for both later plans.

---

## File Structure

- `src/collectors/source_candidates.yaml` — research and promotion state for sources that are not automatically loaded.
- `src/collectors/source_candidates.py` — typed staging loader, catalog conversion, metadata validation, feed validation, and report rendering.
- `scripts/validate_source_candidates.py` — network-capable CLI around the pure validation module.
- `scripts/audit_source_wave.py` — read-only production snapshot and canary evaluator.
- `src/config.py` — staging file path constant only; `load_sources()` remains limited to the three production catalogs.
- `src/engine/health.py` — distinct-publisher country coverage calculation.
- `src/api/routes/world.py` — `GET /api/v2/health/source-coverage` route.
- `src/collectors/sources_world.yaml` — promoted Wave 1 feeds only after validation passes.
- `tests/test_source_candidates.py` — staging isolation, schema, metadata, feed, and promotion contract tests.
- `tests/test_source_health.py` — publisher-family coverage and target-state tests.
- `tests/test_source_wave_audit.py` — canary evaluation and protected-count tests.
- `docs/release/national-source-wave1.md` — exact release, observation, and rollback commands.

---

### Task 1: Isolated candidate registry and typed loader

**Files:**
- Create: `src/collectors/source_candidates.yaml`
- Create: `src/collectors/source_candidates.py`
- Modify: `src/config.py:22-27`
- Create: `tests/test_source_candidates.py`

**Interfaces:**
- Produces: `SourceCandidate`, `load_source_candidates(path: Path) -> list[SourceCandidate]`, and `candidate_to_catalog_source(candidate: SourceCandidate) -> dict`.
- Consumes: `src.countries.COUNTRIES` and `src.collectors.publisher_attribution.normalize_publisher_domain`.

- [ ] **Step 1: Write failing loader and isolation tests**

```python
# tests/test_source_candidates.py
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
```

- [ ] **Step 2: Run the tests and verify the missing module/path failure**

Run: `.venv/bin/pytest tests/test_source_candidates.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'src.collectors.source_candidates'` or missing `SOURCE_CANDIDATES_PATH`.

- [ ] **Step 3: Add the staging path without loading it**

```python
# src/config.py, next to the three existing catalog paths
SOURCE_CANDIDATES_PATH = BASE_DIR / "src" / "collectors" / "source_candidates.yaml"
```

Do not add `SOURCE_CANDIDATES_PATH` to the tuple iterated by `load_sources()`.

- [ ] **Step 4: Implement the typed loader and catalog conversion**

```python
# src/collectors/source_candidates.py
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
```

- [ ] **Step 5: Add the exact Wave 1 staging data**

Use this document shape for all records:

```yaml
version: 1
candidates:
  - {name: RTSH, country_code: AL, type: rss, feed_url: "https://rtsh.al/feed/", publisher_url: "https://rtsh.al", canonical_domain: "rtsh.al", domain_aliases: [], language: sq, tier: official, state_affiliated: true, propaganda_risk: medium, syndication_risk: medium, ownership_evidence_url: "https://rtsh.al", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: Reporter.al, country_code: AL, type: rss, feed_url: "https://reporter.al/feed/", publisher_url: "https://reporter.al", canonical_domain: "reporter.al", domain_aliases: [], language: sq, tier: independent, state_affiliated: false, propaganda_risk: low, syndication_risk: low, ownership_evidence_url: "https://reporter.al", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: Philenews, country_code: CY, type: rss, feed_url: "https://www.philenews.com/feed/", publisher_url: "https://www.philenews.com", canonical_domain: "philenews.com", domain_aliases: [], language: el, tier: mainstream, state_affiliated: false, propaganda_risk: low, syndication_risk: medium, ownership_evidence_url: "https://www.philenews.com", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: Politis, country_code: CY, type: rss, feed_url: "https://www.politis.com.cy/feed/", publisher_url: "https://www.politis.com.cy", canonical_domain: "politis.com.cy", domain_aliases: [], language: el, tier: independent, state_affiliated: false, propaganda_risk: low, syndication_risk: medium, ownership_evidence_url: "https://www.politis.com.cy", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: Politiken, country_code: DK, type: rss, feed_url: "https://politiken.dk/rss/senestenyt.rss", publisher_url: "https://politiken.dk", canonical_domain: "politiken.dk", domain_aliases: [], language: da, tier: mainstream, state_affiliated: false, propaganda_risk: low, syndication_risk: medium, ownership_evidence_url: "https://politiken.dk", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: Information, country_code: DK, type: rss, feed_url: "https://www.information.dk/feed", publisher_url: "https://www.information.dk", canonical_domain: "information.dk", domain_aliases: [], language: da, tier: independent, state_affiliated: false, propaganda_risk: low, syndication_risk: low, ownership_evidence_url: "https://www.information.dk", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: The Irish Times, country_code: IE, type: rss, feed_url: "https://www.irishtimes.com/arc/outboundfeeds/rss/?outputType=xml", publisher_url: "https://www.irishtimes.com", canonical_domain: "irishtimes.com", domain_aliases: [], language: en, tier: mainstream, state_affiliated: false, propaganda_risk: low, syndication_risk: medium, ownership_evidence_url: "https://www.irishtimes.com", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: TheJournal.ie, country_code: IE, type: rss, feed_url: "https://www.thejournal.ie/feed/", publisher_url: "https://www.thejournal.ie", canonical_domain: "thejournal.ie", domain_aliases: [], language: en, tier: independent, state_affiliated: false, propaganda_risk: low, syndication_risk: medium, ownership_evidence_url: "https://www.thejournal.ie", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: RTCG, country_code: ME, type: rss, feed_url: "https://rtcg.me/rss.html", publisher_url: "https://rtcg.me", canonical_domain: "rtcg.me", domain_aliases: [], language: sr, tier: official, state_affiliated: true, propaganda_risk: medium, syndication_risk: medium, ownership_evidence_url: "https://rtcg.me", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: Vijesti, country_code: ME, type: rss, feed_url: "https://www.vijesti.me/rss", publisher_url: "https://www.vijesti.me", canonical_domain: "vijesti.me", domain_aliases: [], language: sr, tier: mainstream, state_affiliated: false, propaganda_risk: low, syndication_risk: medium, ownership_evidence_url: "https://www.vijesti.me", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: MRT, country_code: MK, type: rss, feed_url: "https://www.mrt.com.mk/rss.xml", publisher_url: "https://www.mrt.com.mk", canonical_domain: "mrt.com.mk", domain_aliases: [], language: mk, tier: official, state_affiliated: true, propaganda_risk: medium, syndication_risk: medium, ownership_evidence_url: "https://www.mrt.com.mk", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: Meta.mk, country_code: MK, type: rss, feed_url: "https://meta.mk/feed/", publisher_url: "https://meta.mk", canonical_domain: "meta.mk", domain_aliases: [], language: mk, tier: independent, state_affiliated: false, propaganda_risk: low, syndication_risk: medium, ownership_evidence_url: "https://meta.mk", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: RTP Noticias, country_code: PT, type: rss, feed_url: "https://www.rtp.pt/noticias/rss", publisher_url: "https://www.rtp.pt/noticias", canonical_domain: "rtp.pt", domain_aliases: [], language: pt, tier: official, state_affiliated: true, propaganda_risk: medium, syndication_risk: medium, ownership_evidence_url: "https://www.rtp.pt/noticias", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: Observador, country_code: PT, type: rss, feed_url: "https://observador.pt/feed/", publisher_url: "https://observador.pt", canonical_domain: "observador.pt", domain_aliases: [], language: pt, tier: independent, state_affiliated: false, propaganda_risk: low, syndication_risk: medium, ownership_evidence_url: "https://observador.pt", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: RTV Slovenija, country_code: SI, type: rss, feed_url: "https://www.rtvslo.si/feeds/01.xml", publisher_url: "https://www.rtvslo.si", canonical_domain: "rtvslo.si", domain_aliases: [], language: sl, tier: official, state_affiliated: true, propaganda_risk: medium, syndication_risk: medium, ownership_evidence_url: "https://www.rtvslo.si", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: N1 Slovenija, country_code: SI, type: rss, feed_url: "https://n1info.si/feed/", publisher_url: "https://n1info.si", canonical_domain: "n1info.si", domain_aliases: [], language: sl, tier: mainstream, state_affiliated: false, propaganda_risk: low, syndication_risk: medium, ownership_evidence_url: "https://n1info.si", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
  - {name: CNA Singapore, country_code: SG, type: rss, feed_url: "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=10416", publisher_url: "https://www.channelnewsasia.com/singapore", canonical_domain: "channelnewsasia.com", domain_aliases: [], language: en, tier: official, state_affiliated: true, propaganda_risk: medium, syndication_risk: medium, ownership_evidence_url: "https://www.channelnewsasia.com", research_date: "2026-07-17", status: researched, wave: "2026-07-17-rss-1"}
```

- [ ] **Step 6: Run the loader tests**

Run: `.venv/bin/pytest tests/test_source_candidates.py -v`

Expected: both tests pass.

- [ ] **Step 7: Commit the isolated registry**

```bash
git add src/config.py src/collectors/source_candidates.py src/collectors/source_candidates.yaml tests/test_source_candidates.py
git commit -m "feat: add isolated national source candidate registry"
```

---

### Task 2: Deterministic metadata and feed validation

**Files:**
- Modify: `src/collectors/source_candidates.py`
- Modify: `tests/test_source_candidates.py`

**Interfaces:**
- Consumes: `SourceCandidate`, production config from `config.load_sources()`, `normalize_publisher_domain()`, and `is_aggregator_domain()`.
- Produces: `CandidateValidation`, `configured_publisher_sources(config: dict) -> dict[str, set[tuple[str, str]]]`, `validate_candidate_metadata(...)`, and `validate_feed_document(...)`.

- [ ] **Step 1: Add failing metadata validation tests**

```python
from dataclasses import replace

from src.collectors.source_candidates import (
    configured_publisher_sources,
    validate_candidate_metadata,
)


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
```

- [ ] **Step 2: Add failing feed validation tests with deterministic XML**

```python
from datetime import datetime, timezone

from src.collectors.source_candidates import validate_feed_document

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)


def _feed(*links: str, published="Thu, 16 Jul 2026 10:00:00 GMT") -> bytes:
    items = "".join(
        f"<item><title>Story {i}</title><link>{link}</link>"
        f"<pubDate>{published}</pubDate></item>"
        for i, link in enumerate(links)
    )
    return f"<rss version='2.0'><channel><title>News</title>{items}</channel></rss>".encode()


def test_feed_validation_accepts_valid_xml_even_with_html_content_type():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    result = validate_feed_document(
        candidate,
        _feed("https://rtsh.al/a", "https://rtsh.al/b", "https://rtsh.al/c"),
        now=NOW,
    )
    assert result.ok is True
    assert result.item_count == 3
    assert result.domain_ratio == 1.0


def test_feed_validation_rejects_stale_and_off_domain_entries():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    stale = validate_feed_document(
        candidate,
        _feed(
            "https://rtsh.al/a", "https://rtsh.al/b", "https://rtsh.al/c",
            published="Thu, 01 Jan 2026 10:00:00 GMT",
        ),
        now=NOW,
    )
    assert "stale_feed" in stale.reasons

    foreign = validate_feed_document(
        candidate,
        _feed("https://rtsh.al/a", "https://example.com/b", "https://example.com/c"),
        now=NOW,
    )
    assert "publisher_domain_ratio_below_0_8" in foreign.reasons
```

- [ ] **Step 3: Run focused tests and verify missing-symbol failures**

Run: `.venv/bin/pytest tests/test_source_candidates.py -v`

Expected: new tests fail because validation symbols do not exist.

- [ ] **Step 4: Implement metadata and feed validators**

```python
# src/collectors/source_candidates.py
from dataclasses import dataclass
from datetime import datetime, timezone
from time import mktime
from urllib.parse import urlparse

import feedparser

from src.collectors.publisher_attribution import (
    feed_mode,
    is_aggregator_domain,
    normalize_publisher_domain,
)
from src.countries import COUNTRIES

ALLOWED_TIERS = {
    "official", "mainstream", "independent", "domestic_opposition", "analytics",
}
ALLOWED_RISKS = {"low", "medium", "high"}
ALLOWED_STATUSES = {"researched", "promoted", "rejected", "adapter_required"}


@dataclass(frozen=True)
class CandidateValidation:
    name: str
    country_code: str
    ok: bool
    reasons: tuple[str, ...]
    item_count: int = 0
    newest_age_days: float | None = None
    domain_ratio: float | None = None


def _domain_matches(actual: str | None, expected: set[str]) -> bool:
    return bool(actual) and any(
        actual == domain or actual.endswith(f".{domain}") for domain in expected
    )


def configured_publisher_sources(config: dict) -> dict[str, set[tuple[str, str]]]:
    publishers: dict[str, set[tuple[str, str]]] = {}
    for country_code, country in config.get("countries", {}).items():
        for source in country.get("sources", []):
            source_config = source.get("config") or {}
            if feed_mode(source.get("url"), source_config) == "publisher_discovery":
                continue
            domain = normalize_publisher_domain(
                source_config.get("publisher_domain") or source.get("url")
            )
            if domain and not is_aggregator_domain(domain):
                publishers.setdefault(domain, set()).add(
                    (str(country_code).upper(), source["url"])
                )
    return publishers


def validate_candidate_metadata(
    candidate: SourceCandidate,
    *,
    configured_publishers: dict[str, set[tuple[str, str]]],
) -> CandidateValidation:
    reasons = []
    canonical = normalize_publisher_domain(candidate.canonical_domain)
    feed_domain = normalize_publisher_domain(candidate.feed_url)
    if candidate.country_code not in COUNTRIES:
        reasons.append("unknown_country")
    if candidate.source_type != "rss":
        reasons.append("wave1_requires_rss")
    if not canonical or is_aggregator_domain(canonical) or is_aggregator_domain(feed_domain):
        reasons.append("aggregator_domain")
    existing = configured_publishers.get(canonical or "", set())
    promoted_self = {
        (candidate.country_code, candidate.feed_url)
    } if candidate.status == "promoted" else set()
    if existing and existing != promoted_self:
        reasons.append("duplicate_publisher_domain")
    if candidate.tier not in ALLOWED_TIERS:
        reasons.append("invalid_tier")
    if candidate.propaganda_risk not in ALLOWED_RISKS:
        reasons.append("invalid_propaganda_risk")
    if candidate.syndication_risk not in ALLOWED_RISKS:
        reasons.append("invalid_syndication_risk")
    if candidate.status not in ALLOWED_STATUSES:
        reasons.append("invalid_status")
    if urlparse(candidate.ownership_evidence_url).scheme != "https":
        reasons.append("ownership_evidence_must_be_https")
    return CandidateValidation(
        name=candidate.name,
        country_code=candidate.country_code,
        ok=not reasons,
        reasons=tuple(reasons),
    )


def validate_feed_document(
    candidate: SourceCandidate,
    body: bytes,
    *,
    now: datetime | None = None,
) -> CandidateValidation:
    now = now or datetime.now(timezone.utc)
    parsed = feedparser.parse(body)
    reasons = []
    entries = list(parsed.entries)
    if parsed.bozo and not entries:
        reasons.append("malformed_feed")
    if len(entries) < 3:
        reasons.append("fewer_than_3_entries")

    expected = {candidate.canonical_domain, *candidate.domain_aliases}
    domains = [normalize_publisher_domain(entry.get("link")) for entry in entries]
    matched = sum(_domain_matches(domain, expected) for domain in domains)
    ratio = matched / len(domains) if domains else 0.0
    if ratio < 0.8:
        reasons.append("publisher_domain_ratio_below_0_8")

    dated = []
    for entry in entries:
        stamp = entry.get("published_parsed") or entry.get("updated_parsed")
        if stamp:
            dated.append(datetime.fromtimestamp(mktime(stamp), tz=timezone.utc))
    newest_age = None
    if not dated:
        reasons.append("missing_entry_dates")
    else:
        newest_age = (now - max(dated)).total_seconds() / 86400
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
```

- [ ] **Step 5: Run candidate unit tests**

Run: `.venv/bin/pytest tests/test_source_candidates.py -v`

Expected: all tests pass.

- [ ] **Step 6: Run existing RSS and attribution regression tests**

Run: `.venv/bin/pytest tests/test_rss.py tests/test_publisher_attribution.py tests/test_source_sync.py -v`

Expected: all tests pass with no changed Google News attribution behavior.

- [ ] **Step 7: Commit validation logic**

```bash
git add src/collectors/source_candidates.py tests/test_source_candidates.py
git commit -m "feat: validate national source candidates"
```

---

### Task 3: Validation CLI and reviewable reports

**Files:**
- Create: `scripts/validate_source_candidates.py`
- Modify: `src/collectors/source_candidates.py`
- Modify: `tests/test_source_candidates.py`

**Interfaces:**
- Consumes: `validate_candidate_metadata()` and `validate_feed_document()`.
- Produces: `fetch_and_validate_candidate(candidate, client, now)`, `validation_summary(results)`, `render_validation_markdown(results)`, and CLI exit code `0` only when every candidate passes.

- [ ] **Step 1: Add failing fetch/report tests**

```python
from types import SimpleNamespace

from src.collectors.source_candidates import (
    fetch_and_validate_candidate,
    render_validation_markdown,
    validation_summary,
)


class StubClient:
    def __init__(self, body: bytes, status_code: int = 200):
        self.response = SimpleNamespace(content=body, status_code=status_code)

    def get(self, *args, **kwargs):
        return self.response


def test_fetch_and_report_are_machine_and_human_readable():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    client = StubClient(_feed(
        "https://rtsh.al/a", "https://rtsh.al/b", "https://rtsh.al/c",
    ))
    result = fetch_and_validate_candidate(candidate, client=client, now=NOW)
    assert result.ok is True
    assert validation_summary([result]) == {"total": 1, "passed": 1, "failed": 0}
    markdown = render_validation_markdown([result])
    assert "| AL | RTSH | PASS |" in markdown
```

- [ ] **Step 2: Run the focused test and verify missing-symbol failures**

Run: `.venv/bin/pytest tests/test_source_candidates.py::test_fetch_and_report_are_machine_and_human_readable -v`

Expected: FAIL because the fetch/report functions do not exist.

- [ ] **Step 3: Implement fetch and report helpers**

```python
# append to src/collectors/source_candidates.py
import httpx


def fetch_and_validate_candidate(
    candidate: SourceCandidate,
    *,
    client: httpx.Client,
    now: datetime | None = None,
) -> CandidateValidation:
    try:
        response = client.get(
            candidate.feed_url,
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "GEO-PULSE source validator/1.0"},
        )
    except httpx.HTTPError as exc:
        return CandidateValidation(
            candidate.name, candidate.country_code, False,
            (f"fetch_error:{type(exc).__name__}",),
        )
    if response.status_code != 200:
        return CandidateValidation(
            candidate.name, candidate.country_code, False,
            (f"http_status:{response.status_code}",),
        )
    return validate_feed_document(candidate, response.content, now=now)


def validation_summary(results: list[CandidateValidation]) -> dict:
    passed = sum(item.ok for item in results)
    return {"total": len(results), "passed": passed, "failed": len(results) - passed}


def render_validation_markdown(results: list[CandidateValidation]) -> str:
    lines = [
        "# Source candidate validation",
        "",
        "| Country | Publisher | Result | Items | Newest age | Domain ratio | Reasons |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for item in results:
        lines.append(
            f"| {item.country_code} | {item.name} | {'PASS' if item.ok else 'FAIL'} | "
            f"{item.item_count} | {item.newest_age_days} | {item.domain_ratio} | "
            f"{', '.join(item.reasons)} |"
        )
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Implement the thin CLI**

```python
#!/usr/bin/env python3
# scripts/validate_source_candidates.py
import argparse
import json
from pathlib import Path

import httpx

from src import config
from src.collectors.source_candidates import (
    configured_publisher_sources,
    fetch_and_validate_candidate,
    load_source_candidates,
    render_validation_markdown,
    validate_candidate_metadata,
    validation_summary,
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-file", type=Path, default=config.SOURCE_CANDIDATES_PATH)
    parser.add_argument("--catalog-only", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    candidates = load_source_candidates(args.candidate_file)
    configured = configured_publisher_sources(config.load_sources())
    metadata = [
        validate_candidate_metadata(item, configured_publishers=configured)
        for item in candidates
    ]
    if args.catalog_only or any(not item.ok for item in metadata):
        results = metadata
    else:
        with httpx.Client() as client:
            results = [
                fetch_and_validate_candidate(item, client=client)
                for item in candidates
            ]

    summary = validation_summary(results)
    output = (
        json.dumps({"summary": summary, "results": [item.__dict__ for item in results]},
                   ensure_ascii=False, indent=2)
        if args.json
        else render_validation_markdown(results)
    )
    if args.out:
        args.out.write_text(output)
    print(output)
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run unit tests and a metadata-only CLI smoke test**

Run: `.venv/bin/pytest tests/test_source_candidates.py -v`

Expected: all tests pass.

Run: `.venv/bin/python scripts/validate_source_candidates.py --catalog-only --json`

Expected: JSON summary equals `{"total": 17, "passed": 17, "failed": 0}`.

- [ ] **Step 6: Commit the CLI**

```bash
git add src/collectors/source_candidates.py scripts/validate_source_candidates.py tests/test_source_candidates.py
git commit -m "feat: add source candidate validation CLI"
```

---

### Task 4: Distinct-publisher coverage API

**Files:**
- Modify: `src/engine/health.py:34-120,150-209`
- Modify: `src/api/routes/world.py:20,1007-1019`
- Create: `tests/test_source_health.py`

**Interfaces:**
- Produces: `source_coverage(sources: list[dict] | None = None) -> dict` and `GET /api/v2/health/source-coverage`.
- Consumes: normalized domain and feed-mode helpers from `publisher_attribution.py` and source rows from `source_health()`.

- [ ] **Step 1: Write failing coverage-state tests**

```python
# tests/test_source_health.py
from src.engine.health import source_coverage


def _source(name, domain, tier, *, status="ok", discovery=False, state=False):
    return {
        "name": name,
        "country_code": "XY",
        "tier": tier,
        "type": "rss",
        "url": f"https://{domain}/feed",
        "config": {"feed_mode": "publisher_discovery"} if discovery else {},
        "state_affiliated": state,
        "last_status": status,
    }


def test_source_coverage_counts_domains_not_rows_or_discovery():
    result = source_coverage([
        _source("Official one", "official.example", "official", state=True),
        _source("Official alias", "official.example", "official", state=True),
        _source("Mainstream", "mainstream.example", "mainstream"),
        _source("Independent", "independent.example", "independent"),
        _source("Google", "news.google.com", "mainstream", discovery=True),
    ])
    country = result["countries"][0]
    assert country["configured"] == 5
    assert country["discovery"] == 1
    assert country["direct_publishers"] == 3
    assert country["working_direct_publishers"] == 3
    assert country["mix"] == {"official": 1, "mainstream": 1, "independent": 1}
    assert country["target_state"] == "balanced"


def test_source_coverage_states_are_uncovered_thin_and_baseline():
    assert source_coverage([])["summary"]["uncovered"] == 99
    thin = source_coverage([_source("One", "one.example", "mainstream")])
    assert thin["countries"][0]["target_state"] == "thin"
    baseline = source_coverage([
        _source("One", "one.example", "mainstream"),
        _source("Two", "two.example", "mainstream"),
        _source("Three", "three.example", "mainstream"),
    ])
    assert baseline["countries"][0]["target_state"] == "baseline"
```

- [ ] **Step 2: Run the tests and verify missing-symbol failures**

Run: `.venv/bin/pytest tests/test_source_health.py -v`

Expected: FAIL because `source_coverage` is not defined.

- [ ] **Step 3: Add source metadata needed by coverage calculation**

Extend the `source_health()` SQL select with `s.url, s.config, s.state_affiliated`, then add these fields to each returned dictionary:

```python
"url": r.url,
"config": r.config or {},
"state_affiliated": bool(r.state_affiliated),
```

- [ ] **Step 4: Implement distinct-publisher coverage**

```python
# src/engine/health.py
from collections import Counter, defaultdict

from src.collectors.publisher_attribution import (
    feed_mode,
    normalize_publisher_domain,
)
from src.countries import COUNTRIES, country_name_ru

INDEPENDENT_TIERS = {"independent", "domestic_opposition"}


def source_coverage(sources: list[dict] | None = None) -> dict:
    sources = source_health() if sources is None else sources
    grouped = defaultdict(list)
    for source in sources:
        grouped[str(source["country_code"]).strip().upper()].append(source)

    countries = []
    states = Counter()
    for code in sorted(COUNTRIES):
        rows = grouped.get(code, [])
        discovery = [
            row for row in rows
            if feed_mode(row.get("url"), row.get("config") or {}) == "publisher_discovery"
        ]
        direct = [row for row in rows if row not in discovery and row.get("type") in {"rss", "web"}]
        by_domain = defaultdict(list)
        for row in direct:
            cfg = row.get("config") or {}
            domain = normalize_publisher_domain(cfg.get("publisher_domain") or row.get("url"))
            if domain:
                by_domain[domain].append(row)
        working = {
            domain: family for domain, family in by_domain.items()
            if any(row.get("last_status") == "ok" for row in family)
        }
        mix = {"official": 0, "mainstream": 0, "independent": 0}
        for family in working.values():
            tiers = {row.get("tier") for row in family}
            if any(row.get("state_affiliated") for row in family) or "official" in tiers:
                mix["official"] += 1
            if "mainstream" in tiers:
                mix["mainstream"] += 1
            if tiers & INDEPENDENT_TIERS:
                mix["independent"] += 1
        count = len(working)
        state = (
            "uncovered" if count == 0 else
            "thin" if count < 3 else
            "balanced" if all(mix.values()) else
            "baseline"
        )
        states[state] += 1
        countries.append({
            "country_code": code,
            "country_name": country_name_ru(code),
            "configured": len(rows),
            "discovery": len(discovery),
            "direct_publishers": len(by_domain),
            "working_direct_publishers": count,
            "mix": mix,
            "duplicate_families": sorted(
                domain for domain, family in by_domain.items() if len(family) > 1
            ),
            "target_state": state,
        })
    return {"summary": dict(states), "countries": countries}
```

- [ ] **Step 5: Add the API route**

```python
# src/api/routes/world.py
from src.engine.health import health_summary, source_coverage, source_health


@router.get("/health/source-coverage")
def world_source_coverage():
    """Distinct direct-publisher coverage and editorial mix for all 99 countries."""
    return source_coverage()
```

- [ ] **Step 6: Run coverage and world API tests**

Run: `.venv/bin/pytest tests/test_source_health.py tests/test_world_dossier.py -v`

Expected: all tests pass.

- [ ] **Step 7: Commit coverage reporting**

```bash
git add src/engine/health.py src/api/routes/world.py tests/test_source_health.py
git commit -m "feat: report working national publisher coverage"
```

---

### Task 5: Live validation and deliberate Wave 1 promotion

**Files:**
- Modify: `src/collectors/source_candidates.yaml`
- Modify: `src/collectors/sources_world.yaml` in the nine country blocks
- Modify: `tests/test_source_candidates.py`

**Interfaces:**
- Consumes: validation CLI from Task 3 and `candidate_to_catalog_source()` from Task 1.
- Produces: 17 production-loaded source entries carrying `config.source_expansion_wave = "2026-07-17-rss-1"`.

- [ ] **Step 1: Run live validation before touching the production catalog**

Run:

```bash
.venv/bin/python scripts/validate_source_candidates.py \
  --json \
  --out /private/tmp/geopulse-source-wave1-validation.json
```

Expected: exit `0`, summary `total=17`, `passed=17`, `failed=0`.

If any candidate fails, stop this task. Keep its staging status `researched`, record the exact validation reason, and do not add that publisher to `sources_world.yaml` until the feed passes or an independently verified replacement is added to the staging registry and passes the same command.

- [ ] **Step 2: Write the failing promotion contract test**

```python
def test_promoted_wave1_catalog_contains_exactly_17_direct_publishers():
    candidates = load_source_candidates(config.SOURCE_CANDIDATES_PATH)
    promoted = [item for item in candidates if item.status == "promoted"]
    assert len(promoted) == 17

    loaded = config.load_sources()["countries"]
    by_url = {
        source["url"]: (code, source)
        for code, country in loaded.items()
        for source in country.get("sources", [])
    }
    for candidate in promoted:
        code, source = by_url[candidate.feed_url]
        assert code == candidate.country_code
        assert source["config"]["publisher_domain"] == candidate.canonical_domain
        assert source["config"]["source_expansion_wave"] == candidate.wave
        assert "news.google.com" not in source["url"]
```

- [ ] **Step 3: Run the promotion test and verify failure**

Run: `.venv/bin/pytest tests/test_source_candidates.py::test_promoted_wave1_catalog_contains_exactly_17_direct_publishers -v`

Expected: FAIL because staging statuses are still `researched` and feeds are not loaded.

- [ ] **Step 4: Promote only the 17 passing mappings**

For each candidate, append the exact mapping returned by
`candidate_to_catalog_source(candidate)` to its country `sources` list in
`sources_world.yaml`. For country keys not yet present in that file, create one
country block exactly once. Every entry must have this operational shape:

```yaml
- name: RTSH
  type: rss
  url: https://rtsh.al/feed/
  weight: 1.0
  language: sq
  tier: official
  state_affiliated: true
  propaganda_risk: medium
  config:
    publisher_domain: rtsh.al
    publisher_domain_aliases: []
    source_expansion_wave: 2026-07-17-rss-1
```

Generate and review all 17 mappings without editing the catalog automatically:

```bash
.venv/bin/python -c "from src import config; from src.collectors.source_candidates import load_source_candidates,candidate_to_catalog_source; import yaml; print(yaml.safe_dump([candidate_to_catalog_source(x) for x in load_source_candidates(config.SOURCE_CANDIDATES_PATH)], allow_unicode=True, sort_keys=False))"
```

Then change all 17 staging statuses from `researched` to `promoted`.

- [ ] **Step 5: Run promotion, attribution, and sync tests**

Run:

```bash
.venv/bin/pytest \
  tests/test_source_candidates.py \
  tests/test_publisher_attribution.py \
  tests/test_source_sync.py \
  tests/test_collect.py -v
```

Expected: all tests pass; the promotion contract sees exactly 17 direct feeds.

- [ ] **Step 6: Re-run live validation from the staging audit trail**

Run: `.venv/bin/python scripts/validate_source_candidates.py --json`

Expected: all 17 remain live and pass; changing staging status to `promoted` does not bypass any gate.

- [ ] **Step 7: Commit the Wave 1 catalog**

```bash
git add src/collectors/source_candidates.yaml src/collectors/sources_world.yaml tests/test_source_candidates.py
git commit -m "feat: promote first national RSS source wave"
```

---

### Task 6: Read-only canary audit and rollback runbook

**Files:**
- Create: `scripts/audit_source_wave.py`
- Create: `tests/test_source_wave_audit.py`
- Create: `docs/release/national-source-wave1.md`

**Interfaces:**
- Produces: `evaluate_wave(rows: list[dict], protected_counts: dict, baseline_counts: dict | None = None) -> dict` and CLI `--wave`, `--baseline`, `--json`, `--out`.
- Consumes: sources tagged with `config.source_expansion_wave`, recent articles, and protected table counts.

- [ ] **Step 1: Write failing pure canary evaluation tests**

```python
# tests/test_source_wave_audit.py
from scripts.audit_source_wave import evaluate_wave


def test_canary_requires_fresh_ok_fetch_and_safe_attribution():
    report = evaluate_wave(
        [{
            "source_id": 10,
            "name": "RTSH",
            "country_code": "AL",
            "last_status": "ok",
            "last_fetch_age_minutes": 20,
            "article_count": 4,
            "foreign_url_count": 0,
            "unsafe_geo_count": 0,
        }],
        protected_counts={"articles": 100, "analysis": 90, "temperature": 80,
                          "signals": 70, "stories": 60},
    )
    assert report["summary"] == {"total": 1, "passed": 1, "failed": 0}


def test_canary_fails_closed_on_attribution_or_protected_count_loss():
    report = evaluate_wave(
        [{
            "source_id": 10,
            "name": "Bad",
            "country_code": "AL",
            "last_status": "ok",
            "last_fetch_age_minutes": 20,
            "article_count": 2,
            "foreign_url_count": 1,
            "unsafe_geo_count": 1,
        }],
        protected_counts={"articles": 99, "analysis": 90, "temperature": 80,
                          "signals": 70, "stories": 60},
        baseline_counts={"articles": 100, "analysis": 90, "temperature": 80,
                         "signals": 70, "stories": 60},
    )
    assert report["summary"]["failed"] == 1
    assert report["protected_counts_ok"] is False
```

- [ ] **Step 2: Run tests and verify the missing script failure**

Run: `.venv/bin/pytest tests/test_source_wave_audit.py -v`

Expected: collection fails because `scripts.audit_source_wave` does not exist.

- [ ] **Step 3: Implement evaluation and read-only queries**

```python
# scripts/audit_source_wave.py
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from src.db import get_session

PROTECTED_TABLES = ("articles", "analysis", "temperature", "signals", "stories")


def evaluate_wave(rows, protected_counts, baseline_counts=None):
    evaluated = []
    for row in rows:
        reasons = []
        if row["last_status"] != "ok":
            reasons.append("last_status_not_ok")
        if row["last_fetch_age_minutes"] is None or row["last_fetch_age_minutes"] > 60:
            reasons.append("fetch_not_recent")
        if row["foreign_url_count"]:
            reasons.append("foreign_article_url")
        if row["unsafe_geo_count"]:
            reasons.append("unsafe_geo_attribution")
        evaluated.append({**row, "ok": not reasons, "reasons": reasons})
    passed = sum(item["ok"] for item in evaluated)
    protected_ok = baseline_counts is None or all(
        protected_counts[name] >= baseline_counts[name] for name in PROTECTED_TABLES
    )
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "summary": {"total": len(evaluated), "passed": passed,
                    "failed": len(evaluated) - passed},
        "protected_counts": protected_counts,
        "protected_counts_ok": protected_ok,
        "sources": evaluated,
    }


def load_snapshot(wave):
    with get_session() as session:
        rows = session.execute(text("""
            SELECT s.id AS source_id, s.name, s.country_code, s.last_status,
                   s.config,
                   EXTRACT(EPOCH FROM (NOW() - s.last_fetch_at)) / 60 AS last_fetch_age_minutes,
                   COALESCE(
                     JSON_AGG(JSON_BUILD_OBJECT(
                       'url', COALESCE(NULLIF(a.resolved_url, ''), a.url),
                       'geo_status', a.geo_status
                     )) FILTER (WHERE a.id IS NOT NULL),
                     '[]'::json
                   ) AS articles
            FROM sources s
            LEFT JOIN articles a ON a.source_id = s.id
             AND a.collected_at >= s.created_at
            WHERE s.config->>'source_expansion_wave' = :wave
            GROUP BY s.id
            ORDER BY s.country_code, s.name
        """), {"wave": wave}).mappings().all()
        counts = {
            name: int(session.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar())
            for name in PROTECTED_TABLES
        }
    normalized = []
    for raw in rows:
        row = dict(raw)
        source_config = row.pop("config") or {}
        approved = {
            source_config.get("publisher_domain"),
            *(source_config.get("publisher_domain_aliases") or []),
        } - {None, ""}
        articles = row.pop("articles") or []
        foreign = 0
        unsafe = 0
        for article in articles:
            actual = normalize_publisher_domain(article.get("url"))
            if not any(
                actual == domain or (actual and actual.endswith(f".{domain}"))
                for domain in approved
            ):
                foreign += 1
            if article.get("geo_status") not in {"source_verified", "publisher_verified"}:
                unsafe += 1
        normalized.append({
            **row,
            "last_fetch_age_minutes": (
                float(row["last_fetch_age_minutes"])
                if row["last_fetch_age_minutes"] is not None else None
            ),
            "article_count": len(articles),
            "foreign_url_count": foreign,
            "unsafe_geo_count": unsafe,
        })
    return normalized, counts
```

Add this import at the top of the script so canary URL checks use the same domain
semantics as candidate validation:

```python
from src.collectors.publisher_attribution import normalize_publisher_domain
```

- [ ] **Step 4: Complete the CLI with baseline snapshot support**

```python
# append to scripts/audit_source_wave.py
def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--wave", required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    rows, protected_counts = load_snapshot(args.wave)
    baseline_counts = None
    if args.baseline:
        baseline = json.loads(args.baseline.read_text())
        baseline_counts = baseline["protected_counts"]
    report = evaluate_wave(rows, protected_counts, baseline_counts)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(output)
    print(output)
    return 0 if report["summary"]["failed"] == 0 and report["protected_counts_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run audit tests**

Run: `.venv/bin/pytest tests/test_source_wave_audit.py -v`

Expected: all tests pass.

- [ ] **Step 6: Write the release runbook with exact commands**

`docs/release/national-source-wave1.md` must contain this order:

```bash
# Local release gate
.venv/bin/python scripts/validate_source_candidates.py --json
.venv/bin/pytest -q
git status --short
git push origin main

# Production baseline; read-only
ssh geopulse-prod "cd /opt/geopulse && git pull --ff-only"
ssh geopulse-prod "cd /opt/geopulse && docker compose build api collector"
ssh geopulse-prod "cd /opt/geopulse && docker compose run --rm -v /opt/geopulse/backups:/app/backups collector python scripts/audit_source_wave.py --wave 2026-07-17-rss-1 --json --out /app/backups/source-wave1-baseline.json"

# Start only the two newly built services; db and redis are untouched
ssh geopulse-prod "cd /opt/geopulse && docker compose up -d api collector"

# First post-collection snapshot after at least one 30-minute collector cycle
ssh geopulse-prod "cd /opt/geopulse && docker compose run --rm -v /opt/geopulse/backups:/app/backups collector python scripts/audit_source_wave.py --wave 2026-07-17-rss-1 --baseline /app/backups/source-wave1-baseline.json --json --out /app/backups/source-wave1-first.json"

# Twelve-hour snapshot against the same protected-count baseline
ssh geopulse-prod "cd /opt/geopulse && docker compose run --rm -v /opt/geopulse/backups:/app/backups collector python scripts/audit_source_wave.py --wave 2026-07-17-rss-1 --baseline /app/backups/source-wave1-baseline.json --json --out /app/backups/source-wave1-12h.json"

# HTTP smoke checks
ssh geopulse-prod "curl -fsS http://127.0.0.1:8100/api/v2/health/source-coverage"
ssh geopulse-prod "curl -fsS http://127.0.0.1:8100/api/v2/health/sources"
ssh geopulse-prod "curl -fsS http://127.0.0.1:8100/api/v2/countries"
```

For a failed source, first remove only its new `sources_world.yaml` entry and
deploy `collector`. Then deactivate the exact Wave 1 row with a transaction:

```sql
BEGIN;
UPDATE sources
SET active = FALSE
WHERE id = :source_id
  AND config->>'source_expansion_wave' = '2026-07-17-rss-1'
RETURNING id, name, country_code, active;
COMMIT;
```

Require exactly one returned row. If the update returns zero or multiple rows,
roll back and investigate the ID instead of broadening the predicate. Do not
delete articles collected from the disabled source.

- [ ] **Step 7: Commit audit tooling and release documentation**

```bash
git add scripts/audit_source_wave.py tests/test_source_wave_audit.py docs/release/national-source-wave1.md
git commit -m "feat: add national source canary audit"
```

---

### Task 7: Integrated verification, review, and production canary

**Files:**
- Verify all files changed in Tasks 1-6.
- No new implementation file unless review finds a concrete defect.

**Interfaces:**
- Consumes: all Wave 1 code, catalog data, tests, CLI, coverage endpoint, and audit script.
- Produces: reviewed commits on `main`, production snapshots, and a documented canary verdict.

- [ ] **Step 1: Run the complete local release gate**

Run:

```bash
.venv/bin/python scripts/validate_source_candidates.py --json
.venv/bin/pytest -q
git diff --check origin/main...HEAD
git status --short --branch
```

Expected: 17/17 candidates pass, all tests pass, no whitespace errors, and the worktree is clean.

- [ ] **Step 2: Request independent code review**

Review must check:

- staging YAML cannot be auto-loaded;
- validation fails closed on duplicates, aggregators, stale feeds, and foreign links;
- direct-publisher coverage counts domains rather than rows;
- Singapore is added once and not duplicated through two CNA feeds;
- canary audit performs only `SELECT` queries;
- rollback affects only Wave 1 source rows and never deletes articles.

Expected: no Critical or Important findings remain unresolved.

- [ ] **Step 3: Push and deploy using the committed runbook**

Run the commands from `docs/release/national-source-wave1.md`. Do not run a
database migration and do not recreate `db`, `redis`, `analyzer`, `temperature`,
`threads`, or any other background service.

- [ ] **Step 4: Verify the first collection cycle**

After one normal collector cycle, require:

- `api` and `collector` containers are healthy;
- the three health endpoints return HTTP 200;
- the Wave 1 audit lists 17 source rows;
- no protected count is lower than baseline;
- no article URL or attribution violates its curated publisher domain.

- [ ] **Step 5: Complete the 12-hour canary verdict**

Run the audit again with the original baseline snapshot. The release passes when
at least 15 of 17 sources are healthy, at least eight of the nine target countries
have a working direct publisher, Singapore has one verified direct publisher,
and protected counts have not decreased.

For one or two failing sources, follow the per-source rollback and keep the rest
of the wave active. For three or more failures, stop further source waves and
audit the shared validation or collector assumption before proceeding.

- [ ] **Step 6: Record the production result**

Append a dated result section to `docs/release/national-source-wave1.md` with:

- deployed commit SHA;
- validation summary;
- passing and failed source IDs;
- before/after protected counts;
- country coverage changes;
- rollback actions, if any.

Commit only the result section:

```bash
git add docs/release/national-source-wave1.md
git commit -m "docs: record national source wave one result"
git push origin main
```
