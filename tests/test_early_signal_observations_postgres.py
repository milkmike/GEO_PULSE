"""PostgreSQL outcome: current source filtering precedes country page ranking."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import hashlib
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src import early_signal_observations as observations
from src import early_signal_store as store
from src.early_signals import source_key
from src.signal_workbench import article_snapshot
from tests.test_early_signal_observations import screening


@pytest.fixture
def database(monkeypatch):
    url=os.environ.get('GEO_PULSE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('isolated GEO_PULSE_TEST_DATABASE_URL required')
    admin=create_engine(url)
    schema='signal_observations_test_'+uuid4().hex
    with admin.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA {schema}')
    engine=create_engine(url,connect_args={'options':f'-csearch_path={schema} -cTimeZone=Europe/Moscow'})
    with engine.begin() as conn:
        conn.exec_driver_sql('''CREATE TABLE sources(id bigint primary key,name text,country_code text,config jsonb default '{}');
        CREATE TABLE articles(id bigint primary key,source_id bigint,title text,language text,body text,summary text,
          url text,resolved_url text,published_at timestamptz,collected_at timestamptz,
          is_duplicate bool default false,publisher_source_id bigint,geo_status text default 'source_verified',
          geo_country_code text);''')
        for migration in ('036_article_title_translations.sql','037_article_decision_annotations.sql',
                          '039_early_signal_dossiers.sql'):
            conn.exec_driver_sql(Path('scripts/migrations',migration).read_text())
    @contextmanager
    def session():
        with Session(engine) as db:
            with db.begin():
                yield db
    monkeypatch.setattr(observations,'get_session',session)
    yield engine
    engine.dispose()
    with admin.begin() as conn:
        conn.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
    admin.dispose()


def test_changed_screenings_cannot_hide_valid_current_source(database):
    # Keep the fractional timestamp in the past; replacing microseconds on the
    # current second can accidentally trigger the future-as-of rejection.
    now=(datetime.now(timezone.utc)-timedelta(seconds=1)).replace(microsecond=123400)
    valid=screening(1,event='RS',publisher='GE')
    a=dict(valid['article'],published_at=(now-timedelta(hours=1)).isoformat(),collected_at=now.isoformat())
    a=article_snapshot(a,screened=True)
    valid.update(article=a,source_key=source_key(a),snapshot_hash=hashlib.sha256(store._json(a).encode()).hexdigest())
    with database.begin() as conn:
        conn.execute(text("INSERT INTO sources(id,name,country_code) VALUES(1,'Gazette','GE')"))
        conn.execute(text('''INSERT INTO articles(id,source_id,title,body,url,published_at,collected_at,geo_country_code)
          VALUES(1,1,:title,:body,:url,:published,:collected,'GE')'''),
          {'title':a['title'],'body':a['excerpt'],'url':a['url'],
           'published':now-timedelta(hours=1),'collected':now})
        entries=[]
        for index in range(12):
            changed=dict(a,title=f'Changed version {index}')
            entries.append({'key':source_key(changed),'id':1,
                            'snapshot':hashlib.sha256(store._json(changed).encode()).hexdigest(),
                            'article':store._json(changed),'classification':store._json(valid['classification']),
                            'screened':now-timedelta(minutes=index+1)})
        entries.append({'key':valid['source_key'],'id':1,'snapshot':valid['snapshot_hash'],
                        'article':store._json(a),'classification':store._json(valid['classification']),
                        'screened':now-timedelta(minutes=30)})
        conn.execute(text('''INSERT INTO early_signal_screenings
           (source_key,article_id,snapshot_hash,article,classification,screened_at)
           VALUES(:key,:id,:snapshot,CAST(:article AS jsonb),CAST(:classification AS jsonb),:screened)'''),entries)
    items=observations.list_observations(country='RS',limit=1,as_of=now)
    assert len(items)==1 and items[0]['source_fingerprint']==valid['source_key']
