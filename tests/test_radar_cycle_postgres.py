"""PostgreSQL end-to-end Radar worker regressions on the bootstrap schema.

Opt-in: requires GEO_PULSE_TEST_DATABASE_URL on a disposable server and
GEO_PULSE_TEST_DATABASE_RESET=1. The test creates its own scratch database from
data/init.sql and drops it afterwards, so it never races other PostgreSQL gates
that reset the shared test database.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from scripts.build_radar import parse_args, run_once


ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def radar_engine():
    dsn = os.getenv("GEO_PULSE_TEST_DATABASE_URL")
    if not dsn or os.getenv("GEO_PULSE_TEST_DATABASE_RESET") != "1":
        pytest.skip("requires an explicitly disposable PostgreSQL database")
    pytest.importorskip("psycopg2")
    scratch = f"geopulse_radar_cycle_{uuid4().hex[:12]}"
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
                    RETURNS text LANGUAGE sql
                    AS $$ SELECT 'test-shim-no-timescaledb'::text $$
                """)
                cursor.execute((ROOT / "data" / "init.sql").read_text())
            raw.commit()
        finally:
            raw.close()
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
        admin.dispose()


def _seed_registry_and_unregistered_publishers(connection) -> None:
    """ES is in the 99-country registry; AD was auto-onboarded on 2026-10-12."""

    connection.execute(text("""
        INSERT INTO countries(code, name_ru, name_en, iso3, region)
        VALUES ('ES', 'Испания', 'Spain', 'ESP', 'europe')
    """))
    connection.execute(text("""
        INSERT INTO sources(id, name, url, country_code, source_type, config)
        VALUES
          (1, 'Diario ES', 'https://diario-es.example/rss', 'ES', 'rss',
           '{"publisher_domain": "diario-es.example"}'),
          (2, 'Radio ES', 'https://radio-es.example/rss', 'ES', 'rss',
           '{"publisher_domain": "radio-es.example"}'),
          (3, 'Diari AD', 'https://diari-ad.example/rss', 'AD', 'rss',
           '{"publisher_domain": "diari-ad.example"}'),
          (4, 'Radio AD', 'https://radio-ad.example/rss', 'AD', 'rss',
           '{"publisher_domain": "radio-ad.example"}')
    """))
    article_id = 0
    for days_ago in (2, 1):
        published = AS_OF - timedelta(days=days_ago, hours=1)
        for source_id in (1, 2, 3, 4):
            article_id += 1
            connection.execute(text("""
                INSERT INTO articles(id, source_id, external_id, title, url,
                                     published_at, collected_at)
                VALUES (:id, :source_id, :external_id, :title, :url,
                        :published_at, :published_at + INTERVAL '10 minutes')
            """), {
                "id": article_id,
                "source_id": source_id,
                "external_id": f"article-{article_id}",
                "title": f"Diplomatic note {article_id}",
                "url": f"https://example.test/{article_id}",
                "published_at": published,
            })
            connection.execute(text("""
                INSERT INTO analysis(article_id, is_relevant, sentiment, event_key,
                                     topics, analyzed_at)
                VALUES (:id, TRUE, -0.6, 'russia_diplomatic_note',
                        ARRAY['diplomacy'], :analyzed_at)
            """), {
                "id": article_id,
                "analyzed_at": published + timedelta(minutes=20),
            })


def test_unregistered_publisher_country_cannot_abort_the_hourly_cycle(radar_engine):
    with radar_engine.begin() as connection:
        _seed_registry_and_unregistered_publishers(connection)

    @contextmanager
    def session_factory():
        session = Session(bind=radar_engine)
        try:
            yield session
        finally:
            session.close()

    payload = run_once(
        parse_args([
            "--apply", "--days", "90", "--generation-days", "3",
            "--as-of", AS_OF.isoformat(),
        ]),
        session_factory=session_factory,
    )

    assert payload["shadow"] is False
    assert payload["inserted_observations"] > 0
    with radar_engine.connect() as connection:
        observed = connection.execute(text("""
            SELECT DISTINCT country_code FROM radar_observations ORDER BY 1
        """)).scalars().all()
        trends = connection.execute(text("""
            SELECT DISTINCT country_code FROM radar_trends
            WHERE scope = 'country' ORDER BY 1
        """)).scalars().all()
    assert observed == ["ES"]
    assert trends == ["ES"]


def _seed_quiet_media_trends(connection, *, as_of: datetime, healthy_until: datetime) -> None:
    """Three open ES media trends whose topics went quiet, on healthy collection.

    Every UTC day carries irrelevant articles from two publisher families:
    healthy collection that yields no new Radar observation for any topic. The
    third trend's evidence predates the 104-day history lookback.
    """

    from src.radar.grouping import CountryWave, assign_meta_trends
    from src.radar.repository import make_observation
    from src.radar.service import _persist_sql

    connection.execute(text("""
        INSERT INTO countries(code, name_ru, name_en, iso3, region)
        VALUES ('ES', 'Испания', 'Spain', 'ESP', 'europe');
        INSERT INTO sources(id, name, url, country_code, source_type, config)
        VALUES
          (1, 'Diario ES', 'https://diario-es.example/rss', 'ES', 'rss',
           '{"publisher_domain": "diario-es.example"}'),
          (2, 'Radio ES', 'https://radio-es.example/rss', 'ES', 'rss',
           '{"publisher_domain": "radio-es.example"}');
    """))
    day = (as_of - timedelta(days=95)).replace(hour=6, minute=0)
    articles = []
    while day < healthy_until:
        for source_id in (1, 2):
            articles.append({
                "id": len(articles) + 1, "source_id": source_id,
                "external_id": f"article-{len(articles) + 1}",
                "url": f"https://example.test/{len(articles) + 1}",
                "published_at": day,
            })
        day += timedelta(days=1)
    connection.execute(text("""
        INSERT INTO articles(id, source_id, external_id, title, url,
                             published_at, collected_at)
        VALUES (:id, :source_id, :external_id, 'Local news', :url,
                :published_at, :published_at + INTERVAL '10 minutes')
    """), articles)
    connection.execute(text("""
        INSERT INTO analysis(article_id, is_relevant, sentiment, topics, analyzed_at)
        VALUES (:id, FALSE, 0, ARRAY[]::text[], :published_at + INTERVAL '20 minutes')
    """), articles)

    def media_point(subject: str, observed_at: datetime, article_id: int):
        return make_observation(
            country_code="ES", contour="media", subject_key=subject,
            direction="negative", metric="attention_share",
            observed_at=observed_at, evidence_ids=(f"article:{article_id}",),
            value=0.8, publisher_family_count=2, source_count=2,
            coverage_confidence=1.0,
            evidence={"article_ids": (article_id,)},
        )

    confirmed = tuple(
        media_point("event:quiet_note", as_of - timedelta(days=days_ago), days_ago)
        for days_ago in (20, 15, 11, 8)
    )
    emerging = (media_point("event:fizzled_note", as_of - timedelta(days=31), 31),)
    ancient = (media_point("event:ancient_note", as_of - timedelta(days=110), 110),)
    waves = (
        CountryWave(
            country_code="ES", contour="media", subject_key="event:quiet_note",
            direction="negative", observations=confirmed,
            first_observed_at=confirmed[0].observed_at,
            t0_auto=confirmed[0].observed_at, t0_effective=confirmed[0].observed_at,
            wave_key="quiet-confirmed", state="confirmed",
            detected_at=confirmed[0].observed_at,
            confirmed_at=confirmed[1].observed_at,
        ),
        CountryWave(
            country_code="ES", contour="media", subject_key="event:fizzled_note",
            direction="negative", observations=emerging,
            first_observed_at=emerging[0].observed_at, t0_auto=None,
            wave_key="quiet-emerging", state="emerging",
            detected_at=emerging[0].observed_at,
        ),
        CountryWave(
            country_code="ES", contour="media", subject_key="event:ancient_note",
            direction="negative", observations=ancient,
            first_observed_at=ancient[0].observed_at, t0_auto=None,
            wave_key="out-of-window", state="emerging",
            detected_at=ancient[0].observed_at,
        ),
    )
    session = Session(bind=connection)
    _persist_sql(
        session, [*confirmed, *emerging, *ancient], waves,
        assign_meta_trends(waves, ()).meta_trends,
        confirmed[-1].observed_at + timedelta(hours=1),
    )
    session.flush()


def test_quiet_media_trends_age_in_incremental_cycles(radar_engine):
    """Hourly ``--generation-days 3`` cycles must age topics with no new article."""

    # Meta memberships record joined_at with the wall clock and may only be
    # closed later, so the replay runs after "now".
    as_of = datetime(datetime.now(timezone.utc).year + 1, 6, 1, 14, tzinfo=timezone.utc)
    later = as_of + timedelta(days=31)
    with radar_engine.begin() as connection:
        _seed_quiet_media_trends(connection, as_of=as_of, healthy_until=later)

    @contextmanager
    def session_factory():
        session = Session(bind=radar_engine)
        try:
            yield session
        finally:
            session.close()

    def cycle(cycle_as_of: datetime) -> dict[str, tuple[str, str, float]]:
        payload = run_once(
            parse_args([
                "--apply", "--days", "90", "--generation-days", "3",
                "--as-of", cycle_as_of.isoformat(),
            ]),
            session_factory=session_factory,
        )
        assert payload["inserted_observations"] == 0
        with radar_engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT wave_key, state, explanation->>'lifecycle_reason' AS reason,
                       coverage_confidence
                FROM radar_trends WHERE scope = 'country'
            """)).all()
        return {
            row.wave_key: (row.state, row.reason, float(row.coverage_confidence))
            for row in rows
        }

    assert cycle(as_of) == {
        "quiet-confirmed": ("cooling", "quiet_window_elapsed", 1.0),
        "quiet-emerging": ("rejected", "unconfirmed_quiet_window_elapsed", 1.0),
        # Not re-scored from nothing: a full cycle still ages it.
        "out-of-window": ("emerging", "unscored", 1.0),
    }
    assert cycle(later)["quiet-confirmed"] == ("resolved", "quiet_window_elapsed", 1.0)
    with radar_engine.connect() as connection:
        transitions = connection.execute(text("""
            SELECT trend.wave_key, event.from_state, event.to_state
            FROM radar_state_events event
            JOIN radar_trends trend ON trend.id = event.trend_id
            WHERE trend.scope = 'country'
            ORDER BY trend.wave_key, event.occurred_at
        """)).all()
        open_members = connection.execute(text("""
            SELECT count(*) FROM radar_trend_members WHERE left_at IS NULL
        """)).scalar_one()
    assert [tuple(row) for row in transitions] == [
        ("out-of-window", None, "emerging"),
        ("quiet-confirmed", None, "confirmed"),
        ("quiet-confirmed", "confirmed", "cooling"),
        ("quiet-confirmed", "cooling", "resolved"),
        ("quiet-emerging", None, "emerging"),
        ("quiet-emerging", "emerging", "rejected"),
    ]
    # Only the untouched out-of-window trend keeps its meta membership.
    assert open_members == 1


def test_annual_action_trend_cools_and_resolves_in_incremental_cycles(radar_engine):
    """A confirmed annual action must age out instead of being held forever."""

    # Meta memberships record joined_at with the wall clock and may only be
    # closed later, so the replay runs after "now": the next period end.
    year = datetime.now(timezone.utc).year + 1
    effective = datetime(year, 12, 31, tzinfo=timezone.utc)
    with radar_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO countries(code, name_ru, name_en, iso3, region)
            VALUES ('RS', 'Сербия', 'Serbia', 'SRB', 'europe');
            INSERT INTO trade_data(country_code, year, total_trade_usd, updated_at)
            VALUES ('RS', :previous_year, 100, :loaded_at), ('RS', :year, 80, :loaded_at);
        """), {
            "previous_year": year - 1, "year": year,
            "loaded_at": datetime(year - 1, 6, 1),
        })

    @contextmanager
    def session_factory():
        session = Session(bind=radar_engine)
        try:
            yield session
        finally:
            session.close()

    def cycle(as_of: datetime) -> tuple[str, str]:
        run_once(
            parse_args([
                "--apply", "--days", "90", "--generation-days", "3",
                "--as-of", as_of.isoformat(),
            ]),
            session_factory=session_factory,
        )
        with radar_engine.connect() as connection:
            row = connection.execute(text("""
                SELECT trend.state, trend.explanation->>'lifecycle_reason' AS reason
                FROM radar_trends trend
                WHERE trend.scope = 'country' AND trend.country_code = 'RS'
                  AND trend.contour = 'action'
                  AND trend.subject_key = 'economy:trade:russia'
            """)).one()
        return row.state, row.reason

    assert cycle(effective + timedelta(days=2)) == (
        "confirmed", "authoritative_action_evidence",
    )
    assert cycle(effective + timedelta(days=9)) == ("cooling", "quiet_window_elapsed")
    assert cycle(effective + timedelta(days=34)) == ("resolved", "quiet_window_elapsed")
    with radar_engine.connect() as connection:
        transitions = connection.execute(text("""
            SELECT event.from_state, event.to_state
            FROM radar_state_events event
            JOIN radar_trends trend ON trend.id = event.trend_id
            WHERE trend.scope = 'country' AND trend.country_code = 'RS'
            ORDER BY event.occurred_at
        """)).all()
    assert [tuple(row) for row in transitions] == [
        (None, "confirmed"), ("confirmed", "cooling"), ("cooling", "resolved"),
    ]


def test_subjects_sharing_a_story_anchor_persist_as_separate_meta_trends(radar_engine):
    """Prod 2026-10-12: story 33 anchored two subjects; their meta public ids
    collided and the unique violation rolled back every hourly cycle."""

    from src.radar.grouping import CountryWave, MetaTrend
    from src.radar.service import _persist_meta_and_contours
    from src.radar.types import TrendState

    anchor = "anchor:story:33:энергетическое перемирие украина россия:positive"
    first_seen = datetime(2026, 10, 11, tzinfo=timezone.utc)
    metas = tuple(
        MetaTrend(
            subject, "positive",
            (CountryWave(
                "US", "media", subject, "positive", (), first_seen, None,
                wave_key=f"{subject}-wave", last_observed_at=first_seen,
                state=TrendState.RESOLVED,
            ),),
            None, meta_key=anchor,
        )
        for subject in ("topic:energy", "topic:ukraine_war")
    )

    with Session(bind=radar_engine) as session:
        _persist_meta_and_contours(session, metas, {}, AS_OF)
        session.commit()
        # A second cycle finds both rows by identity instead of re-inserting.
        _persist_meta_and_contours(session, metas, {}, AS_OF + timedelta(hours=1))
        session.commit()

    with radar_engine.connect() as connection:
        rows = connection.execute(text("""
            SELECT subject_key, public_id FROM radar_trends
            WHERE scope = 'meta' AND meta_key = :anchor ORDER BY subject_key
        """), {"anchor": anchor}).all()
    assert [row.subject_key for row in rows] == ["topic:energy", "topic:ukraine_war"]
    assert rows[0].public_id != rows[1].public_id
