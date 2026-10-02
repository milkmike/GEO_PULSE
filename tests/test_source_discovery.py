import httpx
import pytest

from src.source_discovery import DiscoveryUnavailable, discover_publishers


def statement(value):
    return {"mainsnak": {"snaktype": "value", "datavalue": {"value": value}}}


def entity(qid, *, claims=None, name=None, revision=123):
    return {"id": qid, "type": "item", "lastrevid": revision,
            "labels": {"en": {"language": "en", "value": name or qid}},
            "claims": claims or {}}


def fixture_handler(request):
    params = request.url.params
    assert str(request.url).startswith("https://www.wikidata.org/w/api.php?")
    assert request.headers["User-Agent"].startswith("GEO_PULSE/1.0")
    assert params["maxlag"] == "5" and params["format"] == "json"
    if params["action"] == "query" and "P297=AD" in params["srsearch"]:
        return httpx.Response(200, json={"query": {"search": [{"title": "Q228"}]}})
    if params["action"] == "wbgetentities" and params["ids"] == "Q228":
        return httpx.Response(200, json={"entities": {
            "Q228": entity("Q228", claims={"P297": [statement("AD")]})}})
    if params["action"] == "query" and "P17=Q228" in params["srsearch"]:
        assert "haswbstatement:P31=Q11032|P31=Q192283" in params["srsearch"]
        return httpx.Response(200, json={"query": {"search": [
            {"title": "Q100"}, {"title": "Q101"}, {"title": "Q102"},
            {"title": "Q103"}, {"title": "Q104"},
        ]}})
    if params["action"] == "wbgetentities" and "Q100" in params["ids"]:
        return httpx.Response(200, json={"entities": {
            "Q100": entity("Q100", name="Andorra Daily", claims={
                "P17": [statement({"id": "Q228"})], "P31": [statement({"id": "Q11032"})],
                "P856": [statement("https://daily.ad/")]}),
            "Q101": entity("Q101", claims={
                "P17": [statement({"id": "Q999"})], "P31": [statement({"id": "Q11032"})],
                "P856": [statement("https://wrong-country.org/")]}),
            "Q102": entity("Q102", claims={
                "P17": [statement({"id": "Q228"})], "P31": [statement({"id": "Q999"})],
                "P856": [statement("https://wrong-type.org/")]}),
            "Q103": entity("Q103", claims={
                "P17": [statement({"id": "Q228"})], "P31": [statement({"id": "Q192283"})],
                "P856": [statement("http://127.0.0.1/private")]}),
            "Q104": entity("Q104", name="Andorra Agency", claims={
                "P17": [statement({"id": "Q228"})], "P31": [statement({"id": "Q192283"})],
                "P856": [statement("https://agency.ad/")]}),
        }})
    raise AssertionError(f"unexpected Wikidata request: {request.url}")


def client_for(handler=fixture_handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_exact_country_and_media_claims_yield_only_safe_unverified_leads():
    with client_for() as client:
        leads = discover_publishers("AD", client=client)
    assert leads == [
        {"provider": "wikidata", "entity_id": "Q100", "name": "Andorra Daily",
         "website": "https://daily.ad/", "provenance_url": "https://www.wikidata.org/wiki/Q100",
         "country_entity": "Q228", "revision": 123, "claimed_country": "AD"},
        {"provider": "wikidata", "entity_id": "Q104", "name": "Andorra Agency",
         "website": "https://agency.ad/", "provenance_url": "https://www.wikidata.org/wiki/Q104",
         "country_entity": "Q228", "revision": 123, "claimed_country": "AD"},
    ]


def test_duplicate_search_hits_and_max_leads_are_bounded():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.params.get("action") == "query" and "P17=" in request.url.params["srsearch"]:
            return httpx.Response(200, json={"query": {"search": [
                {"title": "Q100"}, {"title": "Q100"}, {"title": "Q104"}]}})
        return fixture_handler(request)

    with client_for(handler) as client:
        leads = discover_publishers("AD", client=client, max_leads=1)
    assert [lead["entity_id"] for lead in leads] == ["Q100"]
    assert len(calls) == 4


def test_ambiguous_country_and_rate_limit_stop_without_publisher_request():
    calls = []

    def ambiguous(request):
        calls.append(request)
        if request.url.params["action"] == "query":
            return httpx.Response(200, json={"query": {"search": [
                {"title": "Q228"}, {"title": "Q229"}]}})
        return httpx.Response(200, json={"entities": {
            "Q228": entity("Q228", claims={"P297": [statement("AD")]}),
            "Q229": entity("Q229", claims={"P297": [statement("AD")]})}})

    with client_for(ambiguous) as client:
        with pytest.raises(DiscoveryUnavailable) as error:
            discover_publishers("AD", client=client)
    assert error.value.reason == "ambiguous_country" and len(calls) == 2

    calls.clear()

    def limited(request):
        calls.append(request)
        return httpx.Response(429, json={"error": {"code": "ratelimited"}})

    with client_for(limited) as client:
        with pytest.raises(DiscoveryUnavailable) as error:
            discover_publishers("AD", client=client)
    assert error.value.reason == "rate_limited" and len(calls) == 1


def test_invalid_scope_and_oversized_body_fail_closed():
    with pytest.raises(ValueError):
        discover_publishers("RU")
    with pytest.raises(ValueError):
        discover_publishers("ZZ")
    with pytest.raises(ValueError):
        discover_publishers("AD", max_leads=6)
    with pytest.raises(ValueError):
        discover_publishers("AD", timeout_seconds=31)

    with client_for(lambda request: httpx.Response(200, content=b"x" * 1_100_000)) as client:
        with pytest.raises(DiscoveryUnavailable) as error:
            discover_publishers("AD", client=client)
    assert error.value.reason == "limited"


@pytest.mark.parametrize("website", [
    "https://user:pass@daily.ad/", "http://127.0.0.1/private", "https://localhost/",
    "https://paper.local/", "https://paper.internal/", "file:///private/data",
    "https://192.168.1.5/", "https://[::1]/", "https://paper.ad/\nsecret",
])
def test_unsafe_publisher_websites_are_rejected(website):
    from src.source_discovery import _website
    assert _website(website) is None


def test_maxlag_and_total_deadline_stop_without_more_requests(monkeypatch):
    with client_for(lambda request: httpx.Response(503, json={"error": {"code": "maxlag"}})) as client:
        with pytest.raises(DiscoveryUnavailable) as error:
            discover_publishers("AD", client=client)
    assert error.value.reason == "rate_limited"

    from src import source_discovery
    clock = {"now": 0.0}
    monkeypatch.setattr(source_discovery, "monotonic", lambda: clock["now"])
    calls = []

    def slow(request):
        calls.append(request)
        clock["now"] = 16.0
        return httpx.Response(200, json={"query": {"search": [{"title": "Q228"}]}})

    with client_for(slow) as client:
        with pytest.raises(DiscoveryUnavailable) as error:
            discover_publishers("AD", client=client, timeout_seconds=15)
    assert error.value.reason == "limited" and len(calls) == 1


def test_inherited_client_params_credentials_and_cookies_never_reach_wikidata():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.url.host == "www.wikidata.org" and request.url.path == "/w/api.php"
        assert request.url.params.get_list("action") in (["query"], ["wbgetentities"])
        assert "evil" not in str(request.url)
        assert "authorization" not in request.headers and "cookie" not in request.headers
        return fixture_handler(request)

    with httpx.Client(transport=httpx.MockTransport(handler),
                      params={"action": "delete", "srsearch": "evil", "maxlag": "0"},
                      headers={"Authorization": "Bearer secret"},
                      cookies={"token": "secret"}, auth=("user", "pass"),
                      follow_redirects=True) as client:
        assert len(discover_publishers("AD", client=client)) == 2
    assert len(seen) == 4
