"""Changes HTTP contract on an isolated PostgreSQL bootstrap database.

Opt-in: GEO_PULSE_TEST_DATABASE_URL and GEO_PULSE_TEST_DATABASE_RESET=1.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from src.api.routes import changes


@pytest.fixture
def changes_engine():
    dsn = os.getenv("GEO_PULSE_TEST_DATABASE_URL")
    if not dsn or os.getenv("GEO_PULSE_TEST_DATABASE_RESET") != "1":
        pytest.skip("requires an explicitly disposable PostgreSQL database")
    pytest.importorskip("psycopg2")
    scratch = f"geopulse_changes_{uuid4().hex[:12]}"
    admin = create_engine(dsn, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{scratch}" TEMPLATE template0')
    except ProgrammingError as exc:
        admin.dispose()
        pytest.skip(f"cannot create a scratch database: {exc.orig}")
    engine = create_engine(make_url(dsn).set(database=scratch))
    try:
        raw = engine.raw_connection()
        try:
            with raw.cursor() as cursor:
                cursor.execute("""
                    CREATE FUNCTION create_hypertable(regclass, name)
                    RETURNS text LANGUAGE sql AS $$ SELECT 'test-shim-no-timescaledb'::text $$
                """)
                cursor.execute((Path(__file__).resolve().parents[1] / "data/init.sql").read_text())
            raw.commit()
        finally:
            raw.close()
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
        admin.dispose()


def test_real_signals_stories_filters_cursor_and_evidence(changes_engine, monkeypatch):
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    with changes_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO countries(code, name_ru, name_en, iso3, region) VALUES
              ('GB', 'Великобритания', 'United Kingdom', 'GBR', 'europe'),
              ('JP', 'Япония', 'Japan', 'JPN', 'asia');
            INSERT INTO sources(id, name, url, country_code, source_type)
              VALUES (1, 'Газета', 'https://example.org/rss', 'GB', 'rss');
            INSERT INTO articles(id, source_id, title, url, published_at)
              VALUES (1, 1, 'Обсуждение соглашения', 'https://example.org/1', :now),
                     (2, 1, 'Переговоры', 'https://example.org/2', :now),
                     (3, 1, 'Ещё переговоры', 'https://example.org/3', :now);
        """), {"now": now})
        for id, days_ago in ((1, 2), (2, 1), (3, 0)):
            at = now - timedelta(days=days_ago)
            connection.execute(text("""
                INSERT INTO signals(id, signal_type, country_code, severity, title, payload,
                                    dedup_key, created_at, expires_at)
                VALUES (:id, 'tone_shift', 'GB', 'warning', 'Сдвиг тона',
                        CAST(:payload AS jsonb), :key, :at, :expires)
            """), {"id": id, "key": f"tone_shift:GB:{at:%Y%m%d}", "at": at, "expires": now - timedelta(hours=1),
                   "payload": '{"tone":-12.7,"mean_90d":-2.1,"std":2.6,"z_score":-4}'})
            connection.execute(text("""
                INSERT INTO signal_evidence(signal_id, detector, detector_version, threshold, observed,
                    baseline, article_ids, confidence, completeness)
                VALUES (:id, 'tone_shift', '1', '{}', '{}', '{}', :articles, 0.8, 'complete')
            """), {"id": id, "articles": [1, id]})
        connection.execute(text("""
            INSERT INTO stories(id, slug, title_ru, lifecycle, first_seen, last_seen, article_count,
                                country_count, clustering_confidence, created_at)
            VALUES (1, 'new-agreement', 'Новое соглашение с Россией', 'emerging', :now, :now, 3, 2, 0.9, :now),
                   (2, 'merged', 'Дубль', 'emerging', :now, :now, 3, 2, 0.9, :now),
                   (3, 'quarantined', 'Скрытый сюжет', 'emerging', :now, :now, 3, 2, 0.9, :now);
            UPDATE stories SET meta = '{"merged_into_story_id": 1}' WHERE id = 2;
            UPDATE stories SET meta = '{"quarantined_at": "2026-10-16"}' WHERE id = 3;
            INSERT INTO story_countries(story_id, country_code) VALUES (1, 'GB'), (1, 'JP');
            INSERT INTO story_articles(story_id, article_id, membership_confidence)
              VALUES (1, 1, 0.9), (1, 2, 0.9), (1, 3, 0.9);
        """), {"now": now})

    @contextmanager
    def session_factory():
        with Session(bind=changes_engine) as session:
            yield session

    monkeypatch.setattr(changes, "get_session", session_factory)
    app = FastAPI()
    app.include_router(changes.router)
    client = TestClient(app)
    payload = client.get("/api/v2/changes").json()
    assert payload["total"] == 2 and payload["raw_total"] == 4
    assert {item["kind"] for item in payload["items"]} == {"story", "signal"}
    signal = next(i for i in payload["items"] if i["kind"] == "signal")
    assert signal["repeat_count"] == 3 and signal["days_running"] == 3
    assert signal["title"] == "Тон СМИ о России −12,7"
    assert signal["evidence_total"] == 3
    assert len(signal["evidence_preview"]) == 2
    story = next(i for i in payload["items"] if i["kind"] == "story")
    assert story["country_names"] == ["Великобритания", "Япония"]
    assert story["evidence_total"] == 3
    assert "3 публикации" in story["context"]
    assert client.get("/api/v2/changes?type=stories&important=1&country=jp").json()["total"] == 1
    assert client.get("/api/v2/changes?type=tone&country=JP").json()["total"] == 0
    assert client.get("/api/v2/changes?type=waves").json()["items"] == []
    page = client.get("/api/v2/changes?limit=1").json()
    rest = client.get("/api/v2/changes", params={"cursor": page["next_cursor"], "limit": 1}).json()
    assert rest["next_cursor"] is None
    assert rest["items"][0]["id"] != page["items"][0]["id"]
    assert rest["total"] == 2
    assert client.get("/api/v2/changes", params={"cursor": page["next_cursor"], "days": 30}).status_code == 422


def test_waves_use_the_radar_public_gate_and_active_participant_countries(changes_engine, monkeypatch):
    now = datetime.now(timezone.utc) - timedelta(minutes=5)
    public_id = uuid4()
    with changes_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO countries(code, name_ru, name_en, iso3, region) VALUES
              ('GB', 'Великобритания', 'United Kingdom', 'GBR', 'europe'),
              ('JP', 'Япония', 'Japan', 'JPN', 'asia');
            INSERT INTO sources(id, name, url, country_code, source_type)
              VALUES (1, 'Газета', 'https://example.org/rss', 'GB', 'rss');
            INSERT INTO articles(id, source_id, title, url, published_at)
              VALUES (1, 1, 'Торговые переговоры', 'https://example.org/1', :now);
        """), {"now": now})
        for id, country in ((1, "GB"), (2, "JP")):
            connection.execute(text("""
                INSERT INTO radar_trends(id, public_id, scope, contour, country_code, subject_key,
                    wave_key, title_ru, direction, state, confidence, coverage_confidence,
                    first_observed_at, detector_version, updated_at)
                VALUES (:id, :public_id, 'country', 'action', :country, 'economy:trade:russia',
                    :key, 'Торговля', 'increase', 'confirmed', 0.9, 0.9, :now, '1', :now)
            """), {"id": id, "public_id": uuid4(), "country": country, "key": f"wave-{id}", "now": now})
        # Only the first meta trend meets all the existing public Radar gates.
        for id, state, subject, velocity, member_count, evidence in (
            (3, "confirmed", "economy:trade:russia", 0.2, 2, True),
            (4, "candidate", "economy:trade:russia", 0.2, 2, True),
            (5, "confirmed", "media:coverage", 0.2, 2, True),
            (6, "confirmed", "economy:trade:russia", 0.01, 2, True),
            (7, "confirmed", "economy:trade:russia", 0.2, 1, True),
            (8, "confirmed", "economy:trade:russia", 0.2, 2, False),
        ):
            connection.execute(text("""
                INSERT INTO radar_trends(id, public_id, scope, subject_key, meta_key, title_ru,
                    direction, state, confidence, coverage_confidence, velocity,
                    first_observed_at, detector_version, updated_at)
                VALUES (:id, :public_id, 'meta', :subject, :key, 'Торговля',
                    'increase', :state, 0.9, 0.9, :velocity, :now, '1', :now)
            """), {"id": id, "public_id": public_id if id == 3 else uuid4(), "subject": subject,
                   "key": f"meta-{id}", "state": state, "velocity": velocity, "now": now})
            for member in range(1, member_count + 1):
                connection.execute(text("""
                    INSERT INTO radar_trend_members(public_id, meta_trend_id, country_trend_id)
                    VALUES (:public_id, :id, :member)
                """), {"public_id": uuid4(), "id": id, "member": member})
            if evidence:
                connection.execute(text("""
                    INSERT INTO radar_trend_evidence(public_id, trend_id, article_id, role)
                    VALUES (:public_id, :id, 1, 'support')
                """), {"public_id": uuid4(), "id": id})

    @contextmanager
    def session_factory():
        with Session(bind=changes_engine) as session:
            yield session

    monkeypatch.setattr(changes, "get_session", session_factory)
    app = FastAPI()
    app.include_router(changes.router)
    response = TestClient(app).get("/api/v2/changes?type=waves&country=JP")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == payload["raw_total"] == 1
    [wave] = payload["items"]
    assert wave["kind"] == "wave"
    assert wave["country_codes"] == ["GB", "JP"]
    assert wave["href"] == f"/radar/{public_id}"
    assert wave["title"] == "Торговые связи с Россией усиливаются"
    assert wave["evidence_total"] == 1
    assert wave["evidence_preview"][0]["source_name"] == "Газета"
