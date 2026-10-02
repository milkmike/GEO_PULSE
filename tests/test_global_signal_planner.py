from datetime import datetime, timedelta, timezone

import pytest

from src.early_signals import source_key
from src.global_signal_planner import plan_candidates
from src.signal_workbench import request_hash


NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def record(article_id, title, *, event="RS", publisher="RS", mechanism="infrastructure",
           signal="change", stage="decision", days=1, source_id=None):
    published = NOW - timedelta(days=days)
    article = {"id": article_id, "title": title, "excerpt": title + " was reported by the source.",
               "source_id": source_id or article_id, "source_name": f"Source {article_id}",
               "country_code": publisher, "url": f"https://example.org/{article_id}",
               "published_at": published.isoformat(), "collected_at": (published + timedelta(hours=1)).isoformat()}
    return {"article": article, "source_key": source_key(article),
            "snapshot_hash": request_hash(article), "classification": {
                "signal": signal, "mechanism": mechanism, "stage": stage,
                "country": event, "status": "needs_review"}}


def test_shared_distinctive_project_name_yields_bounded_provisional_context():
    lead = record(1, "NIS Petrohemija refinery modernization approved", publisher="GE")
    followup = record(2, "Petrohemija refinery modernization works discussed", days=2)
    result = plan_candidates([lead, followup], as_of=NOW)
    first = next(item for item in result if item["source_key"] == lead["source_key"])
    assert first["country_code"] == "RS"  # The publisher is Georgian; the event is Serbian.
    assert first["status"] == "context_ready"
    assert [a["id"] for a in first["context"]["articles"]] == [1, 2]
    assert first["retrieval_links"] == [{"article_id": 2, "relation": "retrieval_only",
                                         "shared_title_tokens": ["modernization", "petrohemija"]}]
    assert first["anchor"]["id"] == 1
    assert len(first["context"]["prompt"].encode()) <= 24_000
    assert "causal" not in first["context"]["note"].lower()


def test_same_country_and_mechanism_without_distinctive_overlap_stays_unresolved():
    rows = [record(1, "Education ministry approves village schools" , mechanism="education"),
            record(2, "Education ministry approves city vocational institute", mechanism="education")]
    result = plan_candidates(rows, as_of=NOW)
    assert all(item["status"] == "needs_context" and item["context"] is None for item in result)
    assert all(item["retrieval_links"] == [] for item in result)


def test_event_country_mechanism_and_geography_are_required_for_context():
    lead = record(1, "NIS Petrohemija refinery modernization approved", publisher="ET")
    wrong_country = record(2, "NIS Petrohemija refinery modernization discussed", event="ET")
    wrong_mechanism = record(3, "NIS Petrohemija refinery modernization discussed", mechanism="trade")
    unknown = record(4, "NIS Petrohemija refinery modernization discussed", event="unknown")
    result = plan_candidates([lead, wrong_country, wrong_mechanism, unknown], as_of=NOW)
    states = {item["source_key"]: item["status"] for item in result}
    assert states[lead["source_key"]] == "needs_context"
    assert states[unknown["source_key"]] == "needs_geography"


def test_stale_future_and_tampered_screenings_cannot_make_candidates():
    fresh = record(1, "NIS Petrohemija refinery modernization approved")
    old = record(2, "NIS Petrohemija refinery modernization discussed", days=8)
    future = record(3, "NIS Petrohemija refinery modernization discussed", days=-1)
    assert [item["source_key"] for item in plan_candidates([old, future, fresh], as_of=NOW)] == [fresh["source_key"]]
    altered = {**fresh, "article": {**fresh["article"], "title": "Changed title"}}
    with pytest.raises(ValueError, match="snapshot|source"):
        plan_candidates([altered], as_of=NOW)


def test_old_report_can_supply_dated_context_but_cannot_be_fresh_anchor():
    fresh = record(1, "NIS Petrohemija modernization approved")
    old = record(2, "Petrohemija modernization proposal reported", days=45)
    result = plan_candidates([old, fresh], as_of=NOW)
    assert len(result) == 1 and result[0]["status"] == "context_ready"
    assert [a["id"] for a in result[0]["context"]["articles"]] == [1, 2]
    assert result[0]["context"]["articles"][1]["published_at"] == old["article"]["published_at"]


def test_corrected_source_metadata_requires_a_new_screening():
    lead = record(1, "NIS Petrohemija modernization approved")
    corrected = {**lead["article"], "url": "https://example.org/correction"}
    assert plan_candidates([lead], [corrected], as_of=NOW) == []


def test_routine_is_excluded_unknown_mechanism_retained_and_six_evidence_cap():
    rows = [record(1, "NIS Petrohemija refinery modernization approved")]
    rows += [record(i, f"NIS Petrohemija refinery modernization phase {i}") for i in range(2, 10)]
    rows[2]["article"]["url"] = rows[1]["article"]["url"]
    rows[2]["source_key"] = source_key(rows[2]["article"])
    rows[2]["snapshot_hash"] = request_hash(rows[2]["article"])
    rows += [record(20, "Celebrity dispute", signal="routine"),
             record(21, "Unclear item", mechanism="unknown")]
    result = plan_candidates(rows, as_of=NOW)
    assert len(result) == 10
    first = next(item for item in result if item["source_key"] == rows[0]["source_key"])
    assert first["status"] == "context_ready"
    assert len(first["context"]["articles"]) == 6
    assert len({a["url"] for a in first["context"]["articles"]}) == 6
    assert rows[-2]["source_key"] not in {item["source_key"] for item in result}
    unresolved = next(item for item in result if item["source_key"] == rows[-1]["source_key"])
    assert unresolved["status"] == "needs_context" and unresolved["context"] is None


def test_other_and_unknown_mechanisms_remain_private_unresolved_leads():
    health = record(30, "District clinic opens emergency ward", mechanism="other")
    health_followup = record(31, "District clinic opens emergency ward expansion", mechanism="other")
    science = record(32, "Laboratory reports novel malaria assay", mechanism="unknown",
                     signal="uncertain")
    science_followup = record(33, "Laboratory reports novel malaria assay review",
                              mechanism="unknown", signal="uncertain")
    result = plan_candidates([health, health_followup, science, science_followup], as_of=NOW)
    assert len(result) == 4
    assert all(item["status"] == "needs_context" and item["context"] is None
               and item["retrieval_links"] == [] for item in result)


def test_quiet_event_country_survives_dominant_country_above_limit():
    dominant = [record(i, f"Serbia local infrastructure proposal {i}") for i in range(1, 101)]
    quiet = record(101, "Andorra local infrastructure proposal", event="AD", publisher="RS")
    result = plan_candidates(dominant + [quiet], as_of=NOW)
    assert len(result) == 80
    assert quiet["source_key"] in {item["source_key"] for item in result}
    assert {item["country_code"] for item in result} == {"RS", "AD"}


def test_existing_eighty_do_not_starve_new_anchor_and_remain_context_pool():
    known = [record(i, f"NIS Petrohemija modernization phase {i}") for i in range(1, 81)]
    new = record(81, "NIS Petrohemija modernization approved")
    result = plan_candidates(known + [new], as_of=NOW,
                             defer_keys={row["source_key"] for row in known})
    assert len(result) == 80
    assert result[0]["source_key"] == new["source_key"]
    assert result[0]["status"] == "context_ready"
    assert any(link["article_id"] in range(1, 81) for link in result[0]["retrieval_links"])
    assert known[-1]["source_key"] not in {item["source_key"] for item in result}


def test_country_names_from_world_registry_are_not_distinctive_event_links():
    left = record(1, "United Kingdom education ministry approves schools", event="GB",
                  mechanism="education")
    right = record(2, "United Kingdom education ministry reviews colleges", event="GB",
                   mechanism="education")
    result = plan_candidates([left, right], as_of=NOW)
    assert all(item["status"] == "needs_context" for item in result)


def test_country_iso3_acronym_alone_does_not_link_unrelated_reports():
    left = record(1, "USA schools approve village buses", event="US", mechanism="education")
    right = record(2, "USA university opens science laboratory", event="US",
                   mechanism="education")
    result = plan_candidates([left, right], as_of=NOW)
    assert all(item["status"] == "needs_context" and item["retrieval_links"] == []
               for item in result)


def test_invalid_defer_keys_rejected():
    with pytest.raises(ValueError, match="planner input"):
        plan_candidates([], as_of=NOW, defer_keys=["not a set"])
