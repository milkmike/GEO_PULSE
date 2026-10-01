from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest

from src import news_triage as triage


def article(i=1, **changes):
    return dict(id=i, title="Serbia discusses visas for Russians", excerpt="Serbian ministry discussed visas for Russian citizens.",
                country_code="RS", is_relevant=True, published_at=datetime.now(timezone.utc)) | changes


def answer(choice, labels):
    return {"type": "choice", "choice": choice, "confidence": .8,
            "probabilities": {label: (.8 if label == choice else .2 / (len(labels) - 1)) for label in labels}}


def response(payload, *, relation="direct", country="RS", country_confidence=.8):
    answers = {}
    for key, question in payload["questions"].items():
        labels = question["criteria"]
        choice = relation if key.endswith("_relation") else (country if key.endswith("_country_primary") else
                 "none" if key.endswith("_country_secondary") else next(iter(labels)))
        answers[key] = answer(choice, labels)
        if key.endswith("_country_primary"):
            answers[key]["confidence"] = country_confidence
            answers[key]["probabilities"] = {label: (country_confidence if label == choice else
                (1-country_confidence)/(len(labels)-1)) for label in labels}
    return {"answers": answers, "usage": {"cost": .0001, "input_tokens": 1000}}


def test_typed_batch_is_bounded_and_never_supplies_publisher_as_involvement():
    rows = [article(i, excerpt="x" * 2000, country_code="US") for i in range(1, 12)]
    payload, selected = triage.prepare_payload(rows, {"RS", "GE", "RU"})
    assert 1 <= len(selected) <= 8
    assert len(triage.encode(payload)) <= 24000
    assert payload["state"]["articles"]["article_1"]["excerpt"] == "x" * 2000
    assert "country_code" not in payload["state"]["articles"]["article_1"]
    assert set(payload["questions"]["article_1_country_primary"]["criteria"]) == {"RS", "none", "unknown"}


def test_country_shortlist_uses_article_source_and_full_catalog_fallback():
    named = article(title="Serbia discusses Russia ties", excerpt="The Serbian ministry spoke.", country_code="GE")
    unnamed = article(2, title="Government discusses Russia ties", excerpt="The ministry spoke.", country_code="RS")
    payload, selected = triage.prepare_payload([named, unnamed], {"RS", "GE", "RU"})
    assert set(payload["questions"]["article_1_country_primary"]["criteria"]) == {"RS", "none", "unknown"}
    assert set(payload["questions"]["article_2_country_primary"]["criteria"]) == {"RS", "GE", "none", "unknown"}
    assert "country_code" not in payload["state"]["articles"]["article_1"]
    records, _ = triage.parse_response(response(payload, country="RS"), selected, {"RS", "GE", "RU"})
    assert records[0]["classification"]["countries"] == ["RS"]


def test_relation_question_includes_international_organization_without_named_country():
    payload, _ = triage.prepare_payload([article(title="Russia threatens NATO", excerpt="Rusia amenaza a la OTAN.")], {"RS", "GE"})
    relation = payload["questions"]["article_1_relation"]
    assert "international organization" in relation["criteria"]["direct"].lower()
    assert "named other country" not in relation["instructions"].lower()


def test_relation_choice_is_preserved_and_uncertain_is_a_lead():
    payload, selected = triage.prepare_payload([article()], {"RS", "RU"})
    data = response(payload, relation="uncertain")
    records, cost = triage.parse_response(data, selected, {"RS", "RU"})
    assert records[0]["classification"]["russia_relation"] == "uncertain"
    assert records[0]["classification"]["uncertain"] is True
    assert records[0]["classification"]["countries"] == ["RS"]
    assert cost == .0001


def test_low_confidence_country_is_unknown_and_negative_still_classified():
    payload, selected = triage.prepare_payload([article()], {"RS"})
    records, _ = triage.parse_response(response(payload, relation="none", country_confidence=.4), selected, {"RS"})
    classification = records[0]["classification"]
    assert classification["russia_relation"] == "none"
    assert classification["countries"] == []
    assert classification["country_primary"] == "unknown"


def test_low_confidence_relation_remains_an_uncertain_lead():
    payload, selected = triage.prepare_payload([article()], {"RS"})
    data = response(payload, relation="direct")
    data["answers"]["article_1_relation"]["confidence"] = .4
    records, _ = triage.parse_response(data, selected, {"RS"})
    assert records[0]["classification"]["russia_relation"] == "uncertain"
    assert records[0]["classification"]["uncertain"] is True


def test_low_confidence_event_type_does_not_claim_incident():
    payload, selected = triage.prepare_payload([article()], {"RS"})
    data = response(payload)
    data["answers"]["article_1_event_type"] = answer("incident", triage.EVENT_TYPE)
    data["answers"]["article_1_event_type"]["confidence"] = .4
    records, _ = triage.parse_response(data, selected, {"RS"})
    assert records[0]["classification"]["event_type"] == "other"


def test_wrong_question_or_probability_rejects_entire_batch():
    payload, selected = triage.prepare_payload([article()], {"RS"})
    data = response(payload)
    data["answers"]["extra"] = answer("none", {"none", "unknown"})
    with pytest.raises(ValueError):
        triage.parse_response(data, selected, {"RS"})
    data = response(payload)
    data["answers"]["article_1_relation"]["probabilities"]["direct"] = True
    with pytest.raises(ValueError):
        triage.parse_response(data, selected, {"RS"})


def test_invalid_known_article_selection_fails_even_when_disabled():
    with pytest.raises(ValueError):
        triage.run_triage_cycle(budget_usd=Decimal("0"), campaign="existing", article_ids=[1, 1])


def test_cycle_reserves_before_request_and_limits_calls(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(triage.store, "load_candidates", lambda **kwargs: ([article(i) for i in range(1, 12)], {"RS"}))
    monkeypatch.setattr(triage.budget, "get_attempted_pair_keys", lambda campaign: set())
    monkeypatch.setattr(triage.budget, "get_budget", lambda campaign: 2.0)
    reserve = Mock(return_value="id")
    monkeypatch.setattr(triage.budget, "reserve_request", reserve)
    finish = Mock()
    monkeypatch.setattr(triage.budget, "finish_request", finish)
    save = Mock(side_effect=lambda records, **kwargs: len(records))
    monkeypatch.setattr(triage.store, "save_if_current", save)
    monkeypatch.setattr(triage, "track_api_call", Mock())
    monkeypatch.setattr(triage, "check_tariff", Mock())
    def request(payload, *_):
        assert reserve.called
        return {"status": "ok", "data": response(payload)}
    monkeypatch.setattr(triage, "_request", request)
    result = triage.run_triage_cycle(budget_usd=Decimal("3"), campaign="existing", max_calls=1)
    assert result["calls"] == 1 and result["saved"] == len(reserve.call_args.kwargs["pair_keys"])
    assert reserve.call_args.kwargs["reservation_usd"] == Decimal(".01")
    assert 1 <= len(reserve.call_args.kwargs["pair_keys"]) <= 8
    finish.assert_called_once_with("id", .0001, "ok")


def test_over_reservation_cost_does_not_save(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(triage.store, "load_candidates", lambda **kwargs: ([article()], {"RS"}))
    monkeypatch.setattr(triage.budget, "get_attempted_pair_keys", lambda campaign: set())
    monkeypatch.setattr(triage.budget, "reserve_request", lambda *args, **kwargs: "id")
    finish = Mock()
    monkeypatch.setattr(triage.budget, "finish_request", finish)
    monkeypatch.setattr(triage, "track_api_call", Mock())
    monkeypatch.setattr(triage, "check_tariff", Mock())
    save = Mock()
    monkeypatch.setattr(triage.store, "save_if_current", save)
    def expensive(payload, *_):
        data = response(payload)
        data["usage"]["cost"] = .02
        return {"status": "ok", "data": data}
    monkeypatch.setattr(triage, "_request", expensive)
    stats = triage.run_triage_cycle(budget_usd=Decimal("3"), campaign="existing")
    assert stats["saved"] == 0 and stats["invalid"] == 1
    save.assert_not_called()
    finish.assert_called_once_with("id", .02, "invalid_response")


def test_first_invalid_provider_batch_stops_before_second_reservation(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test")
    monkeypatch.setattr(triage.store, "load_candidates", lambda **kwargs: ([article(i) for i in range(1, 10)], {"RS"}))
    monkeypatch.setattr(triage.budget, "get_attempted_pair_keys", lambda campaign: set())
    reserve = Mock(return_value="id")
    monkeypatch.setattr(triage.budget, "reserve_request", reserve)
    finish = Mock()
    monkeypatch.setattr(triage.budget, "finish_request", finish)
    monkeypatch.setattr(triage, "track_api_call", Mock())
    monkeypatch.setattr(triage, "check_tariff", Mock())
    request = Mock(side_effect=lambda payload, *_: {"status": "ok", "data": {"answers": {}, "usage": {"cost": .0001}}})
    monkeypatch.setattr(triage, "_request", request)
    save = Mock()
    monkeypatch.setattr(triage.store, "save_if_current", save)
    stats = triage.run_triage_cycle(budget_usd=Decimal("3"), campaign="existing")
    assert stats["status"] == "partial" and stats["calls"] == 1 and stats["saved"] == 0
    reserve.assert_called_once()
    request.assert_called_once()
    finish.assert_called_once_with("id", .0001, "invalid_response")
    save.assert_not_called()
