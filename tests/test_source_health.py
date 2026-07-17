from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import world
from src.engine.health import source_coverage


def _source(name, domain, tier, *, status="ok", discovery=False, state=False):
    return {
        "name": name,
        "country_code": "AL",
        "tier": tier,
        "type": "rss",
        "url": f"https://{domain}/feed",
        "config": {"feed_mode": "publisher_discovery"} if discovery else {},
        "state_affiliated": state,
        "last_status": status,
    }


def _site_wrapper_source(name, domain, tier, *, status="ok"):
    source = _source(name, "news.google.com", tier, status=status)
    source["url"] = (
        "https://news.google.com/rss/search"
        f"?q=site%3A{domain}&hl=en&gl=US&ceid=US%3Aen"
    )
    source["config"] = {"feed_mode": "site_wrapper"}
    return source


def _with_publisher_family(source, canonical, *aliases):
    source["config"].update({
        "publisher_domain": canonical,
        "publisher_domain_aliases": list(aliases),
    })
    return source


def test_source_coverage_counts_domains_not_rows_or_discovery():
    result = source_coverage([
        _source("Official one", "official.example", "official", state=True),
        _source("Official alias", "official.example", "official", state=True),
        _source("Mainstream", "mainstream.example", "mainstream"),
        _source("Independent", "independent.example", "independent"),
        _source("Google", "news.google.com", "mainstream", discovery=True),
    ])
    country = next(country for country in result["countries"] if country["country_code"] == "AL")
    assert country["configured"] == 5
    assert country["discovery"] == 1
    assert country["direct_publishers"] == 3
    assert country["working_direct_publishers"] == 3
    assert country["mix"] == {"official": 1, "mainstream": 1, "independent": 1}
    assert country["target_state"] == "balanced"


def test_source_coverage_attributes_site_wrappers_to_distinct_publishers():
    result = source_coverage([
        _site_wrapper_source("First publisher RSS", "first.example", "mainstream"),
        _site_wrapper_source("First publisher alias", "first.example", "mainstream"),
        _site_wrapper_source("Second publisher", "second.example", "independent"),
    ])

    country = next(country for country in result["countries"] if country["country_code"] == "AL")
    assert country["configured"] == 3
    assert country["discovery"] == 0
    assert country["direct_publishers"] == 2
    assert country["working_direct_publishers"] == 2
    assert country["mix"] == {"official": 0, "mainstream": 1, "independent": 1}
    assert country["duplicate_families"] == ["first.example"]
    assert country["target_state"] == "thin"


def test_source_coverage_excludes_centralized_aggregator_families():
    result = source_coverage([
        _source("AllAfrica", "allafrica.com", "mainstream"),
        _source("National publisher", "national.example", "independent"),
    ])

    country = next(country for country in result["countries"] if country["country_code"] == "AL")
    assert country["configured"] == 2
    assert country["direct_publishers"] == 1
    assert country["working_direct_publishers"] == 1
    assert country["mix"] == {"official": 0, "mainstream": 0, "independent": 1}


def test_source_coverage_merges_publisher_families_with_overlapping_aliases():
    result = source_coverage([
        _with_publisher_family(
            _source("First feed", "feeds.first.example", "mainstream"),
            "first.example",
            "zz-shared.example",
        ),
        _with_publisher_family(
            _source("Second feed", "feeds.second.example", "independent"),
            "second.example",
            "zz-shared.example",
        ),
    ])

    country = next(country for country in result["countries"] if country["country_code"] == "AL")
    assert country["direct_publishers"] == 1
    assert country["working_direct_publishers"] == 1
    assert country["mix"] == {"official": 0, "mainstream": 1, "independent": 1}
    assert country["duplicate_families"] == ["first.example"]


def test_source_coverage_uses_site_wrapper_domain_as_family_membership():
    wrapper = _with_publisher_family(
        _site_wrapper_source("Publisher wrapper", "publisher-alias.example", "mainstream"),
        "publisher.example",
    )
    result = source_coverage([
        wrapper,
        _source("Publisher direct feed", "publisher-alias.example", "independent"),
    ])

    country = next(country for country in result["countries"] if country["country_code"] == "AL")
    assert country["direct_publishers"] == 1
    assert country["working_direct_publishers"] == 1
    assert country["mix"] == {"official": 0, "mainstream": 1, "independent": 1}
    assert country["duplicate_families"] == ["publisher-alias.example"]


def test_source_coverage_states_are_uncovered_thin_and_baseline():
    assert source_coverage([])["summary"]["uncovered"] == 99
    thin = source_coverage([_source("One", "one.example", "mainstream")])
    assert next(country for country in thin["countries"] if country["country_code"] == "AL")["target_state"] == "thin"
    baseline = source_coverage([
        _source("One", "one.example", "mainstream"),
        _source("Two", "two.example", "mainstream"),
        _source("Three", "three.example", "mainstream"),
    ])
    assert next(country for country in baseline["countries"] if country["country_code"] == "AL")["target_state"] == "baseline"


def test_source_coverage_api_route_returns_coverage_shape(monkeypatch):
    coverage = {
        "summary": {"uncovered": 99},
        "countries": [],
    }
    monkeypatch.setattr(world, "source_coverage", lambda: coverage)
    app = FastAPI()
    app.include_router(world.router)

    response = TestClient(app).get("/api/v2/health/source-coverage")

    assert response.status_code == 200
    assert response.json() == coverage
