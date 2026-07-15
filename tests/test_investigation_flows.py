from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routes import investigations as investigation_routes
from src.api.routes import signal_detail as signal_routes
from src.api.routes import stories as story_routes
from src.api.routes.search import get_search_service
from src.stories import StoryCandidate


NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _StorySession:
    def __init__(self, row):
        self.row = row
        self.statements: list[str] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.statements.append(sql)
        assert sql.lstrip().upper().startswith("SELECT")
        return _Rows([self.row])


@contextmanager
def _session_context(session):
    yield session


def _story_row():
    return SimpleNamespace(
        id=71,
        slug="russia-spain-port-talks",
        title_ru="Россия и Испания обсуждают портовую логистику",
        title_en=None,
        summary="Один сюжет объединяет публикации испанских и российских источников.",
        lifecycle="developing",
        first_seen=NOW - timedelta(days=1),
        last_seen=NOW,
        article_count=4,
        source_count=3,
        country_count=2,
        highest_action_level=3,
        clustering_confidence=0.84,
        generated_at=NOW,
        meta={"topics": ["diplomacy"], "thread_ids": [10, 20]},
        relevance_score=0.83,
        countries=["ES", "RU"],
        primary_url="https://elpais.com/mundo/rusia-espana",
    )


class _SearchService:
    def __call__(self, query):
        assert query.q == "путин"
        assert query.country == "ES"
        return {
            "items": [
                {
                    "article_id": 501,
                    "title": "Putin, mencionado en un debate sobre España",
                    "summary": "La publicación enlaza la mención con la política española.",
                    "url": "https://elpais.com/internacional/putin-espana",
                    "published_at": NOW.isoformat(),
                    "language": "es",
                    "source": {"name": "El País", "country": "ES", "tier": "mainstream"},
                    "topics": ["diplomacy"],
                    "matched_entities": [{"id": "entity-putin", "name": "Владимир Путин"}],
                    "sentiment": -0.3,
                    "action_level": 2,
                    "story": None,
                    "why_included": "Точное упоминание сущности «Владимир Путин»",
                    "relevance_score": 0.91,
                    "confidence": 0.95,
                    "evidence": [{"type": "entity_mention", "article_id": 501}],
                    "scores": {
                        "lexical": 0.8,
                        "entity": 1.0,
                        "topic": 0.0,
                        "freshness": 0.9,
                        "trust": 0.9,
                        "story": 0.0,
                        "vector": None,
                    },
                }
            ],
            "candidate_count": 1,
            "next_cursor": None,
        }


def _signal_detail(*, signal_id: int):
    assert signal_id == 9
    return {
        "id": 9,
        "type": "index_shift",
        "severity": "warning",
        "summary": {
            "headline": "Сдвиг индекса Испании",
            "description": "RRI изменился на 8 пунктов.",
            "what_changed": "Медийная компонента стала холоднее.",
        },
        "rule": {
            "detector": "index_shift",
            "version": "1.0",
            "description": "Сдвиг RRI от 7 до 18 пунктов.",
            "threshold": {"absolute_delta_min": 7.0},
        },
        "values": {
            "observed": {"delta_24h": -8.0},
            "baseline": {"score": 12.0},
            "window": {
                "start": (NOW - timedelta(days=1)).isoformat(),
                "end": NOW.isoformat(),
            },
        },
        "chart_points": [],
        "articles": [{"id": 501, "url": "https://elpais.com/internacional/putin-espana"}],
        "articles_page": {"total": 1, "returned": 1, "limit": 100, "truncated": False, "has_more": False},
        "related_story": {"id": 71, "slug": "russia-spain-port-talks"},
        "countries": [{"code": "ES", "name": "Испания"}],
        "state": {"active": True, "status": "active", "created_at": NOW.isoformat(), "expires_at": (NOW + timedelta(days=1)).isoformat()},
        "confidence": 0.8,
        "evidence_completeness": "complete",
        "evidence_ids": ["rri:ES:2026-07-15T12:00:00+00:00"],
        "evidence": {"article_ids": [501], "story_ids": [71], "rri_points": []},
        "evidence_truncation": {},
        "limitations": ["Контекст не доказывает причинность."],
    }


def _explanation(**kwargs):
    assert kwargs["country_code"] == "ES"
    return {
        "country_code": "ES",
        "from_time": kwargs["from_time"].isoformat(),
        "to_time": kwargs["to_time"].isoformat(),
        "rri_version": kwargs["rri_version"],
        "exact_changes": {
            "total_delta": -8.0,
            "structural_delta": 0.0,
            "media_delta": -8.0,
            "boost_delta": 0.0,
        },
        "estimated_contributions": [
            {
                "label": "estimated",
                "status": "estimated",
                "event_key": "портовые переговоры",
                "estimated_delta": -3.1,
            }
        ],
        "context": [
            {
                "scope": "article",
                "id": 501,
                "url": "https://elpais.com/internacional/putin-espana",
                "why_included": "published_in_selected_window",
            }
        ],
        "related_story_ids": [71],
        "related_signal_ids": [9],
        "evidence_completeness": "complete",
        "limitations": ["contextual_proximity_is_not_causation"],
        "cache": {"status": "hit", "input_hash": "a" * 64},
    }


@pytest.fixture
def investigation_client(monkeypatch):
    story_session = _StorySession(_story_row())
    monkeypatch.setattr(story_routes, "get_session", lambda: _session_context(story_session))
    app.dependency_overrides[get_search_service] = lambda: _SearchService()
    app.dependency_overrides[signal_routes.get_signal_detail_service] = lambda: SimpleNamespace(
        detail=_signal_detail
    )
    app.dependency_overrides[investigation_routes.get_explanation_service] = lambda: _explanation
    try:
        yield TestClient(app), story_session
    finally:
        app.dependency_overrides.clear()


def test_putin_in_spain_search_returns_the_source_article(investigation_client):
    client, _ = investigation_client

    response = client.get(
        "/api/v2/search/articles",
        params={"q": "Путин", "country": "ES"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["semantic_search"] == "unavailable"
    assert payload["items"][0]["source"]["country"] == "ES"
    assert payload["items"][0]["url"] == "https://elpais.com/internacional/putin-espana"
    assert payload["items"][0]["matched_entities"][0]["name"] == "Владимир Путин"


def test_cross_country_story_is_visible_globally_and_in_a_country_slice(
    investigation_client,
):
    client, _ = investigation_client

    global_payload = client.get("/api/v2/stories").json()
    country_payload = client.get("/api/v2/countries/ES/stories").json()

    assert global_payload["stories"][0]["countries"] == ["ES", "RU"]
    assert global_payload["stories"][0]["why_included"][0] == "cross_country"
    assert country_payload["country"] == "ES"
    assert country_payload["stories"][0]["id"] == 71


def test_signal_explanation_and_methodology_keep_evidence_categories_separate(
    investigation_client,
):
    client, _ = investigation_client

    signal = client.get("/api/v2/signals/9").json()
    explanation = client.get(
        "/api/v2/countries/ES/index-explanation",
        params={"at": NOW.isoformat(), "window_hours": 24},
    ).json()
    methodology = client.get("/api/v2/methodology/temperature").json()

    assert signal["rule"]["threshold"] == {"absolute_delta_min": 7.0}
    assert signal["articles"][0]["url"].startswith("https://")
    assert explanation["exact_changes"]["media_delta"] == -8.0
    assert explanation["estimated_contributions"][0]["label"] == "estimated"
    assert explanation["context"][0]["scope"] == "article"
    assert explanation["limitations"] == ["contextual_proximity_is_not_causation"]
    assert methodology["methodology_version"] == "temperature-v1"
    assert methodology["technical"]["window_days"] == 14
    assert methodology["technical"]["action_level_weights"]["6"] == 15


def test_public_investigation_gets_do_not_call_providers_or_write(
    investigation_client,
    monkeypatch,
):
    client, story_session = investigation_client

    def forbidden(*args, **kwargs):
        raise AssertionError("public GET attempted a provider call")

    monkeypatch.setattr("src.llm.chat", forbidden)
    monkeypatch.setattr("src.embeddings.generate_embedding", forbidden)
    monkeypatch.setattr("src.embeddings.generate_embeddings_batch", forbidden)

    requests = (
        ("/api/v2/search/articles", {"q": "Путин", "country": "ES"}),
        ("/api/v2/stories", {}),
        ("/api/v2/countries/ES/stories", {}),
        ("/api/v2/signals/9", {}),
        (
            "/api/v2/countries/ES/index-explanation",
            {"at": NOW.isoformat(), "window_hours": 24},
        ),
        ("/api/v2/methodology/temperature", {}),
    )
    assert all(client.get(path, params=params).status_code == 200 for path, params in requests)
    assert story_session.statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in story_session.statements)


def test_default_search_and_signal_services_are_local_read_only(
    monkeypatch,
):
    from src.api.routes.signal_detail import SqlSignalDetailService
    from src.search import SearchQuery, search_articles

    def forbidden(*args, **kwargs):
        raise AssertionError("read service attempted a provider call")

    monkeypatch.setattr("src.llm.chat", forbidden)
    monkeypatch.setattr("src.embeddings.generate_embedding", forbidden)
    monkeypatch.setattr("src.embeddings.generate_embeddings_batch", forbidden)

    class EmptyResult:
        def fetchall(self):
            return []

        def fetchone(self):
            return None

    class ReadOnlySession:
        def __init__(self):
            self.statements = []

        def execute(self, statement, params=None):
            sql = str(statement).strip()
            self.statements.append(sql)
            assert not any(
                token in sql.upper()
                for token in ("INSERT ", "UPDATE ", "DELETE ")
            )
            return EmptyResult()

    search_session = ReadOnlySession()

    @contextmanager
    def search_session_factory():
        yield search_session

    page = search_articles(
        SearchQuery(q="путин", country="ES"),
        session_factory=search_session_factory,
        now=NOW,
    )

    signal_session = ReadOnlySession()
    monkeypatch.setattr(
        signal_routes,
        "get_session",
        lambda: _session_context(signal_session),
    )
    detail = SqlSignalDetailService().detail(signal_id=999)

    assert page["items"] == []
    assert page["candidate_count"] == 0
    assert detail is None
    assert search_session.statements
    assert signal_session.statements


def test_backfill_cli_is_dry_run_by_default_and_apply_is_explicit():
    from scripts.backfill_investigation_data import build_parser

    assert build_parser().parse_args([]).apply is False
    assert build_parser().parse_args(["--apply"]).apply is True


def test_backfill_command_is_packaged_in_the_analyzer_image():
    dockerfile = (ROOT / "Dockerfile.analyzer").read_text(encoding="utf-8")

    assert "scripts/backfill_investigation_data.py" in dockerfile


def test_backfill_rejects_unbounded_batch_sizes(tmp_path):
    from scripts.backfill_investigation_data import run_backfill

    with pytest.raises(ValueError, match="between 1 and 1000"):
        run_backfill(batch_size=1001, checkpoint_path=tmp_path / "checkpoint.json")


def test_dry_run_executes_all_stages_without_writing_a_checkpoint(tmp_path):
    from scripts.backfill_investigation_data import STAGE_NAMES, StageReport, run_backfill

    calls = []

    def stage(context, cursor):
        calls.append((context.apply, cursor))
        return StageReport(eligible=3, processed=0, cursor=cursor, done=False)

    checkpoint = tmp_path / "checkpoint.json"
    summary = run_backfill(
        checkpoint_path=checkpoint,
        stages={name: stage for name in STAGE_NAMES},
    )

    assert summary["mode"] == "dry-run"
    assert list(summary["stages"]) == list(STAGE_NAMES)
    assert calls == [(False, None)] * len(STAGE_NAMES)
    assert not checkpoint.exists()


def test_default_dry_run_stages_execute_only_read_statements(tmp_path):
    from scripts.backfill_investigation_data import STAGE_NAMES, run_backfill

    class EmptyResult:
        def scalar_one(self):
            return 0

        def fetchall(self):
            return []

    class ReadOnlySession:
        def __init__(self):
            self.statements = []

        def execute(self, statement, params=None):
            sql = str(statement).strip()
            self.statements.append(sql)
            upper = sql.upper()
            assert not any(token in upper for token in ("INSERT ", "UPDATE ", "DELETE "))
            return EmptyResult()

    sessions = []

    @contextmanager
    def session_factory():
        session = ReadOnlySession()
        sessions.append(session)
        yield session

    checkpoint = tmp_path / "checkpoint.json"
    summary = run_backfill(
        checkpoint_path=checkpoint,
        session_factory=session_factory,
    )

    assert list(summary["stages"]) == list(STAGE_NAMES)
    assert sessions
    assert all(session.statements for session in sessions)
    assert not checkpoint.exists()


def test_apply_checkpoint_resumes_every_stage_and_makes_reruns_idempotent(tmp_path):
    from scripts.backfill_investigation_data import STAGE_NAMES, StageReport, run_backfill

    starts = []

    def stage(context, cursor):
        starts.append(cursor)
        if cursor == 2:
            return StageReport(eligible=0, processed=0, cursor=2, done=True)
        context.save_cursor(1, done=False)
        context.save_cursor(2, done=True)
        return StageReport(eligible=2, processed=2, cursor=2, done=True)

    checkpoint = tmp_path / "checkpoint.json"
    stages = {name: stage for name in STAGE_NAMES}

    first = run_backfill(apply=True, checkpoint_path=checkpoint, stages=stages)
    second = run_backfill(apply=True, checkpoint_path=checkpoint, stages=stages)

    assert checkpoint.exists()
    assert starts[: len(STAGE_NAMES)] == [None] * len(STAGE_NAMES)
    assert starts[len(STAGE_NAMES) :] == [2] * len(STAGE_NAMES)
    assert all(stage["processed"] == 2 for stage in first["stages"].values())
    assert all(stage["processed"] == 0 for stage in second["stages"].values())


def test_legacy_signal_reconstruction_is_honest_about_missing_snapshot():
    from scripts.backfill_investigation_data import reconstruct_signal_evidence

    evidence = reconstruct_signal_evidence(
        SimpleNamespace(
            id=22,
            signal_type="tone_shift",
            payload={"tone": -3.2, "mean_90d": -0.4, "std": 0.8, "z_score": -3.5},
            confidence=0.88,
            created_at=NOW,
        )
    )

    assert evidence is not None
    assert evidence["detector"] == "tone_shift"
    assert evidence["detector_version"] == "legacy-reconstructed-v1"
    assert evidence["threshold"] == {}
    assert evidence["explanation"]["current_rule_reference"] == {
        "absolute_z_score_min": 1.6,
        "standard_deviation_floor": 0.3,
    }
    assert evidence["observed"]["z_score"] == -3.5
    assert evidence["baseline"]["mean"] == -0.4
    assert evidence["window_start"] is None
    assert evidence["window_end"] is None
    assert evidence["completeness"] == "partial"
    assert "исходный снимок" in evidence["explanation"]["limitations"][0]


def test_story_resume_fails_closed_when_the_frozen_candidate_snapshot_changes(
    tmp_path,
    monkeypatch,
):
    import scripts.backfill_investigation_data as backfill

    first_cluster = (
        StoryCandidate(
            thread_id=1,
            country_code="ES",
            event_key="переговоры",
            title="Переговоры",
            article_ids=(1,),
            entities=frozenset({"putin"}),
            topics=frozenset({"diplomacy"}),
            sources=frozenset({"El Pais"}),
            first_seen=NOW,
            last_seen=NOW,
            highest_action_level=3,
        ),
        StoryCandidate(
            thread_id=2,
            country_code="RU",
            event_key="переговоры",
            title="Переговоры",
            article_ids=(2,),
            entities=frozenset({"putin"}),
            topics=frozenset({"diplomacy"}),
            sources=frozenset({"TASS"}),
            first_seen=NOW,
            last_seen=NOW,
            highest_action_level=3,
        ),
    )
    second_cluster = tuple(
        StoryCandidate(
            thread_id=item.thread_id + 2,
            country_code=item.country_code,
            event_key="другой сюжет",
            title="Другой сюжет",
            article_ids=(item.thread_id + 2,),
            entities=frozenset({"lavrov"}),
            topics=frozenset({"diplomacy"}),
            sources=item.sources,
            first_seen=NOW,
            last_seen=NOW,
            highest_action_level=3,
        )
        for item in first_cluster
    )
    snapshot_hash = backfill.story_candidate_snapshot_hash(
        [*first_cluster, *second_cluster]
    )
    loads = [
        ([*first_cluster, *second_cluster], frozenset(), snapshot_hash, 4),
        (
            [*first_cluster, *second_cluster],
            frozenset(),
            "changed-" + snapshot_hash,
            4,
        ),
    ]
    monkeypatch.setattr(
        backfill,
        "_load_story_candidates",
        lambda *args, **kwargs: loads.pop(0),
    )
    calls = 0

    def persist(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated interruption")
        return 71, 2

    monkeypatch.setattr(backfill, "persist_story_cluster", persist)

    @contextmanager
    def session_factory():
        yield object()

    checkpoint = backfill.JsonCheckpointStore(tmp_path / "checkpoint.json")
    state = {"version": backfill.CHECKPOINT_VERSION, "stages": {}}
    context = backfill.StageContext(
        apply=True,
        batch_size=1,
        session_factory=session_factory,
        checkpoint=checkpoint,
        checkpoint_state=state,
        stage_name="story_membership",
    )

    with pytest.raises(RuntimeError, match="simulated interruption"):
        backfill.backfill_story_membership(context, None)
    saved = checkpoint.load()["stages"]["story_membership"]["cursor"]
    assert saved["snapshot_max_thread_id"] == 4
    assert saved["snapshot_hash"] == snapshot_hash
    assert saved["cluster_plan"] == [[1, 2], [3, 4]]
    assert saved["next_cluster_index"] == 1

    resumed_context = backfill.StageContext(
        apply=True,
        batch_size=1,
        session_factory=session_factory,
        checkpoint=checkpoint,
        checkpoint_state=checkpoint.load(),
        stage_name="story_membership",
    )
    with pytest.raises(RuntimeError, match="snapshot changed"):
        backfill.backfill_story_membership(resumed_context, saved)
