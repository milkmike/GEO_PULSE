"""Article-first storage must not inherit the RRI admission gate."""
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def store(monkeypatch):
    from src import agenda_store
    url = os.getenv('GEO_PULSE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('GEO_PULSE_TEST_DATABASE_URL required for isolated PostgreSQL test')
    engine = create_engine(url)
    schema = 'agenda_test_' + uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    local = create_engine(url, connect_args={'options': f'-csearch_path={schema}'})
    with local.begin() as conn:
        conn.exec_driver_sql('''CREATE TABLE sources(id int primary key,name text,country_code text,config jsonb default '{}');
        CREATE TABLE articles(id int primary key,source_id int,title text,body text,summary text,url text,resolved_url text,
         published_at timestamptz,collected_at timestamptz,is_duplicate bool default false,publisher_source_id int,geo_status text default 'source_verified');
        CREATE VIEW article_country_facts AS SELECT a.id article_id,s.* FROM articles a JOIN sources s ON s.id=a.source_id;
        INSERT INTO sources(id,name,country_code) VALUES(1,'Source A','BY'),(2,'Source B','IL');
        INSERT INTO articles(id,source_id,title,body,summary,url,resolved_url,published_at,collected_at,is_duplicate) VALUES
        (1,1,'Самолёт Flydubai сел в Табуке','Учебный авиационный инцидент.',NULL,'https://news.example/a',NULL,now(),now(),false),
        (2,2,'Flydubai flight lands in Tabuk','Aircraft diverted to Tabuk.',NULL,'https://news.example/b',NULL,now(),now(),false),
        (3,1,'Old date but new collection','Some new report',NULL,'javascript:alert(1)',NULL,'2026-01-01',now(),false),
        (4,1,'Duplicate','Copy',NULL,NULL,NULL,now(),now(),true);
        ''')
        conn.exec_driver_sql(Path('scripts/migrations/034_news_agendas.sql').read_text())
        conn.exec_driver_sql(Path('scripts/migrations/036_article_title_translations.sql').read_text())
        conn.exec_driver_sql(Path('scripts/migrations/038_article_news_triage.sql').read_text())
    factory = sessionmaker(bind=local)
    @contextmanager
    def session():
        with factory() as s:
            try:
                yield s
                s.commit()
            except Exception:
                s.rollback()
                raise
    monkeypatch.setattr(agenda_store,'get_session',session)
    yield agenda_store
    local.dispose()
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
    engine.dispose()


def decision(choice='same_event',confidence=.95):
    return {'cache_key':'a'*64,'anchor_id':1,'article_id':2,'question_id':'pair_1_2',
            'choice':choice,'confidence':confidence,
            'probabilities':{'same_event':.95,'development':.02,'unrelated':.02,'uncertain':.01}}


def test_raw_articles_need_no_analysis_and_bad_date_remains_visible(store):
    rows = store.load_articles()
    assert {r['id'] for r in rows} == {1,2,3}
    odd = next(r for r in rows if r['id']==3)
    assert odd['date_warning'] is True
    assert odd['effective_time'] == odd['collected_at']


def test_only_accepted_jev_evidence_creates_idempotent_agenda(store):
    rows={r['id']:r for r in store.load_articles()}
    assert store.attach_decisions(rows[1],[decision(confidence=.1)],rows) is None
    assert store.list_agendas()['items'] == []
    gid=store.attach_decisions(rows[1],[decision()],rows)
    assert store.attach_decisions(rows[1],[decision()],rows)==gid
    result=store.list_agendas()
    assert len(result['items'])==1
    item=result['items'][0]
    assert item['article_count']==2 and item['source_count']==2
    assert item['countries']==['BY','IL']
    assert {a['relation'] for a in item['articles']}=={'seed','same_event'}
    assert len(store.load_groups())==1
    assert store.list_agendas(q='absent')['items']==[]


def test_decision_cache_and_run_coverage_survive_reload(store):
    d=decision()
    store.save_decisions([d])
    assert store.get_cached_decisions([d['cache_key']])[d['cache_key']]['choice']=='same_event'
    store.record_run('budget_exhausted',{'articles_scanned':3,'accepted':0,'remaining_budget_usd':0})
    assert store.list_agendas()['coverage']['status']=='budget_exhausted'


def test_changed_article_content_does_not_keep_stale_membership(store):
    rows={r['id']:r for r in store.load_articles()}
    store.attach_decisions(rows[1],[decision()],rows)
    with store.get_session() as s:
        s.execute(text("UPDATE articles SET body='A different event now' WHERE id=2"))
    assert store.list_agendas()['items']==[]


def test_unsafe_source_links_are_not_exposed(store):
    rows={r['id']:r for r in store.load_articles()}
    d=decision(); d.update(article_id=3,cache_key='b'*64)
    store.attach_decisions(rows[1],[d],rows)
    item=store.list_agendas()['items'][0]
    assert next(a for a in item['articles'] if a['id']==3)['url'] is None


def test_unverified_publisher_is_not_used_as_country_evidence(store):
    with store.get_session() as s:
        s.execute(text("UPDATE articles SET geo_status='unverified' WHERE id=2"))
    assert 2 not in {a['id'] for a in store.load_articles()}


@pytest.mark.parametrize('change', [
    "UPDATE articles SET published_at=published_at-interval '2 days' WHERE id=2",
    "UPDATE articles SET collected_at=collected_at-interval '1 hour' WHERE id=2",
    "UPDATE sources SET country_code='GE' WHERE id=2",
])
def test_changed_decision_context_invalidates_membership(store, change):
    rows={r['id']:r for r in store.load_articles()}
    store.attach_decisions(rows[1],[decision()],rows)
    with store.get_session() as s:
        s.execute(text(change))
    assert store.list_agendas()['items']==[]


@pytest.mark.parametrize('change', [
    "UPDATE articles SET is_duplicate=true WHERE id=1",
    "UPDATE articles SET geo_status='unverified' WHERE id=1",
])
def test_invalid_anchor_hides_otherwise_valid_members(store, change):
    rows={r['id']:r for r in store.load_articles()}
    third=decision(); third['article_id']=3
    store.attach_decisions(rows[1],[decision(),third],rows)
    with store.get_session() as s:
        s.execute(text(change))
    assert store.list_agendas()['items']==[]
    assert store.load_groups()==[]


def test_stale_member_can_join_new_group_but_valid_member_cannot(store):
    rows={r['id']:r for r in store.load_articles()}
    old_id=store.attach_decisions(rows[1],[decision()],rows)
    other=decision(); other['anchor_id']=3
    store.attach_decisions(rows[3],[other],rows)
    assert all(item['id']==old_id for item in store.list_agendas()['items'])
    with store.get_session() as s:
        s.execute(text("UPDATE articles SET title='Corrected report about another incident' WHERE id=2"))
    rows={r['id']:r for r in store.load_articles()}
    new_id=store.attach_decisions(rows[3],[other],rows)
    result=store.list_agendas()['items']
    assert len(result)==1 and result[0]['id']==new_id and new_id!=old_id
    assert {a['id'] for a in result[0]['articles']}=={2,3}


def test_changed_snapshot_cannot_be_attached_after_provider_returns(store):
    rows={r['id']:r for r in store.load_articles()}
    with store.get_session() as s:
        s.execute(text("UPDATE articles SET published_at=published_at-interval '1 day' WHERE id=2"))
    assert store.attach_decisions(rows[1],[decision()],rows) is None
    assert store.list_agendas()['items']==[]


def test_discovery_country_comes_from_verified_publisher(store):
    with store.get_session() as s:
        s.execute(text("UPDATE sources SET config='{" + '"feed_mode":"publisher_discovery"' + "}' WHERE id=1"))
        s.execute(text("UPDATE articles SET publisher_source_id=2,geo_status='publisher_verified' WHERE id=1"))
    rows={r['id']:r for r in store.load_articles()}
    assert rows[1]['country_code']=='IL' and rows[1]['source_id']==2
    assert 3 not in rows


def test_read_projection_remains_consistent_during_concurrent_correction(store, monkeypatch):
    rows={r['id']:r for r in store.load_articles()}
    store.attach_decisions(rows[1],[decision()],rows)
    original=store.get_session
    @contextmanager
    def competing_session():
        with original() as reader:
            class Reader:
                def execute(self, statement, params=None):
                    result=reader.execute(statement, params or {})
                    if 'COUNT(*) AS article_count' in str(statement):
                        with original() as writer:
                            writer.execute(text("UPDATE articles SET body='Corrected story' WHERE id=2"))
                    return result
            yield Reader()
    monkeypatch.setattr(store,'get_session',competing_session)
    result=store.list_agendas()['items']
    assert len(result)==1 and result[0]['article_count']==len(result[0]['articles'])==2


def test_discovery_resume_state_loads_latest_cursor_and_recent_cache(store):
    assert store.get_discovery_state()=={'cursor':0,'known_pair_keys':set(),'cached_pair_keys':set()}
    store.save_decisions([decision()])
    store.record_run('ok',{'discovery_cursor':47})
    assert store.get_discovery_state()=={'cursor':47,'known_pair_keys':set(),'cached_pair_keys':{'a'*64}}
    store.record_run('error',{})
    assert store.get_discovery_state()['cursor']==47


def test_resume_skips_rejected_cache_but_can_replay_accepted_cache(store):
    accepted=decision()
    rejected=decision(choice='unrelated'); rejected['cache_key']='b'*64
    store.save_decisions([accepted,rejected])
    state=store.get_discovery_state()
    assert state['known_pair_keys']=={'b'*64}
    assert state['cached_pair_keys']=={'a'*64,'b'*64}
    with store.get_session() as s:
        s.execute(text("UPDATE news_agenda_decisions SET created_at=now()-interval '73 hours' WHERE cache_key=:key"),{'key':'b'*64})
    assert store.get_discovery_state()['known_pair_keys']==set()


def test_concurrent_groups_cannot_share_an_article(store):
    from concurrent.futures import ThreadPoolExecutor
    rows={r['id']:r for r in store.load_articles()}
    first=decision()
    second=decision(); second['anchor_id']=3
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(store.attach_decisions,rows[d['anchor_id']],[d],rows) for d in (first,second)]
        results=[future.result(timeout=10) for future in futures]
    assert sum(result is not None for result in results)==1
    items=store.list_agendas()['items']
    assert len(items)==1 and items[0]['article_count']==2


def test_recent_id_bound_is_applied_before_eligibility(store):
    assert {a['id'] for a in store.load_articles(limit=2)}=={3}


def test_large_raw_window_reads_bounded_batches_without_replacing_excluded_ids(store, monkeypatch):
    # A monolithic excerpt query (or widening the window to replace quarantined
    # rows) defeats the bounded-work guarantee that protects production reads.
    with store.get_session() as session:
        session.execute(text("""INSERT INTO articles
          (id,source_id,title,body,published_at,collected_at,is_duplicate,geo_status)
          SELECT n,1,'Report number '||n,'Evidence',now(),now(),false,
                 CASE WHEN n%2=0 THEN 'unverified' ELSE 'source_verified' END
          FROM generate_series(5,2010) n"""))
    original=store.get_session
    batches=[]
    @contextmanager
    def observed_session():
        with original() as session:
            class Reader:
                def execute(self, statement, params=None):
                    params=params or {}
                    if store.ARTICLE_FIELDS in str(statement):
                        ids=params.get('ids')
                        assert ids and len(ids)<=1000, 'excerpt reads must have a bounded ID batch'
                        batches.append(list(ids))
                    return session.execute(statement,params)
            yield Reader()
    monkeypatch.setattr(store,'get_session',observed_session)
    rows=store.load_articles(limit=2005)
    assert {r['id'] for r in rows}==set(range(7,2011,2))
    assert len(batches)==3
    assert [identity for batch in batches for identity in batch]==list(range(2010,5,-1))


def test_translation_cache_projects_same_representative_and_invalidates_changed_title(store):
    rows={r['id']:r for r in store.load_articles()}
    store.attach_decisions(rows[1],[decision()],rows)
    store.save_title_translations([{'article_id':2,'source_title':rows[2]['title'],
                                    'title_ru':'Рейс Flydubai сел в Табуке'}], 'test-model')
    item=store.list_agendas()['items'][0]
    assert item['title']==rows[2]['title']
    assert item['title_ru']=='Рейс Flydubai сел в Табуке'
    assert item['articles'][0]['id']==2
    assert item['articles'][0]['title_ru']==item['title_ru']
    assert [a['id'] for a in store.load_translation_candidates()]==[1]
    with store.get_session() as session:
        session.execute(text("UPDATE articles SET title='Corrected Flydubai flight report' WHERE id=2"))
    # A stale response cannot overwrite the cache after a source correction.
    store.save_title_translations([{'article_id':2,'source_title':rows[2]['title'],
                                    'title_ru':'Устаревший перевод'}], 'test-model')
    rows={r['id']:r for r in store.load_articles()}
    store.attach_decisions(rows[1],[decision()],rows)
    item=store.list_agendas()['items'][0]
    assert item['title']=='Corrected Flydubai flight report' and item['title_ru'] is None
    assert [a['id'] for a in store.load_translation_candidates()]==[2,1]


def test_triage_leads_are_translated_without_waiting_for_agenda_group(store):
    with store.get_session() as session:
        session.execute(text("""INSERT INTO article_news_triage
            (article_id,source_title,source_excerpt,classification,model,version)
            SELECT id,title,LEFT(body,2000),
              '{"countries":["RS"],"russia_relation":"uncertain"}'::jsonb,
              'typesafe/jev-1.13','news-triage-v2' FROM articles WHERE id=2"""))
    assert store.list_agendas()['items'] == []
    assert store.load_translation_candidates() == [{'id':2,'title':'Flydubai flight lands in Tabuk'}]
    with store.get_session() as session:
        session.execute(text("UPDATE article_news_triage SET source_excerpt='stale' WHERE article_id=2"))
    assert store.load_translation_candidates() == []


def test_translation_candidates_only_current_memberships_and_cards_first(store):
    rows={r['id']:r for r in store.load_articles()}
    store.attach_decisions(rows[1],[decision()],rows)
    assert [a['id'] for a in store.load_translation_candidates()]==[2,1]
    with store.get_session() as session:
        session.execute(text("UPDATE articles SET collected_at=now()-interval '73 hours' WHERE id IN(1,2)"))
    rows={r['id']:r for r in store.load_articles(hours=168)}
    store.attach_decisions(rows[1],[decision()],rows)
    assert store.load_translation_candidates()==[]


def test_old_translation_cannot_overwrite_concurrent_corrected_title_cache(store,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    import time
    rows={r['id']:r for r in store.load_articles()}
    stale={'article_id':2,'source_title':rows[2]['title'],'title_ru':'Старый перевод'}
    store.save_title_translations([stale],'test-model')
    original=store.get_session
    started=Event()
    pids=[]
    @contextmanager
    def observed_session():
        with original() as session:
            pids.append(session.execute(text('SELECT pg_backend_pid()')).scalar())
            started.set()
            yield session
    with original() as writer:
        writer.execute(text("UPDATE articles SET title='Corrected headline' WHERE id=2"))
        writer.execute(text("UPDATE article_title_translations SET source_title='Corrected headline',title_ru='Исправленный перевод' WHERE article_id=2"))
        monkeypatch.setattr(store,'get_session',observed_session)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(store.save_title_translations,[stale],'test-model')
            try:
                assert started.wait(2)
                deadline=time.monotonic()+3
                blocked=False
                while time.monotonic()<deadline:
                    blocked=writer.execute(text('SELECT cardinality(pg_blocking_pids(:pid))>0'),{'pid':pids[0]}).scalar()
                    if blocked:break
                    time.sleep(.01)
                assert blocked, 'stale saver did not reach the contested row'
            finally:
                writer.commit()
            future.result(timeout=3)
    with original() as reader:
        cached=reader.execute(text('SELECT source_title,title_ru FROM article_title_translations WHERE article_id=2')).one()
    assert tuple(cached)==('Corrected headline','Исправленный перевод')
