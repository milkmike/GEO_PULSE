from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest
import yaml

from src import config
from src.collectors.source_candidates import (
    candidate_to_catalog_source,
    configured_publisher_sources,
    fetch_and_validate_candidate,
    load_source_candidates,
    render_validation_markdown,
    validate_candidate_metadata,
    validate_feed_document,
    validation_summary,
)


NOW = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[1]
TARGET_WAVE = "2026-07-17-rss-1"


class StubClient:
    def __init__(self, body: bytes, status_code: int = 200):
        self.response = SimpleNamespace(content=body, status_code=status_code)
        self.calls = []

    def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


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


def _feed_with_dates(domain: str, *published_values: str) -> bytes:
    items = "".join(
        f"<item><title>Story {index}</title>"
        f"<link>https://{domain}/{index}</link>"
        f"<pubDate>{published}</pubDate></item>"
        for index, published in enumerate(published_values)
    )
    return (
        f"<rss version='2.0'><channel><title>News</title>{items}</channel></rss>"
    ).encode()


def test_fetch_and_report_are_machine_and_human_readable():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    client = StubClient(
        _feed("https://rtsh.al/a", "https://rtsh.al/b", "https://rtsh.al/c")
    )

    result = fetch_and_validate_candidate(candidate, client=client, now=NOW)

    assert result.ok is True
    assert validation_summary([result]) == {"total": 1, "passed": 1, "failed": 0}
    markdown = render_validation_markdown([result])
    assert "| AL | RTSH | PASS |" in markdown
    assert client.calls == [
        (
            (candidate.feed_url,),
            {
                "timeout": 20,
                "follow_redirects": True,
                "headers": {"User-Agent": "GEO-PULSE source validator/1.0"},
            },
        )
    ]


def test_fetch_fails_closed_on_non_200_response():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]

    result = fetch_and_validate_candidate(
        candidate,
        client=StubClient(b"temporarily unavailable", status_code=503),
        now=NOW,
    )

    assert result.ok is False
    assert result.reasons == ("http_status:503",)
    assert result.item_count == 0


def test_fetch_fails_closed_on_httpx_network_error():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    request = httpx.Request("GET", candidate.feed_url)

    class FailingClient:
        def get(self, *args, **kwargs):
            raise httpx.ConnectError("network unavailable", request=request)

    result = fetch_and_validate_candidate(candidate, client=FailingClient(), now=NOW)

    assert result.ok is False
    assert result.reasons == ("fetch_error:ConnectError",)
    assert result.item_count == 0


def test_catalog_cli_runs_directly_from_repo_root_and_emits_json():
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/validate_source_candidates.py",
            "--catalog-only",
            "--json",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["summary"] == {"total": 17, "passed": 17, "failed": 0}
    assert len(payload["results"]) == 17


def test_cli_out_matches_stable_json_stdout(tmp_path, capsys):
    from scripts import validate_source_candidates as cli

    output_path = tmp_path / "candidate-validation.json"

    first_exit = cli.main(
        ["--catalog-only", "--json", "--out", str(output_path)]
    )
    first_stdout = capsys.readouterr().out
    second_exit = cli.main(["--catalog-only", "--json"])
    second_stdout = capsys.readouterr().out

    assert first_exit == second_exit == 0
    assert first_stdout == second_stdout == output_path.read_text(encoding="utf-8")
    assert json.loads(first_stdout)["summary"] == {
        "total": 17,
        "passed": 17,
        "failed": 0,
    }


def test_cli_fetches_every_candidate_only_after_clean_metadata(monkeypatch, capsys):
    from scripts import validate_source_candidates as cli

    fetched = []

    class ClientContext:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    def fake_fetch(candidate, *, client):
        fetched.append((candidate, client))
        return validate_candidate_metadata(candidate, configured_publishers={})

    monkeypatch.setattr(cli, "fetch_and_validate_candidate", fake_fetch, raising=False)
    monkeypatch.setattr(
        cli,
        "httpx",
        SimpleNamespace(Client=ClientContext),
        raising=False,
    )

    exit_code = cli.main(["--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["summary"] == {"total": 17, "passed": 17, "failed": 0}
    assert len(fetched) == 17


def test_cli_does_not_fetch_when_any_candidate_metadata_fails(monkeypatch, capsys):
    from scripts import validate_source_candidates as cli

    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    duplicate = {candidate.canonical_domain: {("ZZ", "https://duplicate.test/rss")}}
    monkeypatch.setattr(cli, "configured_publisher_sources", lambda loaded: duplicate)

    class ForbiddenClient:
        def __init__(self):
            raise AssertionError("HTTP client must not be created after metadata failure")

    monkeypatch.setattr(cli.httpx, "Client", ForbiddenClient)

    exit_code = cli.main(["--json"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["summary"] == {"total": 17, "passed": 16, "failed": 1}
    assert payload["results"][0]["reasons"] == ["duplicate_publisher_domain"]


def test_cli_rejects_an_empty_candidate_registry(tmp_path, capsys):
    from scripts import validate_source_candidates as cli

    registry = tmp_path / "empty-candidates.yaml"
    registry.write_text("version: 1\ncandidates: []\n", encoding="utf-8")

    exit_code = cli.main(
        ["--candidate-file", str(registry), "--catalog-only", "--json"]
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {"total": 0, "passed": 0, "failed": 0}
    assert exit_code == 1


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


def _assert_promoted_wave1_catalog(candidates, loaded):
    promoted = [
        item
        for item in candidates
        if item.status == "promoted" and item.wave == TARGET_WAVE
    ]
    assert len(promoted) == 17

    wave_sources = [
        (code, source)
        for code, country in loaded.items()
        for source in country.get("sources", [])
        if (source.get("config") or {}).get("source_expansion_wave")
        == TARGET_WAVE
    ]
    promoted_urls = {candidate.feed_url for candidate in promoted}
    assert len(wave_sources) == 17
    assert {source["url"] for _, source in wave_sources} == promoted_urls

    by_url = {source["url"]: (code, source) for code, source in wave_sources}
    for candidate in promoted:
        code, source = by_url[candidate.feed_url]
        assert code == candidate.country_code
        assert source == candidate_to_catalog_source(candidate)
        assert "news.google.com" not in source["url"]


def test_promoted_wave1_catalog_contract_rejects_duplicate_wave_entry():
    candidates = load_source_candidates(config.SOURCE_CANDIDATES_PATH)
    loaded = config.load_sources()["countries"]
    first = candidates[0]
    duplicate_loaded = {
        **loaded,
        first.country_code: {
            **loaded[first.country_code],
            "sources": [
                *loaded[first.country_code]["sources"],
                candidate_to_catalog_source(first),
            ],
        },
    }

    with pytest.raises(AssertionError):
        _assert_promoted_wave1_catalog(candidates, duplicate_loaded)


def test_promoted_wave1_catalog_contract_rejects_coordinated_wrong_wave_retag():
    candidates = load_source_candidates(config.SOURCE_CANDIDATES_PATH)
    loaded = config.load_sources()["countries"]
    original_wave = candidates[0].wave
    wrong_wave = "2026-07-18-rss-2"
    retagged_candidates = [
        replace(candidate, wave=wrong_wave) for candidate in candidates
    ]
    retagged_loaded = {
        code: {
            **country,
            "sources": [
                {
                    **source,
                    "config": {
                        **source["config"],
                        "source_expansion_wave": wrong_wave,
                    },
                }
                if (source.get("config") or {}).get("source_expansion_wave")
                == original_wave
                else source
                for source in country.get("sources", [])
            ],
        }
        for code, country in loaded.items()
    }

    with pytest.raises(AssertionError):
        _assert_promoted_wave1_catalog(retagged_candidates, retagged_loaded)


def test_promoted_wave1_catalog_contains_exactly_17_direct_publishers():
    candidates = load_source_candidates(config.SOURCE_CANDIDATES_PATH)
    loaded = config.load_sources()["countries"]

    _assert_promoted_wave1_catalog(candidates, loaded)


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


def test_configured_publishers_register_explicit_wrapper_and_alias_families():
    direct_url = "https://feeds.example.test/latest.xml"
    wrapper_url = (
        "https://news.google.com/rss/search?"
        "q=site:wrapper.example&hl=en&gl=IE&ceid=IE:en"
    )
    fallback_url = "https://news.fallback.example/rss"
    publishers = configured_publisher_sources(
        {
            "countries": {
                "ie": {
                    "sources": [
                        {
                            "url": direct_url,
                            "config": {
                                "publisher_domain": "WWW.PUBLISHER.EXAMPLE",
                                "publisher_domain_aliases": [
                                    "RSS.ALIAS.EXAMPLE",
                                    "https://m.second-alias.example/about",
                                ],
                            },
                        },
                        {
                            "url": wrapper_url,
                            "config": {
                                "publisher_domain_aliases": ["AMP.WRAPPER-ALIAS.EXAMPLE"],
                            },
                        },
                        {"url": fallback_url, "config": {}},
                        {
                            "url": "https://feeds.example.test/disallowed.xml",
                            "config": {
                                "publisher_domain": "allafrica.com",
                                "publisher_domain_aliases": ["hidden-wire.example"],
                            },
                        },
                        {
                            "url": "https://news.google.com/rss/search?q=Ireland",
                            "config": {
                                "feed_mode": "publisher_discovery",
                                "publisher_domain": "discovery.example",
                                "publisher_domain_aliases": ["discovery-alias.example"],
                            },
                        },
                    ],
                }
            }
        }
    )

    assert publishers == {
        "publisher.example": {("IE", direct_url)},
        "alias.example": {("IE", direct_url)},
        "second-alias.example": {("IE", direct_url)},
        "wrapper.example": {("IE", wrapper_url)},
        "wrapper-alias.example": {("IE", wrapper_url)},
        "news.fallback.example": {("IE", fallback_url)},
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


def test_candidate_duplicate_check_uses_canonical_and_every_alias():
    candidate = replace(
        load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0],
        domain_aliases=("alias.rtsh.example", "other.rtsh.example"),
    )

    result = validate_candidate_metadata(
        candidate,
        configured_publishers={
            "other.rtsh.example": {("ZZ", "https://duplicate.example/feed")},
        },
    )

    assert "duplicate_publisher_domain" in result.reasons


def test_promoted_candidate_allows_exact_self_identity_across_domain_family():
    candidate = replace(
        load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0],
        status="promoted",
        domain_aliases=("alias.rtsh.example",),
    )
    identity = (candidate.country_code, candidate.feed_url)

    result = validate_candidate_metadata(
        candidate,
        configured_publishers={
            candidate.canonical_domain: {identity},
            candidate.domain_aliases[0]: {identity},
        },
    )

    assert result.ok is True


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
        (
            {"ownership_evidence_url": "https:///missing-host"},
            "ownership_evidence_must_be_https",
        ),
        (
            {"publisher_url": "http://publisher.example"},
            "publisher_url_must_be_https",
        ),
        ({"publisher_url": "https:///missing-host"}, "publisher_url_must_be_https"),
        ({"research_date": "17-07-2026"}, "invalid_research_date"),
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


@pytest.mark.parametrize(
    "domain",
    [
        "allafrica.com",
        "tass.com",
        "tass.ru",
        "rt.com",
        "sputnikglobe.com",
        "sputniknews.com",
        "ria.ru",
    ],
)
def test_metadata_rejects_disallowed_aggregator_and_wire_families(domain):
    candidate = replace(
        load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0],
        canonical_domain=domain,
        feed_url=f"https://{domain}/rss",
    )

    result = validate_candidate_metadata(candidate, configured_publishers={})

    assert "aggregator_domain" in result.reasons


def test_metadata_rejects_disallowed_domain_hidden_in_alias_family():
    candidate = replace(
        load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0],
        domain_aliases=("updates.allafrica.com",),
    )

    result = validate_candidate_metadata(candidate, configured_publishers={})

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


def test_feed_validation_rejects_bozo_parse_even_with_recovered_entries():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    truncated = _feed(
        "https://rtsh.al/a",
        "https://rtsh.al/b",
        "https://rtsh.al/c",
    ).replace(b"</channel></rss>", b"")

    result = validate_feed_document(candidate, truncated, now=NOW)

    assert result.item_count == 3
    assert "malformed_feed" in result.reasons


def test_feed_validation_requires_dates_on_at_least_80_percent_of_entries():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    body = _feed_with_dates(
        "rtsh.al",
        "Thu, 16 Jul 2026 10:00:00 GMT",
        "not-a-date",
        "still-not-a-date",
        "missing-date-value",
        "invalid",
    )

    result = validate_feed_document(candidate, body, now=NOW)

    assert "entry_date_ratio_below_0_8" in result.reasons


def test_feed_validation_accepts_sub_two_hour_clock_skew():
    candidate = next(
        candidate
        for candidate in load_source_candidates(config.SOURCE_CANDIDATES_PATH)
        if candidate.name == "Diário de Notícias"
    )
    now = datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc)
    body = _feed(
        "https://dn.pt/a",
        "https://dn.pt/b",
        "https://dn.pt/c",
        published="Fri, 17 Jul 2026 08:52:00 GMT",
    )

    result = validate_feed_document(candidate, body, now=now)

    assert result.ok is True


def test_feed_validation_rejects_materially_future_timestamps():
    candidate = load_source_candidates(config.SOURCE_CANDIDATES_PATH)[0]
    body = _feed(
        "https://rtsh.al/a",
        "https://rtsh.al/b",
        "https://rtsh.al/c",
        published="Thu, 01 Jan 2099 10:00:00 GMT",
    )

    result = validate_feed_document(candidate, body, now=NOW)

    assert "future_entry_date" in result.reasons


@pytest.mark.parametrize(
    ("published", "now"),
    [
        pytest.param(
            "17.07.2026T08:24:02 +0100",
            datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc),
            id="live-offset-form",
        ),
        pytest.param(
            "17.07.2026. 06:47",
            datetime(2026, 7, 17, 7, 0, tzinfo=timezone.utc),
            id="legacy-dot-form",
        ),
    ],
)
def test_feed_validation_parses_only_exact_rtcg_raw_date_forms(published, now):
    candidate = next(
        candidate
        for candidate in load_source_candidates(config.SOURCE_CANDIDATES_PATH)
        if candidate.name == "RTCG"
    )
    body = _feed(
        "https://rtcg.me/a",
        "https://rtcg.me/b",
        "https://rtcg.me/c",
        published=published,
    )

    result = validate_feed_document(candidate, body, now=now)

    assert result.ok is True


def test_feed_validation_does_not_guess_other_rtcg_date_shapes():
    candidate = next(
        candidate
        for candidate in load_source_candidates(config.SOURCE_CANDIDATES_PATH)
        if candidate.name == "RTCG"
    )
    body = _feed(
        "https://rtcg.me/a",
        "https://rtcg.me/b",
        "https://rtcg.me/c",
        published="17.07.2026 06:47",
    )

    result = validate_feed_document(
        candidate,
        body,
        now=datetime(2026, 7, 17, 7, 0, tzinfo=timezone.utc),
    )

    assert "missing_entry_dates" in result.reasons


def test_feed_validation_accepts_current_thejournal_items_among_old_promos():
    candidate = next(
        candidate
        for candidate in load_source_candidates(config.SOURCE_CANDIDATES_PATH)
        if candidate.name == "TheJournal.ie"
    )
    body = _feed_with_dates(
        "thejournal.ie",
        "Mon, 01 Jan 2024 10:00:00 GMT",
        "Tue, 02 Jan 2024 10:00:00 GMT",
        "Wed, 03 Jan 2024 10:00:00 GMT",
        "Thu, 04 Jan 2024 10:00:00 GMT",
        "Fri, 17 Jul 2026 09:00:00 GMT",
    )

    result = validate_feed_document(candidate, body, now=NOW)

    assert result.ok is True


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


def test_wave1_candidate_registry_is_complete_and_promoted():
    candidates = load_source_candidates(config.SOURCE_CANDIDATES_PATH)
    assert len(candidates) == 17
    assert len({item.feed_url for item in candidates}) == 17
    assert {item.country_code for item in candidates} == {
        "AL", "CY", "DK", "IE", "ME", "MK", "PT", "SI", "SG",
    }
    assert {item.status for item in candidates} == {"promoted"}

    production_urls = {
        source["url"]
        for country in config.load_sources()["countries"].values()
        for source in country.get("sources", [])
    }
    assert {item.feed_url for item in candidates} <= production_urls


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


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"publisher_url": "http://publisher.example"},
            "publisher_url must be an absolute HTTPS URL with a hostname",
        ),
        (
            {"publisher_url": "https:///missing-host"},
            "publisher_url must be an absolute HTTPS URL with a hostname",
        ),
        (
            {"ownership_evidence_url": "https:///missing-host"},
            "ownership_evidence_url must be an absolute HTTPS URL with a hostname",
        ),
        (
            {"research_date": "20260717"},
            r"research_date must be an ISO date \(YYYY-MM-DD\)",
        ),
    ],
)
def test_loader_validates_https_metadata_urls_and_iso_research_date(
    tmp_path,
    overrides,
    message,
):
    with pytest.raises(ValueError, match=message):
        _load_document(
            tmp_path,
            {"version": 1, "candidates": [_candidate(**overrides)]},
        )
