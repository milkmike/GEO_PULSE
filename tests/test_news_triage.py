from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import Mock

import pytest

from src import news_triage as triage


def test_country_catalog_does_not_exclude_moldova_when_only_us_name_is_literal():
    labels = triage._country_criteria({'MD', 'US'}, {
        'title': 'Moldovan parliament discusses energy cooperation with the United States',
        'excerpt': 'Moldovan deputies discussed possible supply agreements.'})
    assert {'MD', 'US'} <= labels.keys()


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


def chat_response(payload, *, country="MD", country_quote="Moldova", confidence=.8):
    answers = {}
    for key, question in payload["questions"].items():
        labels = question["criteria"]
        choice = (country if key.endswith("_country_primary") else
                  "none" if key.endswith("_country_secondary") else
                  "direct" if key.endswith("_relation") else next(iter(labels)))
        article_key = key.rsplit("_", 1)[0]
        if key.endswith(("country_primary", "country_secondary", "event_type", "actor_type")):
            article_key = key.split("_country_")[0] if "_country_" in key else key.rsplit("_", 2)[0]
        quote = country_quote if key.endswith("_country_primary") else "Russia"
        evidence = [] if choice in {"none", "unknown", "uncertain"} else [{"article_id": article_key, "quote": quote}]
        answers[key] = {"type": "choice", "choice": choice, "confidence": confidence,
                        "confidence_kind": "self_reported", "evidence": evidence}
    return {"answers": answers, "usage": {"cost": .001, "input_tokens": 150}}


def test_typed_batch_is_bounded_and_never_supplies_publisher_as_involvement():
    rows = [article(i, excerpt="x" * 2000, country_code="US") for i in range(1, 12)]
    payload, selected = triage.prepare_payload(rows, {"RS", "GE", "RU"})
    assert 1 <= len(selected) <= 8
    assert len(triage.encode(payload)) <= 24000
    assert payload["state"]["articles"]["article_1"]["excerpt"] == "x" * 2000
    assert "country_code" not in payload["state"]["articles"]["article_1"]
    assert set(payload["questions"]["article_1_country_primary"]["criteria"]) == {"RS", "GE", "none", "unknown"}


def test_chat_batch_is_four_articles_to_leave_room_for_grounded_answers():
    rows = [article(i) for i in range(1, 10)]
    _, selected = triage.prepare_payload(rows, {"RS"})
    assert len(selected) == (8 if triage.decision_model.PROVIDER == "jev" else 4)


def test_country_catalog_keeps_all_allowed_countries_for_named_and_unnamed_articles():
    named = article(title="Serbia discusses Russia ties", excerpt="The Serbian ministry spoke.", country_code="GE")
    unnamed = article(2, title="Government discusses Russia ties", excerpt="The ministry spoke.", country_code="RS")
    payload, selected = triage.prepare_payload([named, unnamed], {"RS", "GE", "RU"})
    assert set(payload["questions"]["article_1_country_primary"]["criteria"]) == {"RS", "GE", "none", "unknown"}
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
    monkeypatch.setenv(triage.decision_model.KEY_ENV, "test")
    monkeypatch.setattr(triage.store, "load_candidates", lambda **kwargs: ([article(i) for i in range(1, 12)], {"RS"}))
    monkeypatch.setattr(triage.budget, "get_attempted_pair_keys", lambda campaign: set())
    monkeypatch.setattr(triage.budget, "get_budget", lambda campaign: 2.0)
    reserve = Mock(return_value="id")
    monkeypatch.setattr(triage.budget, "reserve_request", reserve)
    finish = Mock()
    monkeypatch.setattr(triage.budget, "finish_request", finish)
    save = Mock(side_effect=lambda records, **kwargs: len(records))
    monkeypatch.setattr(triage.store, "save_if_current", save)
    track = Mock()
    monkeypatch.setattr(triage, "track_api_call", track)
    monkeypatch.setattr(triage, "check_tariff", Mock())
    def request(payload, api_key, timeout):
        assert reserve.called
        assert api_key == "test" and timeout == 45
        data=response(payload)
        data['usage'].update(prompt_tokens=123,completion_tokens=45)
        return {"status": "ok", "data": data}
    monkeypatch.setattr(triage, "_request", request)
    result = triage.run_triage_cycle(budget_usd=Decimal("3"), campaign="existing", max_calls=1)
    assert result["calls"] == 1 and result["saved"] == len(reserve.call_args.kwargs["pair_keys"])
    assert reserve.call_args.kwargs["reservation_usd"] == Decimal(".10")
    assert 1 <= len(reserve.call_args.kwargs["pair_keys"]) <= 8
    finish.assert_called_once_with("id", .0001, "ok")
    assert track.call_args.kwargs["model"] == triage.MODEL
    assert track.call_args.kwargs["service"] == triage.decision_model.SERVICE
    assert track.call_args.kwargs["endpoint"] == triage.decision_model.ENDPOINT
    assert track.call_args.kwargs['tokens_out']==45
    assert track.call_args.kwargs['estimate_missing_cost'] is False


def test_over_reservation_cost_does_not_save(monkeypatch):
    monkeypatch.setenv(triage.decision_model.KEY_ENV, "test")
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
        data["usage"]["cost"] = .12
        return {"status": "ok", "data": data}
    monkeypatch.setattr(triage, "_request", expensive)
    stats = triage.run_triage_cycle(budget_usd=Decimal("3"), campaign="existing")
    assert stats["saved"] == 0 and stats["invalid"] == 1
    save.assert_not_called()
    finish.assert_called_once_with("id", .12, "invalid_response")


def test_first_invalid_provider_batch_stops_before_second_reservation(monkeypatch):
    monkeypatch.setenv(triage.decision_model.KEY_ENV, "test")
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


def test_invalid_provider_response_still_charges_reported_overage(monkeypatch):
    monkeypatch.setenv(triage.decision_model.KEY_ENV, "test")
    monkeypatch.setattr(triage.store, "load_candidates", lambda **kwargs: ([article()], {"RS"}))
    monkeypatch.setattr(triage.budget, "get_attempted_pair_keys", lambda campaign: set())
    monkeypatch.setattr(triage.budget, "reserve_request", lambda *args, **kwargs: "id")
    finish = Mock()
    monkeypatch.setattr(triage.budget, "finish_request", finish)
    monkeypatch.setattr(triage, "track_api_call", Mock())
    monkeypatch.setattr(triage, "check_tariff", Mock())
    save = Mock()
    monkeypatch.setattr(triage.store, "save_if_current", save)
    monkeypatch.setattr(triage, "_request", lambda *args: {
        "status": "invalid_response", "usage": {"cost": .12, "prompt_tokens": 200}})
    stats = triage.run_triage_cycle(budget_usd=Decimal("3"), campaign="existing")
    assert stats["status"] == "partial" and stats["saved"] == 0
    finish.assert_called_once_with("id", .12, "invalid_response")
    save.assert_not_called()


def test_confident_secondary_cannot_rescue_low_confidence_primary():
    payload, selected = triage.prepare_payload([article()], {"RS"})
    data = response(payload, country_confidence=.4)
    key = "article_1_country_secondary"
    data["answers"][key] = answer("RS", payload["questions"][key]["criteria"])
    records, _ = triage.parse_response(data, selected, {"RS"})
    tags = records[0]["classification"]
    assert tags["country_primary"] == "unknown"
    assert tags["country_secondary"] == "RS"
    assert tags["countries"] == []
    assert tags["uncertain"] is True


def test_chat_country_is_grounded_in_source_not_publisher_and_audit_keeps_provenance():
    row = article(title="Moldova and Russia discuss visas", excerpt="Moldova discussed visas with Russia.", country_code="US")
    payload, selected = triage.prepare_payload([row], {"MD", "US", "RS"})
    assert set(payload["questions"]["article_1_country_primary"]["criteria"]) == {"MD", "US", "RS", "none", "unknown"}
    records, cost = triage.parse_response(chat_response(payload), selected, {"MD", "US", "RS"})
    tags = records[0]["classification"]
    assert tags["countries"] == ["MD"]
    assert tags["country_primary"] == "MD"
    assert tags["decisions"]["country_primary"] == {
        "type": "choice", "choice": "MD", "confidence": .8, "confidence_kind": "self_reported",
        "evidence": [{"article_id": "article_1", "quote": "Moldova"}],
        "probabilities": {}}
    assert cost == .001


def test_chat_country_without_exact_country_evidence_becomes_unknown():
    row = article(title="Russia discusses visas", excerpt="Officials discussed visas with Russia.", country_code="US")
    payload, selected = triage.prepare_payload([row], {"US", "RS"})
    data = chat_response(payload, country="US", country_quote="Russia")
    records, _ = triage.parse_response(data, selected, {"US", "RS"})
    tags = records[0]["classification"]
    assert tags["country_primary"] == "unknown"
    assert tags["countries"] == []
    assert tags["decisions"]["country_primary"]["choice"] == "US"


def test_chat_low_confidence_country_is_unknown_without_probabilities():
    row = article(title="Moldova and Russia discuss visas", excerpt="Moldova discussed visas with Russia.", country_code="US")
    payload, selected = triage.prepare_payload([row], {"MD", "US"})
    records, _ = triage.parse_response(chat_response(payload, confidence=.4), selected, {"MD", "US"})
    assert records[0]["classification"]["country_primary"] == "unknown"


@pytest.mark.parametrize('quote', ['Chișinău', 'La Moldavie', 'Кишинёв'])
def test_local_country_name_or_city_stays_a_provisional_review_lead(quote):
    row = article(title=quote + ' discusses cooperation', excerpt=quote)
    payload, selected = triage.prepare_payload([row], {'MD', 'US'})
    data = chat_response(payload, country='MD', country_quote=quote, confidence=.95)
    for value in data['answers'].values():
        for item in value['evidence']:
            item['quote'] = quote
    tags = triage.parse_response(data, selected, {'MD', 'US'})[0][0]['classification']
    assert tags['countries'] == ['MD']
    assert tags['uncertain'] is True
    assert tags['country_evidence_unverified'] == ['MD']


def test_russia_only_quote_cannot_establish_another_country_even_at_high_confidence():
    row = article(title='Russia discusses visas', excerpt='Russia discusses visas')
    payload, selected = triage.prepare_payload([row], {'US'})
    data = chat_response(payload, country='US', country_quote='Russia', confidence=.99)
    for value in data['answers'].values():
        for item in value['evidence']:
            item['quote'] = 'Russia'
    tags = triage.parse_response(data, selected, {'US'})[0][0]['classification']
    assert tags['countries'] == []
    assert tags['country_primary'] == 'unknown'


def test_chat_negative_relation_accepts_empty_evidence():
    row = article(title="Moldova discusses visas", excerpt="Moldova discussed visas.")
    payload, selected = triage.prepare_payload([row], {"MD"})
    data = chat_response(payload)
    for answer_value in data["answers"].values():
        for item in answer_value["evidence"]:
            item["quote"] = "Moldova"
    data["answers"]["article_1_relation"]["choice"] = "none"
    data["answers"]["article_1_relation"]["evidence"] = []
    records, _ = triage.parse_response(data, selected, {"MD"})
    assert records[0]["classification"]["russia_relation"] == "none"


def test_chat_evidence_must_quote_the_same_article_even_for_topic():
    row = article(title="Moldova and Russia discuss visas", excerpt="Moldova discussed visas with Russia.")
    payload, selected = triage.prepare_payload([row], {"MD"})
    data = chat_response(payload)
    data["answers"]["article_1_topic"]["evidence"] = [{"article_id": "article_2", "quote": "Russia"}]
    with pytest.raises(ValueError, match="evidence"):
        triage.parse_response(data, selected, {"MD"})
    data["answers"]["article_1_topic"]["evidence"] = [{"article_id": "article_1", "quote": "invented quote"}]
    with pytest.raises(ValueError, match="evidence"):
        triage.parse_response(data, selected, {"MD"})


def test_chat_topic_and_actor_without_evidence_abstain():
    row = article(title="Moldova and Russia discuss visas", excerpt="Moldova discussed visas with Russia.")
    payload, selected = triage.prepare_payload([row], {"MD"})
    data = chat_response(payload)
    data["answers"]["article_1_topic"]["evidence"] = []
    data["answers"]["article_1_actor_type"]["evidence"] = []
    data["answers"]["article_1_topic"]["confidence"] = .4
    data["answers"]["article_1_actor_type"]["confidence"] = .4
    records, _ = triage.parse_response(data, selected, {"MD"})
    assert records[0]["classification"]["topic"] == "other"
    assert records[0]["classification"]["actor_type"] == "other"
