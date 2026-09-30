from decimal import Decimal
import copy

import pytest

from src import decision_verification as verify


@pytest.fixture(autouse=True)
def no_telemetry(monkeypatch):
    monkeypatch.setattr(verify, "track_api_call", lambda **kwargs: None)


def source():
    return {"id": 1727142, "title": "Ukraine Hoping SA Will Announce Progress On Returning Abducted Children",
            "excerpt": "President Cyril Ramaphosa has apparently spoken to Vladimir Putin about returning Ukrainian children taken to Russia."}


def proposed():
    return {"relevant": True, "headline_ru": "Президент ЮАР обсудил возвращение детей",
            "summary_ru": "Как сообщается, Сирил Рамафоса говорил с Путиным о возвращении украинских детей.",
            "russia_explanation_ru": "Речь о разговоре с Путиным.",
            "russia_evidence_quote": "Vladimir Putin", "kind": "position",
            "countries": [{"code": "ZA", "evidence_quote": "President Cyril Ramaphosa"}],
            "positions": [{"actor": "Сирил Рамафоса", "actor_type": "government",
                           "position_ru": "Как сообщается, говорил с Путиным о возвращении детей.",
                           "evidence_quote": "has apparently spoken to Vladimir Putin"}],
            "changes": [{"category": "other", "change_ru": "Дети будут возвращены.",
                         "evidence_quote": "returning Ukrainian children taken to Russia"}]}


def answer(choice="supported", confidence=.95, support=.96):
    return {"type": "choice", "choice": choice, "confidence": confidence,
            "probabilities": {"supported": support, "unsupported": 1-support, "insufficient_evidence": 0.0}}


def response(questions, overrides=None, cost=.0001):
    answers = {key: answer() for key in questions}
    answers.update(overrides or {})
    return {"answers": answers, "usage": {"cost": cost}}


def test_jev_prunes_speculative_change_but_keeps_supported_position():
    payload = verify.prepare_payload(source(), proposed())
    assert payload["state"]["proposed"]["kind"] == "position"
    assert "implemented decision" in payload["questions"]["kind"]["instructions"]
    assert set(payload["questions"]) == {"headline", "summary", "russia", "explanation", "kind", "country_0", "position_0", "change_0"}
    assert "Independently of the generated explanation" in payload["questions"]["russia"]["instructions"]
    decisions, cost = verify.parse_response(response(payload["questions"],
        {"change_0": answer("unsupported", support=.02)}), set(payload["questions"]))
    original = proposed()
    result = verify.project_verified(original, decisions)
    assert result is not None and result["countries"][0]["code"] == "ZA"
    assert len(result["positions"]) == 1 and result["changes"] == []
    assert len(original["changes"]) == 1
    assert cost == .0001


def test_jev_rejects_country_inferred_from_kaliningrad():
    claim = proposed() | {"countries": [
        {"code": "LT", "evidence_quote": "Kaliningrado"},
        {"code": "PL", "evidence_quote": "Kaliningrado"}]}
    payload = verify.prepare_payload(source(), claim)
    decisions, _ = verify.parse_response(response(payload["questions"], {
        "country_0": answer("unsupported", support=.01),
        "country_1": answer("insufficient_evidence", support=.02)}), set(payload["questions"]))
    assert verify.project_verified(claim, decisions) is None


def test_multiple_reviewed_countries_do_not_share_actor_or_change_claims():
    claim = proposed() | {"countries": [
        {"code": "ZA", "evidence_quote": "President Cyril Ramaphosa"},
        {"code": "UA", "evidence_quote": "Ukrainian children"}]}
    payload = verify.prepare_payload(source(), claim)
    decisions, _ = verify.parse_response(response(payload["questions"]), set(payload["questions"]))
    result = verify.project_verified(claim, decisions)
    assert result is not None and {c["code"] for c in result["countries"]} == {"ZA", "UA"}
    assert result["positions"] == [] and result["changes"] == []


def test_rejected_country_still_prevents_optional_claim_spill():
    claim = proposed() | {"countries": [
        {"code": "ZA", "evidence_quote": "President Cyril Ramaphosa"},
        {"code": "UA", "evidence_quote": "Ukrainian children"}]}
    payload = verify.prepare_payload(source(), claim)
    decisions, _ = verify.parse_response(response(payload["questions"],
        {"country_1": answer("unsupported", support=.01)}), set(payload["questions"]))
    result = verify.project_verified(claim, decisions)
    assert result is not None and [c["code"] for c in result["countries"]] == ["ZA"]
    assert result["positions"] == [] and result["changes"] == []


def test_low_support_or_weak_brief_abstains():
    payload = verify.prepare_payload(source(), proposed())
    for rejected in (answer(confidence=.79), answer(support=.89), answer("unsupported", support=.01)):
        decisions, _ = verify.parse_response(response(payload["questions"], {"headline": rejected}), set(payload["questions"]))
        assert verify.project_verified(proposed(), decisions) is None
    decisions, _ = verify.parse_response(response(payload["questions"],
        {"country_0": answer("insufficient_evidence", support=.03)}), set(payload["questions"]))
    assert verify.project_verified(proposed(), decisions) is None


def test_weak_kind_falls_back_to_other_without_losing_supported_facts():
    claim = proposed()
    payload = verify.prepare_payload(source(), claim)
    decisions, _ = verify.parse_response(response(payload["questions"],
        {"kind": answer("unsupported", support=.01)}), set(payload["questions"]))
    result = verify.project_verified(claim, decisions)
    assert result is not None and result["kind"] == "other"
    assert result["countries"] == claim["countries"]


def test_unsupported_summary_or_explanation_can_be_omitted_without_losing_evidence():
    claim = proposed()
    payload = verify.prepare_payload(source(), claim)
    decisions, _ = verify.parse_response(response(payload["questions"], {
        "summary": answer("unsupported", support=.01),
        "explanation": answer("insufficient_evidence", support=.04)}), set(payload["questions"]))
    result = verify.project_verified(claim, decisions)
    assert result is not None
    assert result["headline_ru"] == claim["headline_ru"]
    assert result["summary_ru"] == "" and result["russia_explanation_ru"] == ""
    assert result["russia_evidence_quote"] == claim["russia_evidence_quote"]


def test_maximum_claim_count_stays_within_one_bounded_request():
    article = {"id": 1, "title": "x" * 300, "excerpt": "x" * 4000}
    claim = proposed() | {
        "headline_ru": "т" * 180, "summary_ru": "т" * 240,
        "russia_explanation_ru": "т" * 180, "russia_evidence_quote": "x" * 200,
        "countries": [{"code": code, "evidence_quote": "x" * 200}
                      for code in ("RS", "ZA", "PL", "LT")],
        "positions": [{"actor": "А" * 120, "actor_type": "government",
                       "position_ru": "т" * 240, "evidence_quote": "x" * 200}] * 2,
        "changes": [{"category": "other", "change_ru": "т" * 240,
                     "evidence_quote": "x" * 200}] * 2,
    }
    payload = verify.prepare_payload(article, claim)
    assert len(payload["questions"]) == 13
    assert len(verify._encode(payload)) <= 24_000


@pytest.mark.parametrize("bad", [
    {"answers": {}, "usage": {"cost": .001}},
    {"answers": {"headline": {"type": "choice", "choice": "supported", "confidence": True,
                             "probabilities": {"supported": .99, "unsupported": .01, "insufficient_evidence": 0}}}},
])
def test_strict_response_rejects_missing_or_malformed_answers(bad):
    with pytest.raises(ValueError):
        verify.parse_response(bad, {"headline"})


def test_no_network_for_irrelevant_or_zero_budget(monkeypatch):
    monkeypatch.setattr(verify, "_request", lambda *args: pytest.fail("unexpected paid request"))
    assert verify.verify_annotation(source(), proposed() | {"relevant": False}, campaign="existing", budget_usd=Decimal("3")) is None
    assert verify.verify_annotation(source(), proposed(), campaign="existing", budget_usd=Decimal("0")) is None


def test_exhausted_budget_never_opens_paid_transport(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    monkeypatch.setattr(verify, "check_tariff", lambda: None)
    monkeypatch.setattr(verify.budget, "reserve_request", lambda *a, **kw: None)
    monkeypatch.setattr(verify, "_request", lambda *args: pytest.fail("paid request without reservation"))
    assert verify.verify_annotation(source(), proposed(), campaign="existing", budget_usd=Decimal("3")) is None


def test_one_successful_request_returns_pruned_claims_and_settles_cost(monkeypatch):
    calls = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    monkeypatch.setattr(verify, "check_tariff", lambda: None)
    monkeypatch.setattr(verify.budget, "reserve_request", lambda *a, **kw: calls.append("reserve") or "id")
    monkeypatch.setattr(verify.budget, "finish_request", lambda ident, cost, status: calls.append(("finish", cost, status)))
    def request(payload, key, timeout):
        calls.append("request")
        assert timeout == 5 and key == "test-only"
        return {"status": "ok", "data": response(payload["questions"],
            {"change_0": answer("unsupported", support=.01)}, cost=.0003)}
    monkeypatch.setattr(verify, "_request", request)
    result = verify.verify_annotation(source(), proposed(), campaign="existing", budget_usd=Decimal("3"))
    assert result is not None and result["changes"] == [] and len(result["positions"]) == 1
    assert calls == ["reserve", "request", ("finish", .0003, "ok")]


def test_over_reservation_cost_abstains_and_passes_raw_cost_to_budget(monkeypatch):
    calls = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    monkeypatch.setattr(verify, "check_tariff", lambda: None)
    monkeypatch.setattr(verify.budget, "reserve_request", lambda *a, **kw: "id")
    monkeypatch.setattr(verify.budget, "finish_request", lambda ident, cost, status: calls.append((cost, status)))
    monkeypatch.setattr(verify, "_request", lambda payload, key, timeout: {
        "status": "ok", "data": response(payload["questions"], cost=.11)})
    assert verify.verify_annotation(source(), proposed(), campaign="existing", budget_usd=Decimal("3")) is None
    assert calls == [(.11, "invalid_response")]


def test_reserves_before_single_request_and_settles_unknown_failure(monkeypatch):
    calls = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-only")
    monkeypatch.setattr(verify, "check_tariff", lambda: None)
    monkeypatch.setattr(verify.budget, "reserve_request", lambda *a, **kw: calls.append("reserve") or "id")
    monkeypatch.setattr(verify.budget, "finish_request", lambda ident, cost, status: calls.append(("finish", cost, status)))
    monkeypatch.setattr(verify, "_request", lambda *args: calls.append("request") or {"status": "timeout"})
    original = copy.deepcopy(proposed())
    assert verify.verify_annotation(source(), original, campaign="existing", budget_usd=Decimal("3")) is None
    assert original == proposed()
    assert calls == ["reserve", "request", ("finish", None, "timeout")]


def test_unfinished_excerpt_tail_never_substantiates_optional_claim_even_if_reviewer_accepts():
    article={'id':1,'title':'Gas deal with Serbia extended','excerpt':'Russia extended gas supplies to Serbia on the same t'}
    claim=proposed() | {'countries':[{'code':'RS','evidence_quote':'Serbia'}],
        'changes':[{'category':'work','change_ru':'Условия Газпрома остались прежними','evidence_quote':article['excerpt']}]}
    payload=verify.prepare_payload(article,claim)
    decisions,_=verify.parse_response(response(payload['questions']),set(payload['questions']))
    result=verify.project_verified(claim,decisions,article=article)
    assert result is not None and result['changes']==[]
