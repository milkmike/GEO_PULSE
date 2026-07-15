from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.engine.explanations import (
    RriPoint,
    TemperatureArticleInput,
    build_explanation,
    compute_explanation_input_hash,
    decompose_rri_shift,
    estimate_event_contribution,
    load_explanation_from_session,
    persist_explanation_cache,
)


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def rri_point(
    *,
    time: datetime,
    score: float,
    structural: float,
    media: float | None,
    boost: float,
    structural_weight: float = 0.45,
    media_weight: float = 0.55,
    media_source: str = "temperature",
    bound_rule: str | None = None,
) -> RriPoint:
    details = {
        "weights": {
            "structural": structural_weight,
            "media": media_weight,
        },
        "media": {"source": media_source},
    }
    if bound_rule:
        details["bound_rule"] = bound_rule
    return RriPoint(
        country_code="KZ",
        time=time,
        score=score,
        structural=structural,
        media=media,
        boost=boost,
        version="v1",
        article_count=3,
        gdelt_volume=None,
        details=details,
    )


def test_exact_decomposition_keeps_rounding_residual_separate_from_components():
    previous = rri_point(
        time=NOW - timedelta(hours=24),
        score=10.0,
        structural=20.0,
        media=0.0,
        boost=1.0,
    )
    current = rri_point(
        time=NOW,
        score=16.31,
        structural=22.0,
        media=8.0,
        boost=2.0,
    )

    exact = decompose_rri_shift(previous, current)

    assert exact["total_delta"] == 6.31
    assert exact["structural_delta"] == 0.9
    assert exact["media_delta"] == 4.4
    assert exact["boost_delta"] == 1.0
    assert exact["exact_subtotal"] == 6.3
    assert exact["rounding_residual"] == 0.01
    assert exact["from_time"] == previous.time.isoformat()
    assert exact["to_time"] == current.time.isoformat()


def test_context_never_changes_the_exact_subtotal():
    previous = rri_point(
        time=NOW - timedelta(hours=24),
        score=10.0,
        structural=20.0,
        media=0.0,
        boost=1.0,
    )
    current = rri_point(
        time=NOW,
        score=16.31,
        structural=22.0,
        media=8.0,
        boost=2.0,
    )
    without_context = build_explanation(previous, current, context=[])
    with_context = build_explanation(
        previous,
        current,
        context=[{
            "scope": "article",
            "id": 99,
            "why_included": "published_in_selected_window",
            "relevance_score": 1.0,
            "confidence": 0.5,
            "evidence": {"temporal_only": True},
        }],
    )

    assert with_context["exact_changes"] == without_context["exact_changes"]
    assert with_context["exact_changes"]["exact_subtotal"] == 6.3
    assert with_context["context"][0]["scope"] == "article"


def test_bound_adjustment_is_not_mislabelled_as_rounding_residual():
    previous = rri_point(
        time=NOW - timedelta(hours=24),
        score=16.3,
        structural=22.0,
        media=8.0,
        boost=2.0,
    )
    current = rri_point(
        time=NOW,
        score=-75.0,
        structural=100.0,
        media=100.0,
        boost=15.0,
    )
    current = replace(
        current,
        details={
            **current.details,
            "bound_rule": "war_cap",
        },
    )

    exact = decompose_rri_shift(previous, current)

    assert exact["exact_subtotal"] == 98.7
    assert exact["calculation_adjustment_delta"] == -190.0
    assert exact["rounding_residual"] == 0.0
    assert (
        exact["exact_subtotal"]
        + exact["calculation_adjustment_delta"]
        + exact["rounding_residual"]
        == exact["total_delta"]
    )


def test_no_media_points_use_only_structural_contribution():
    previous = rri_point(
        time=NOW - timedelta(hours=24),
        score=10.0,
        structural=10.0,
        media=None,
        boost=5.0,
        structural_weight=1.0,
        media_weight=0.0,
        media_source="none",
    )
    current = rri_point(
        time=NOW,
        score=12.0,
        structural=12.0,
        media=None,
        boost=10.0,
        structural_weight=1.0,
        media_weight=0.0,
        media_source="none",
    )

    exact = decompose_rri_shift(previous, current)

    assert exact["total_delta"] == 2.0
    assert exact["structural_delta"] == 2.0
    assert exact["media_delta"] == 0.0
    assert exact["boost_delta"] == 0.0
    assert exact["exact_subtotal"] == 2.0
    assert exact["calculation_adjustment_delta"] == 0.0
    assert exact["rounding_residual"] == 0.0


@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        pytest.param(
            rri_point(
                time=NOW - timedelta(hours=24),
                score=10.0,
                structural=10.0,
                media=None,
                boost=5.0,
                structural_weight=1.0,
                media_weight=0.0,
                media_source="none",
            ),
            rri_point(
                time=NOW,
                score=25.0,
                structural=12.0,
                media=20.0,
                boost=3.0,
                bound_rule="union_state_floor",
            ),
            {
                "total_delta": 15.0,
                "structural_delta": -4.6,
                "media_delta": 11.0,
                "boost_delta": 3.0,
                "exact_subtotal": 9.4,
                "calculation_adjustment_delta": 5.6,
                "rounding_residual": 0.0,
            },
            id="no-media-to-media",
        ),
        pytest.param(
            rri_point(
                time=NOW - timedelta(hours=24),
                score=19.4,
                structural=12.0,
                media=20.0,
                boost=3.0,
            ),
            rri_point(
                time=NOW,
                score=25.0,
                structural=14.0,
                media=None,
                boost=10.0,
                structural_weight=1.0,
                media_weight=0.0,
                media_source="none",
                bound_rule="union_state_floor",
            ),
            {
                "total_delta": 5.6,
                "structural_delta": 8.6,
                "media_delta": -11.0,
                "boost_delta": -3.0,
                "exact_subtotal": -5.4,
                "calculation_adjustment_delta": 11.0,
                "rounding_residual": 0.0,
            },
            id="media-to-no-media",
        ),
    ],
)
def test_media_availability_transitions_follow_the_stored_formula_branch(
    previous,
    current,
    expected,
):
    exact = decompose_rri_shift(previous, current)

    assert {
        field: exact[field]
        for field in expected
    } == expected


def test_counterfactual_event_contribution_is_explicitly_estimated():
    articles = [
        TemperatureArticleInput(
            article_id=1,
            event_key="summit",
            sentiment=2.0,
            published_at=NOW - timedelta(hours=1),
            source_weight=1.0,
            event_type="diplomatic",
            action_level=3,
            reprint_count=0,
        ),
        TemperatureArticleInput(
            article_id=2,
            event_key="summit",
            sentiment=1.0,
            published_at=NOW - timedelta(hours=2),
            source_weight=0.8,
            event_type="diplomatic",
            action_level=2,
            reprint_count=1,
        ),
        TemperatureArticleInput(
            article_id=3,
            event_key="trade",
            sentiment=-1.0,
            published_at=NOW - timedelta(hours=3),
            source_weight=1.0,
            event_type="economic",
            action_level=1,
            reprint_count=0,
        ),
    ]

    contribution = estimate_event_contribution(
        event_key="summit",
        articles=articles,
        at=NOW,
        media_weight=0.55,
    )

    assert contribution["status"] == "estimated"
    assert contribution["label"] == "estimated"
    assert contribution["method"] == "counterfactual_event_cluster_removal_v1"
    assert contribution["input_article_ids"] == [1, 2, 3]
    assert contribution["removed_article_ids"] == [1, 2]
    assert contribution["estimated_delta"] != 0
    assert 0 <= contribution["confidence"] <= 1
    assert contribution["why_included"] == (
        "event_cluster_present_in_reconstructed_temperature_window"
    )
    assert contribution["relevance_score"] == pytest.approx(2 / 3, abs=1e-3)
    assert contribution["evidence"] == {
        "input_article_ids": [1, 2, 3],
        "removed_article_ids": [1, 2],
        "relevance_basis": "share_of_temperature_article_inputs",
    }


def test_counterfactual_is_omitted_with_reason_when_article_inputs_are_missing():
    contribution = estimate_event_contribution(
        event_key="summit",
        articles=[],
        at=NOW,
        media_weight=0.55,
    )

    assert contribution == {
        "status": "omitted",
        "label": "estimated",
        "method": "counterfactual_event_cluster_removal_v1",
        "event_key": "summit",
        "input_article_ids": [],
        "removed_article_ids": [],
        "why_included": "counterfactual_requested_for_event_cluster",
        "relevance_score": None,
        "confidence": None,
        "evidence": {
            "input_article_ids": [],
            "removed_article_ids": [],
            "relevance_basis": "share_of_temperature_article_inputs",
        },
        "reason": "article_inputs_missing",
    }


def test_counterfactual_uses_the_engine_raw_event_key_length_gate():
    articles = [
        TemperatureArticleInput(
            article_id=1,
            event_key="   x",
            sentiment=2.0,
            published_at=NOW,
            source_weight=1.0,
            event_type="diplomatic",
            action_level=2,
            reprint_count=0,
        ),
        TemperatureArticleInput(
            article_id=2,
            event_key="   x",
            sentiment=1.0,
            published_at=NOW,
            source_weight=0.8,
            event_type="diplomatic",
            action_level=1,
            reprint_count=0,
        ),
        TemperatureArticleInput(
            article_id=3,
            event_key="other",
            sentiment=-1.0,
            published_at=NOW,
            source_weight=1.0,
            event_type="economic",
            action_level=1,
            reprint_count=0,
        ),
    ]

    contribution = estimate_event_contribution(
        event_key="   x",
        articles=articles,
        at=NOW,
        media_weight=0.55,
    )

    assert contribution["status"] == "estimated"
    assert contribution["removed_article_ids"] == [1, 2]


def test_explanation_input_hash_binds_country_times_version_and_inputs():
    payload = {"point_ids": ["KZ:from", "KZ:to"], "article_ids": [1, 2]}

    first = compute_explanation_input_hash(
        country_code="KZ",
        from_time=NOW - timedelta(hours=24),
        to_time=NOW,
        rri_version="v1",
        inputs=payload,
    )
    reordered = compute_explanation_input_hash(
        country_code="KZ",
        from_time=NOW - timedelta(hours=24),
        to_time=NOW,
        rri_version="v1",
        inputs={"article_ids": [1, 2], "point_ids": ["KZ:from", "KZ:to"]},
    )
    changed_version = compute_explanation_input_hash(
        country_code="KZ",
        from_time=NOW - timedelta(hours=24),
        to_time=NOW,
        rri_version="v2",
        inputs=payload,
    )
    equivalent_offsets = compute_explanation_input_hash(
        country_code="KZ",
        from_time=(NOW - timedelta(hours=24)).astimezone(
            timezone(timedelta(hours=3))
        ),
        to_time=NOW.astimezone(timezone(timedelta(hours=3))),
        rri_version="v1",
        inputs=payload,
    )

    assert len(first) == 64
    assert reordered == first
    assert equivalent_offsets == first
    assert changed_version != first


class FakeResult:
    def __init__(self, *, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class CachedExplanationSession:
    def __init__(self):
        self.calls = []
        self.previous = SimpleNamespace(
            country_code="KZ",
            time=NOW - timedelta(hours=24),
            score=10,
            structural=20,
            media=0,
            boost=1,
            version="v1",
            article_count=2,
            gdelt_volume=None,
            details={"weights": {"structural": 0.45, "media": 0.55}},
        )
        self.current = SimpleNamespace(
            country_code="KZ",
            time=NOW,
            score=16.3,
            structural=22,
            media=8,
            boost=2,
            version="v1",
            article_count=3,
            gdelt_volume=None,
            details={"weights": {"structural": 0.45, "media": 0.55}},
        )

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        self.calls.append((sql, params))
        if "FROM ru_index" in sql:
            point = self.previous if params["target_time"] < NOW else self.current
            return FakeResult(row=point)
        if "FROM analysis a" in sql:
            return FakeResult(rows=[])
        if "AS context_scope" in sql:
            return FakeResult(rows=[])
        if "FROM index_change_explanations" in sql:
            return FakeResult(row=SimpleNamespace(
                exact_changes={"total_delta": 6.3, "exact_subtotal": 6.3},
                estimated_contributions=[],
                context=[],
                evidence_completeness="complete",
                limitations=["cached limitation"],
            ))
        raise AssertionError(sql)


class ArticleInputCacheSession(CachedExplanationSession):
    def __init__(self, sentiment, *, cache_hit=True):
        super().__init__()
        self.sentiment = sentiment
        self.cache_hit = cache_hit
        self.current.details = {
            **self.current.details,
            "media": {"source": "temperature"},
        }

    def execute(self, statement, params=None):
        sql = str(statement)
        params = params or {}
        if "FROM analysis a" in sql:
            self.calls.append((sql, params))
            return FakeResult(rows=[SimpleNamespace(
                article_id=1,
                event_key="summit",
                sentiment=self.sentiment,
                published_at=NOW - timedelta(hours=1),
                source_weight=1.0,
                event_type="diplomatic",
                action_level=3,
                reprint_count=0,
            ), SimpleNamespace(
                article_id=2,
                event_key="other",
                sentiment=-1.0,
                published_at=NOW - timedelta(hours=2),
                source_weight=0.8,
                event_type="economic",
                action_level=1,
                reprint_count=1,
            )])
        if "FROM index_change_explanations" in sql and not self.cache_hit:
            self.calls.append((sql, params))
            return FakeResult(row=None)
        return super().execute(statement, params)


def test_loader_uses_full_migration_021_cache_key_without_writes():
    session = CachedExplanationSession()

    result = load_explanation_from_session(
        session,
        country_code="KZ",
        from_time=NOW - timedelta(hours=24),
        to_time=NOW,
        rri_version="v1",
    )
    assert result["cache"]["status"] == "hit"
    assert result["exact_changes"]["total_delta"] == 6.3
    cache_sql, cache_params = next(
        call for call in session.calls if "FROM index_change_explanations" in call[0]
    )
    assert "country_code = :country_code" in cache_sql
    assert "from_time = :from_time" in cache_sql
    assert "to_time = :to_time" in cache_sql
    assert "rri_version = :rri_version" in cache_sql
    assert "input_hash = :input_hash" in cache_sql
    assert cache_params["country_code"] == "KZ"
    assert cache_params["from_time"] == NOW - timedelta(hours=24)
    assert cache_params["to_time"] == NOW
    assert cache_params["rri_version"] == "v1"
    assert len(cache_params["input_hash"]) == 64
    assert not any(
        token in sql.upper()
        for sql, _ in session.calls
        for token in ("INSERT ", "UPDATE ", "DELETE ")
    )


class CacheWriterSession:
    def __init__(self):
        self.calls = []

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return FakeResult()


def test_background_cache_writer_uses_the_full_migration_021_conflict_key():
    session = CacheWriterSession()
    payload = explanation_payload()
    payload.update({
        "country_code": "KZ",
        "from_time": (NOW - timedelta(hours=24)).isoformat(),
        "to_time": NOW.isoformat(),
        "rri_version": "v1",
        "cache": {"status": "miss", "input_hash": "b" * 64},
    })

    persist_explanation_cache(session, payload)

    sql, params = session.calls[0]
    assert "INSERT INTO index_change_explanations" in sql
    assert (
        "ON CONFLICT (country_code, from_time, to_time, rri_version, input_hash)"
        in sql
    )
    assert params["country_code"] == "KZ"
    assert params["from_time"] == NOW - timedelta(hours=24)
    assert params["to_time"] == NOW
    assert params["rri_version"] == "v1"
    assert params["input_hash"] == "b" * 64

def test_cache_hash_changes_when_article_analysis_changes_for_the_same_id():
    first = ArticleInputCacheSession(sentiment=1.0)
    changed = ArticleInputCacheSession(sentiment=2.0)

    for session in (first, changed):
        load_explanation_from_session(
            session,
            country_code="KZ",
            from_time=NOW - timedelta(hours=24),
            to_time=NOW,
            rri_version="v1",
        )

    first_hash = next(
        params["input_hash"]
        for sql, params in first.calls
        if "FROM index_change_explanations" in sql
    )
    changed_hash = next(
        params["input_hash"]
        for sql, params in changed.calls
        if "FROM index_change_explanations" in sql
    )
    assert changed_hash != first_hash


def test_counterfactual_loader_discloses_historical_input_reconstruction_limit():
    session = ArticleInputCacheSession(sentiment=1.0, cache_hit=False)
    session.previous.time = NOW - timedelta(hours=36)
    requested_from = NOW - timedelta(hours=30)

    result = load_explanation_from_session(
        session,
        country_code="KZ",
        from_time=requested_from,
        to_time=NOW,
        rri_version="v1",
    )

    assert result["from_time"] == requested_from.isoformat()
    assert result["exact_changes"]["from_time"] == session.previous.time.isoformat()
    assert result["evidence_completeness"] == "complete"
    assert (
        "counterfactual_reconstructs_media_window_not_historical_input_snapshot"
        in result["limitations"]
    )
    assert not any(
        token in sql.upper()
        for sql, _ in session.calls
        for token in ("INSERT ", "UPDATE ", "DELETE ")
    )


def explanation_payload():
    previous = rri_point(
        time=NOW - timedelta(hours=24),
        score=10.0,
        structural=20.0,
        media=0.0,
        boost=1.0,
    )
    current = rri_point(
        time=NOW,
        score=16.3,
        structural=22.0,
        media=8.0,
        boost=2.0,
    )
    payload = build_explanation(
        previous,
        current,
        estimated_contributions=[{
            "status": "estimated",
            "label": "estimated",
            "method": "counterfactual_event_cluster_removal_v1",
            "event_key": "summit",
            "input_article_ids": [1, 2],
            "removed_article_ids": [1],
            "estimated_delta": 1.2,
            "confidence": 0.7,
        }],
        context=[{
            "scope": "article",
            "id": 1,
            "url": "javascript:alert(1)",
            "why_included": "published_in_selected_window",
            "relevance_score": 1.0,
            "confidence": 0.7,
            "evidence": {"published_at": NOW.isoformat()},
        }],
    )
    payload["cache"] = {"status": "miss", "input_hash": "a" * 64}
    return payload


def investigation_client(monkeypatch):
    from src.api.routes import investigations

    observed = []

    def service(*, country_code, from_time, to_time, rri_version):
        observed.append({
            "country_code": country_code,
            "from_time": from_time,
            "to_time": to_time,
            "rri_version": rri_version,
        })
        return explanation_payload()

    app = FastAPI()
    app.include_router(investigations.router)
    app.dependency_overrides[investigations.get_explanation_service] = lambda: service
    return TestClient(app), observed


def test_index_explanation_endpoint_validates_window_forms_and_urls(monkeypatch):
    client, observed = investigation_client(monkeypatch)

    point = client.get(
        "/api/v2/countries/kz/index-explanation",
        params={"at": NOW.isoformat(), "window_hours": 24},
    )
    interval = client.get(
        "/api/v2/countries/KZ/index-explanation",
        params={
            "from": (NOW - timedelta(hours=12)).isoformat(),
            "to": NOW.isoformat(),
        },
    )
    mixed = client.get(
        "/api/v2/countries/KZ/index-explanation",
        params={
            "at": NOW.isoformat(),
            "from": (NOW - timedelta(hours=12)).isoformat(),
            "to": NOW.isoformat(),
        },
    )
    incomplete = client.get(
        "/api/v2/countries/KZ/index-explanation",
        params={"from": (NOW - timedelta(hours=12)).isoformat()},
    )
    reversed_interval = client.get(
        "/api/v2/countries/KZ/index-explanation",
        params={
            "from": NOW.isoformat(),
            "to": (NOW - timedelta(hours=12)).isoformat(),
        },
    )
    naive = client.get(
        "/api/v2/countries/KZ/index-explanation",
        params={"at": "2026-07-15T12:00:00"},
    )

    assert point.status_code == 200
    point_payload = point.json()
    assert point_payload["context"][0]["url"] is None
    assert point_payload["context"][0]["evidence"]
    assert point_payload["estimated_contributions"][0]["label"] == "estimated"
    assert point_payload["evidence_completeness"] == "complete"
    assert point_payload["limitations"] == ["contextual_proximity_is_not_causation"]
    assert observed[0] == {
        "country_code": "KZ",
        "from_time": NOW - timedelta(hours=24),
        "to_time": NOW,
        "rri_version": "v1",
    }
    assert interval.status_code == 200
    assert mixed.status_code == 422
    assert incomplete.status_code == 422
    assert reversed_interval.status_code == 422
    assert naive.status_code == 422


def test_methodology_endpoint_returns_plain_and_technical_layers(monkeypatch):
    client, _ = investigation_client(monkeypatch)

    response = client.get("/api/v2/methodology/temperature")

    assert response.status_code == 200
    payload = response.json()
    assert payload["plain_language"]
    assert payload["technical"]["window_days"] == 14
    assert payload["worked_example"]["temperature"] is not None


@pytest.mark.parametrize("url", [
    "https://-bad.example/story",
    "https://example.com:not-a-port/story",
    "https://exa mple.com/story",
    "javascript:alert(1)",
    "https://user:secret@example.com/story",
])
def test_investigation_urls_require_safe_http_hostnames(url):
    from src.api.routes.investigations import safe_public_url

    assert safe_public_url(url) is None


def test_investigation_url_validation_preserves_safe_http_url():
    from src.api.routes.investigations import safe_public_url

    assert (
        safe_public_url("https://news.example/story")
        == "https://news.example/story"
    )
