"""Opt-in live PostgreSQL regression for story membership publication."""

from __future__ import annotations

import os
from urllib.parse import urlparse

import psycopg2
import pytest


DATABASE_URL = os.getenv("GEOPULSE_STORY_MVCC_TEST_DATABASE_URL")


@pytest.mark.skipif(
    not DATABASE_URL,
    reason="GEOPULSE_STORY_MVCC_TEST_DATABASE_URL is not configured",
)
def test_commit_after_page_one_stays_outside_cursor_generation():
    """A writer cannot publish memberships before its clock increment commits."""

    database_name = urlparse(DATABASE_URL).path.removeprefix("/")
    if not database_name.endswith("_test"):
        pytest.fail("MVCC regression requires a dedicated *_test database")

    setup = psycopg2.connect(DATABASE_URL)
    writer = psycopg2.connect(DATABASE_URL)
    reader = psycopg2.connect(DATABASE_URL)
    try:
        setup.autocommit = True
        with setup.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS story_membership_clock (
                    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (singleton),
                    generation BIGINT NOT NULL DEFAULT 0 CHECK (generation >= 0)
                )
            """)
            cursor.execute("""
                INSERT INTO story_membership_clock (singleton, generation)
                VALUES (TRUE, 0)
                ON CONFLICT (singleton) DO UPDATE SET generation = 0
            """)
            cursor.execute("DROP TABLE IF EXISTS story_membership_mvcc_probe")
            cursor.execute("""
                CREATE TABLE story_membership_mvcc_probe (
                    article_id INTEGER PRIMARY KEY,
                    membership_generation BIGINT NOT NULL
                )
            """)
            cursor.execute("""
                INSERT INTO story_membership_mvcc_probe
                    (article_id, membership_generation)
                VALUES (1, 0)
            """)

        writer.autocommit = False
        reader.autocommit = True
        with writer.cursor() as writer_cursor:
            writer_cursor.execute("""
                UPDATE story_membership_clock
                SET generation = generation + 1
                WHERE singleton IS TRUE
                RETURNING generation
            """)
            next_generation = writer_cursor.fetchone()[0]
            writer_cursor.execute("""
                INSERT INTO story_membership_mvcc_probe
                    (article_id, membership_generation)
                VALUES (2, %s)
            """, (next_generation,))

            with reader.cursor() as reader_cursor:
                reader_cursor.execute("""
                    SELECT generation FROM story_membership_clock
                    WHERE singleton IS TRUE
                """)
                page_one_generation = reader_cursor.fetchone()[0]
                reader_cursor.execute("""
                    SELECT article_id FROM story_membership_mvcc_probe
                    WHERE membership_generation <= %s ORDER BY article_id
                """, (page_one_generation,))
                assert page_one_generation == 0
                assert reader_cursor.fetchall() == [(1,)]

            writer.commit()

            with reader.cursor() as reader_cursor:
                reader_cursor.execute("""
                    SELECT generation FROM story_membership_clock
                    WHERE singleton IS TRUE
                """)
                assert reader_cursor.fetchone()[0] == next_generation == 1
                reader_cursor.execute("""
                    SELECT article_id FROM story_membership_mvcc_probe
                    WHERE membership_generation <= %s ORDER BY article_id
                """, (page_one_generation,))
                assert reader_cursor.fetchall() == [(1,)]
                reader_cursor.execute("""
                    SELECT article_id FROM story_membership_mvcc_probe
                    WHERE membership_generation <= %s ORDER BY article_id
                """, (next_generation,))
                assert reader_cursor.fetchall() == [(1,), (2,)]
    finally:
        writer.rollback()
        writer.close()
        reader.close()
        if not setup.closed:
            with setup.cursor() as cursor:
                cursor.execute("DROP TABLE IF EXISTS story_membership_mvcc_probe")
            setup.close()
