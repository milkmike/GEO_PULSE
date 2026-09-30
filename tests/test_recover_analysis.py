import os
from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from scripts import recover_analysis as recovery
from src.db import Analysis


@pytest.mark.parametrize("remaining,reset", [(0, None), (None, None), ("NaN", None)])
def test_paid_recovery_rejects_missing_or_exhausted_allowance(monkeypatch, remaining, reset):
    monkeypatch.setattr(recovery.httpx, "get", lambda *a, **kw: httpx.Response(200,
        request=httpx.Request("GET", "https://openrouter.ai/api/v1/key"),
        json={"data": {"limit_remaining": remaining, "limit": 5, "limit_reset": reset}}))
    with pytest.raises(ValueError):
        recovery.check_budget(Decimal("5"))


def test_paid_recovery_accepts_provider_enforced_cap(monkeypatch):
    monkeypatch.setattr(recovery.httpx, "get", lambda *a, **kw: httpx.Response(200,
        request=httpx.Request("GET", "https://openrouter.ai/api/v1/key"),
        json={"data": {"limit_remaining": 4.99, "limit": 5, "limit_reset": None, "usage": .01}}))
    assert recovery.check_budget(Decimal("5"))["remaining_usd"] == "4.99"


def test_dry_run_never_calls_provider_or_changes_rows(monkeypatch):
    @contextmanager
    def session():
        yield SimpleNamespace(execute=lambda *a, **kw: SimpleNamespace(fetchall=lambda: [SimpleNamespace(id=7)]))
    monkeypatch.setattr(recovery, "get_session", session)
    monkeypatch.setattr(recovery, "check_budget", lambda _: pytest.fail("network in dry run"))
    monkeypatch.setattr(recovery, "repair_row", lambda _: pytest.fail("write in dry run"))
    assert recovery.recover([7]) == {"apply": False, "eligible_ids": [7], "results": []}


@pytest.mark.parametrize("ids", [[], [0], list(range(1, 27))])
def test_recovery_selection_is_explicit_and_bounded(ids):
    with pytest.raises(ValueError):
        recovery.recover(ids)


@pytest.fixture
def repair_database(monkeypatch):
    dsn = os.getenv("GEO_PULSE_TEST_DATABASE_URL")
    if not dsn or os.getenv("GEO_PULSE_TEST_DATABASE_RESET") != "1":
        pytest.skip("requires explicitly disposable PostgreSQL")
    schema = "recovery_test_" + uuid4().hex
    engine = create_engine(dsn)
    assert engine.url.database.endswith("_test")
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = create_engine(dsn, connect_args={"options": f"-csearch_path={schema}"})
    Analysis.__table__.create(scoped)
    sessions = sessionmaker(bind=scoped, expire_on_commit=False)
    with sessions.begin() as session:
        session.add(Analysis(id=11, article_id=7, is_relevant=True,
                             model_used="keyword_filter", prompt_version="v1.1", relevance_score=.8))

    @contextmanager
    def transaction():
        with sessions.begin() as session:
            yield session

    monkeypatch.setattr(recovery, "get_session", transaction)
    yield sessions
    scoped.dispose()
    with engine.begin() as connection:
        connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


def result():
    return {"article_id": 7, "is_relevant": True, "model_used": "tested-model",
            "sentiment": 1, "event_type": "diplomatic", "event_key": "treaty signed",
            "raw_response": {"fixture": True}, "entities": None}


def test_repair_preserves_identity_and_commits_valid_analysis(repair_database, monkeypatch):
    monkeypatch.setattr(recovery.analyze, "_analyze_one", lambda _: result())
    assert recovery.repair_row(SimpleNamespace(id=7, analysis_id=11)) == "repaired"
    with repair_database() as session:
        saved = session.execute(select(Analysis)).scalar_one()
        assert (saved.id, saved.article_id, saved.model_used, saved.sentiment) == (11, 7, "tested-model", 1)


def test_provider_failure_keeps_original_analysis(repair_database, monkeypatch):
    monkeypatch.setattr(recovery.analyze, "_analyze_one", lambda _: None)
    assert recovery.repair_row(SimpleNamespace(id=7, analysis_id=11)) == "provider_failed"
    with repair_database() as session:
        saved = session.get(Analysis, 11)
        assert saved.model_used == "keyword_filter" and saved.sentiment is None


def test_repair_does_not_overwrite_concurrent_success(repair_database, monkeypatch):
    def concurrent(_):
        with repair_database.begin() as session:
            existing = session.get(Analysis, 11)
            existing.model_used, existing.sentiment = "another-success", -1
        return result()
    monkeypatch.setattr(recovery.analyze, "_analyze_one", concurrent)
    assert recovery.repair_row(SimpleNamespace(id=7, analysis_id=11)) == "changed_concurrently"
    with repair_database() as session:
        assert session.get(Analysis, 11).model_used == "another-success"


def test_mention_write_failure_rolls_back_analysis(repair_database, monkeypatch):
    monkeypatch.setattr(recovery.analyze, "_analyze_one", lambda _: result())
    def fail(*args):
        raise RuntimeError("mention failure")
    monkeypatch.setattr(recovery, "upsert_analysis_mentions", fail)
    with pytest.raises(RuntimeError, match="mention failure"):
        recovery.repair_row(SimpleNamespace(id=7, analysis_id=11))
    with repair_database() as session:
        saved = session.get(Analysis, 11)
        assert saved.model_used == "keyword_filter" and saved.sentiment is None
