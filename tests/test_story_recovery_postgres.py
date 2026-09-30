"""Real SQL regressions for recovery eligibility and additive thread refresh."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from scripts import build_threads, prepare_embedding_jobs
from tests.test_postgres_migrations import _requirements, _reset, _run_migrations, _assert_success


@pytest.fixture(scope="module")
def story_engine():
    dsn, psycopg2 = _requirements()
    assert create_engine(dsn).url.database.endswith("_test")
    with psycopg2.connect(dsn) as connection:
        with connection.cursor() as cursor:
            _reset(cursor, initialize=True)
    _assert_success(_run_migrations(dsn))
    engine = create_engine(dsn)
    yield engine
    engine.dispose()


@pytest.fixture
def story_session(story_engine):
    with Session(story_engine) as session:
        session.execute(text("""
            INSERT INTO sources (id,name,url,country_code,source_type)
            VALUES (7001,'Publisher','https://example.test','BY','rss');
            INSERT INTO articles (id,source_id,title,body,published_at,geo_country_code,geo_status)
            SELECT id,7001,'Trade agreement','A specific trade agreement',now(),'BY','source_verified'
            FROM generate_series(7001,7004) id;
            INSERT INTO analysis (article_id,is_relevant,model_used,sentiment,event_key)
            VALUES (7001,true,'keyword_filter',NULL,'specific trade agreement'),
                   (7002,true,'tested-model',1,'specific trade agreement'),
                   (7003,true,NULL,NULL,'specific legacy agreement'),
                   (7004,true,'keyword_filter',1,'repaired legacy agreement');
        """))
        yield session
        session.rollback()


def test_placeholder_excluded_without_rejecting_legacy_or_repaired_rows(story_session):
    expected = {7002, 7003, 7004}
    ordinary = prepare_embedding_jobs.load_eligible_articles(story_session, days=3, limit=10)
    pending = prepare_embedding_jobs.load_story_eligible_articles(
        story_session, days=3, limit=10, profile_id=None)
    coverage = prepare_embedding_jobs.load_story_embedding_coverage(story_session, days=3, profile_id=None)
    threads = build_threads.fetch_articles(story_session, days=3)
    assert {r['id'] for r in ordinary} == expected
    assert {r['id'] for r in pending} == expected
    assert coverage == {'eligible': 3, 'ready_current': 0, 'missing_current': 3}
    assert {r['article_id'] for r in threads} == expected


def test_queued_placeholder_never_reaches_paid_embedding_provider(story_session):
    from contextlib import nullcontext

    from scripts.index_embeddings import index_pending, load_job_content
    from src.embedding_store import EmbeddingJob, EmbeddingProfile, content_hash
    from tests.test_embedding_store import DocumentOnlyProvider, IndexStore

    profile = EmbeddingProfile('test', 'test', 'test', 2, 'text-matching', '1', True, 4)
    content = 'Trade agreement\nA specific trade agreement'
    jobs = [EmbeddingJob(id=i, profile_id=4, object_type='article', object_id=str(i),
                         content_hash=content_hash(content), attempts=1)
            for i in range(7001, 7005)]
    store = IndexStore(profile, jobs)
    provider = DocumentOnlyProvider(profile)
    result = index_pending(
        store=store, session_factory=lambda: nullcontext(story_session),
        provider_factory=lambda _: provider,
    )
    assert result == {'processed': 4, 'indexed': 3, 'failed': 1}
    assert provider.documents == [[content, content, content]]
    assert [job.object_id for job, _, retry in store.failures if not retry] == ['7001']

    # Recovery restores eligibility without changing the article's content/hash.
    story_session.execute(text("UPDATE analysis SET sentiment=1 WHERE article_id=7001"))
    assert load_job_content(story_session, jobs[0]) == content


def test_additive_refresh_count_matches_all_saved_memberships(story_session, monkeypatch):
    monkeypatch.setattr(build_threads, 'calculate_importance_v2', lambda _: {
        'importance': 5, 'velocity': 0, 'sentiment_shift': 0})
    articles = [dict(article_id=i, sentiment=1, action_level=2,
                     title='Trade agreement', published_at=datetime.now(timezone.utc))
                for i in (7002,7003,7004)]
    options = dict(replace_memberships=False, generate_narrative=False)
    first = build_threads.upsert_thread(story_session,'BY','trade agreement',articles,[],**options)
    second = build_threads.upsert_thread(story_session,'BY','trade agreement',articles[1:],[],**options)
    assert first == second
    row = story_session.execute(text('''
        SELECT t.article_count, count(ta.article_id) AS saved
        FROM threads t JOIN thread_articles ta ON ta.thread_id=t.id
        WHERE t.id=:id GROUP BY t.id
    '''), {'id': first}).one()
    assert row.article_count == row.saved == 3


def test_thread_embedding_lookup_requires_current_hash_and_active_profile(story_session):
    story_session.execute(text("""
        INSERT INTO embedding_profiles (id,profile_key,provider,model,dimensions,version,active)
        VALUES (701,'active-test','test','test',1024,'v1',true),
               (702,'old-test','test','test',1024,'v0',false);
        INSERT INTO content_embeddings (profile_id,object_type,object_id,content_hash,embedding)
        SELECT 701,'article',id::text,encode(digest(title || E'\\n' || body,'sha256'),'hex'),
               ('[1,' || array_to_string(array_fill(0,ARRAY[1023]),',') || ']')::vector
        FROM articles WHERE id IN (7001,7002,7003);
        UPDATE articles SET body='Updated text' WHERE id=7003;
        INSERT INTO content_embeddings (profile_id,object_type,object_id,content_hash,embedding)
        SELECT 702,'article',id::text,encode(digest(title || E'\\n' || body,'sha256'),'hex'),
               ('[1,' || array_to_string(array_fill(0,ARRAY[1023]),',') || ']')::vector
        FROM articles WHERE id=7004;
    """))
    rows = build_threads.fetch_articles(story_session, days=3)
    assert {r['article_id']: r['has_embedding'] for r in rows} == {
        7002: True, 7003: False, 7004: False,
    }
