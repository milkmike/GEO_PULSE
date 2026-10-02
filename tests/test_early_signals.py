"""Offline contract tests. Synthetic answers exercise parsing, not model accuracy."""
from datetime import datetime, timedelta, timezone
import hashlib
import math

import pytest

from src import early_signals as screening


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def article(i=1, country="RS", publisher="Gazette", **changes):
    return dict(id=i, title="Regional university opens exchange office",
                excerpt="A small exchange office opened this week.", source_id=i,
                source_name=publisher, country_code=country,
                url=f"https://example.test/{i}", published_at=NOW-timedelta(days=1),
                collected_at=NOW, is_relevant=False) | changes


def decision(choice, labels, confidence=.8):
    others = (1-.8)/(len(labels)-1)
    return {"type": "choice", "choice": choice, "confidence": confidence,
            "probabilities": {label: (.8 if label == choice else others) for label in labels}}


def response(payload):
    choices = {"signal": "change", "mechanism": "education",
               "stage": "implementation", "country": "RS"}
    return {"answers": {key: decision(choices[key.rsplit("_", 1)[-1]], question["criteria"])
                        for key, question in payload["questions"].items()},
            "usage": {"cost": .0003}}


def test_legacy_negative_still_selected_and_input_is_bounded():
    rows = [article(1), article(2, published_at=NOW-timedelta(days=8)),
            article(3, published_at=NOW+timedelta(minutes=1)),
            article(4, source_id=0), article(5, title=""),
            article(6, collected_at=NOW+timedelta(minutes=1))]
    assert [a["id"] for a in screening.select_candidates(rows, as_of=NOW)] == [1]
    with pytest.raises(ValueError):
        screening.select_candidates(rows * 5001, as_of=NOW)
    assert [a["id"] for a in screening.select_candidates([
        article(published_at="2026-09-30T00:00:00Z", collected_at="2026-10-01T00:00:00+00:00")],
        as_of=NOW)] == [1]


def test_country_and_publisher_round_robin_avoids_volume_weighting():
    rows = [article(i, "RS", "A", source_id=1) for i in range(1, 30)]
    rows += [article(30, "RS", "B", source_id=30), article(31, "GE", "C", source_id=31)]
    ids = [a["id"] for a in screening.select_candidates(rows, as_of=NOW, limit=4)]
    assert set(ids[:3]) == {29, 30, 31}
    assert ids[3] == 28
    assert ids == [a["id"] for a in screening.select_candidates(list(reversed(rows)), as_of=NOW, limit=4)]


def test_later_hour_reaches_countries_and_publishers_beyond_limit():
    countries = [chr(65+i//26) + chr(65+i%26) for i in range(100)]
    rows = [article(i+1, country=code) for i, code in enumerate(countries)]
    at_first = {a["id"] for a in screening.select_candidates(rows, as_of=NOW)}
    at_later = {a["id"] for a in screening.select_candidates(rows, as_of=NOW+timedelta(hours=20))}
    assert len(at_first) == len(at_later) == 80
    assert at_first | at_later == set(range(1, 101))
    rows = [article(i, source_id=i, publisher=f"Source {i}") for i in range(1, 101)]
    at_first = {a["id"] for a in screening.select_candidates(rows, as_of=NOW)}
    at_later = {a["id"] for a in screening.select_candidates(rows, as_of=NOW+timedelta(hours=20))}
    assert at_first | at_later == set(range(1, 101))


def test_conflicting_duplicate_identity_is_rejected_and_exact_duplicate_collapses():
    row = article()
    assert [a["id"] for a in screening.select_candidates([row, dict(row)], as_of=NOW)] == [1]
    with pytest.raises(ValueError, match="duplicate"):
        screening.select_candidates([row, dict(row, title="Different report")], as_of=NOW)
    payload, selected = screening.prepare_payload([row, dict(row)], {"RS"})
    assert len(selected) == len(payload["state"]["articles"]) == 1
    with pytest.raises(ValueError, match="duplicate"):
        screening.prepare_payload([row, dict(row, source_name="Different publisher")], {"RS"})


@pytest.mark.parametrize("url", ["http://127.0.0.1/private", "http://169.254.169.254/latest",
                                  "http://localhost/", "http://site.local/", "file:///tmp/article",
                                  "https://user:pass@example.org/", "https://example.org/%0a"])
def test_unsafe_article_urls_never_enter_candidates(url):
    assert screening.select_candidates([article(url=url)], as_of=NOW) == []


def test_payload_is_bounded_and_keeps_full_country_catalog_without_publisher_geo():
    rows = [article(i, country="GE", title="Russia absent here", excerpt="x"*2000)
            for i in range(1, 20)]
    payload, selected = screening.prepare_payload(rows, {"RS", "GE", "RU"})
    assert 1 <= len(selected) <= 8
    assert len(screening.encode(payload)) <= 24000
    assert payload["model"] == "typesafe/jev-1.13"
    assert set(payload["questions"]["article_1_country"]["criteria"]) == {"RS", "GE", "RU", "none", "unknown"}
    assert payload["state"]["articles"]["article_1"] == {"title": "Russia absent here", "excerpt": "x"*2000}
    assert "Gazette" not in screening.encode(payload).decode()
    assert screening.source_key(rows[0]) == screening.source_key(dict(rows[0], excerpt="x"*2000+"not sent"))
    with pytest.raises(ValueError):
        screening.prepare_payload([article(title="x"*513)], {"RS"})


def test_prompt_covers_private_noise_strategic_visits_and_reported_stage():
    payload, _ = screening.prepare_payload([article()], {"RS", "ZA", "ET"})
    questions = payload["questions"]
    signals = questions["article_1_signal"]["criteria"]
    stages = questions["article_1_stage"]["criteria"]
    assert "private celebrity" in signals["routine"]
    assert "court order" in signals["routine"]
    assert "strategic industrial site" in signals["uncertain"]
    assert "visit alone is not a project decision" in signals["uncertain"]
    assert "small primary report" in signals["change"]
    assert "without approval or execution" in stages["proposal"]
    assert "execution is not established" in stages["decision"]
    assert "actually started" in stages["implementation"]
    assert "not completion" in stages["implementation"]
    assert "Russia mention" in questions["article_1_signal"]["instructions"]
    assert "publisher's location" in questions["article_1_country"]["instructions"]
    assert "Gazette" not in screening.encode(payload).decode()


def test_changed_screening_version_invalidates_prior_source_key():
    row = article()
    for old_version in ("early-signal-v1", "early-signal-v2"):
        old_key = hashlib.sha256(screening.encode({
            "version": old_version, "model": screening.MODEL, "id": row["id"],
            "state": {"title": row["title"], "excerpt": row["excerpt"]},
        })).hexdigest()
        assert screening.source_key(row) != old_key
    assert screening.VERSION == "early-signal-v3"
    assert screening.source_key(row) != screening.source_key(dict(row, excerpt="Different report"))


def test_source_key_changes_for_corrected_provenance_but_normalizes_timestamps():
    row = article()
    key = screening.source_key(row)
    for change in (
        {"url": "https://example.test/corrected"},
        {"source_id": 99},
        {"source_name": "Corrected Gazette"},
        {"country_code": "GE"},
        {"published_at": NOW-timedelta(days=2)},
        {"collected_at": NOW-timedelta(hours=1)},
    ):
        assert screening.source_key(dict(row, **change)) != key
    assert screening.source_key(dict(row,
        published_at="2026-09-30T00:00:00Z",
        collected_at="2026-10-01T00:00:00+00:00")) == key
    assert screening.source_key(dict(row,
        published_at="2026-09-30T03:00:00+03:00",
        collected_at="2026-10-01T03:00:00+03:00")) == key
    with pytest.raises(ValueError, match="provenance"):
        screening.source_key(dict(row, collected_at=None))


def test_parser_preserves_provisional_decisions_and_cost():
    row = article(country="GE")
    payload, selected = screening.prepare_payload([row], {"RS", "GE"})
    records, cost = screening.parse_response(response(payload), selected, {"RS", "GE"})
    assert cost == .0003
    assert records[0]["article"] is row
    assert records[0]["source_key"] == screening.source_key(row)
    assert records[0]["classification"]["signal"] == "change"
    assert records[0]["classification"]["country"] == "RS"
    assert records[0]["classification"]["status"] == "needs_review"


def test_low_confidence_decisions_become_uncertain_or_unknown():
    payload, selected = screening.prepare_payload([article(country="GE")], {"RS", "GE"})
    data = response(payload)
    for suffix in ("signal", "mechanism", "stage", "country"):
        data["answers"][f"article_1_{suffix}"]["confidence"] = .5
    result, _ = screening.parse_response(data, selected, {"RS", "GE"})
    assert result[0]["classification"] == dict(signal="uncertain", mechanism="unknown",
                                                  stage="unknown", country="unknown",
                                                  status="needs_review")


def test_parser_rejects_incomplete_or_inconsistent_batch():
    payload, selected = screening.prepare_payload([article()], {"RS", "GE"})
    data = response(payload)
    del data["answers"]["article_1_stage"]
    with pytest.raises(ValueError):
        screening.parse_response(data, selected, {"RS", "GE"})
    data = response(payload)
    data["answers"]["article_1_signal"]["probabilities"]["change"] = math.nan
    with pytest.raises(ValueError):
        screening.parse_response(data, selected, {"RS", "GE"})
    data = response(payload)
    data["answers"]["article_1_country"]["choice"] = "US"
    with pytest.raises(ValueError):
        screening.parse_response(data, selected, {"RS", "GE"})
