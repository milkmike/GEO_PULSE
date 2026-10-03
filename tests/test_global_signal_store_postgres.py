"""A model change cannot claim or count private work from the previous model."""
from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src import global_signal_store as store
from src.early_signals import source_key
from tests.test_early_signals import article


@pytest.fixture
def database(monkeypatch):
    url = os.getenv('GEO_PULSE_TEST_DATABASE_URL')
    if not url:
        pytest.skip('isolated PostgreSQL test database required')
    admin = create_engine(url)
    schema = 'signal_model_test_' + uuid4().hex
    with admin.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA {schema}')
    engine = create_engine(url, connect_args={'options': f'-csearch_path={schema}'})
    with engine.begin() as conn:
        conn.exec_driver_sql(Path('scripts/migrations/039_early_signal_dossiers.sql').read_text())
        conn.exec_driver_sql(Path('scripts/migrations/040_global_signal_monitor.sql').read_text())
    @contextmanager
    def session():
        with Session(engine) as db:
            with db.begin(): yield db
    monkeypatch.setattr(store, 'get_session', session)
    monkeypatch.setattr(store.dossiers, 'get_session', session)
    yield engine
    engine.dispose()
    with admin.begin() as conn:
        conn.exec_driver_sql(f'DROP SCHEMA {schema} CASCADE')
    admin.dispose()


def test_old_model_work_is_not_counted_or_claimed(database):
    import json
    now = datetime.now(timezone.utc)
    row = article(published_at=now, collected_at=now)
    key = source_key(row)
    context = {'as_of': now.isoformat(), 'articles': [row]}
    with database.begin() as conn:
        conn.execute(text('''INSERT INTO early_signal_work
            (id,source_key,planner_version,article_id,country_code,status,context,newest_published_at)
            VALUES(:id,:key,'global-context-v1',1,'RS','context_ready',CAST(:context AS jsonb),:now)'''),
            {'id': 'a' * 64, 'key': key, 'context': json.dumps(context, default=str), 'now': now})
    assert store.queue_summary(as_of=now) == {}
    assert store.claim_context(as_of=now, current_articles=[row]) is None
    with database.begin() as conn:
        conn.execute(text('UPDATE early_signal_work SET planner_version=:version'), {'version': store.PLANNER_VERSION})
    assert store.queue_summary(as_of=now) == {'RS': {'context_ready': 1}}
    assert store.claim_context(as_of=now, current_articles=[row])['id'].strip() == 'a' * 64


def test_chat_screening_is_saved_and_reloaded_with_its_audit_evidence(database, monkeypatch):
    from decimal import Decimal
    from src import early_signal_worker as worker
    from src import early_signals as screening
    from src import decision_model
    now = datetime.now(timezone.utc)
    row = article(country='US', title='Молдова обсуждает поставки газа',
                  excerpt='Молдова обсуждает поставки газа. Решение пока не принято.',
                  published_at=now, collected_at=now)
    monkeypatch.setenv(decision_model.KEY_ENV, 'isolated-test-only')
    monkeypatch.setattr(worker.budget, 'get_attempted_pair_keys', lambda *a: set())
    monkeypatch.setattr(worker.budget, 'reserve_request', lambda *a, **kw: 'test-reservation')
    monkeypatch.setattr(worker.budget, 'get_budget', lambda *a: 2)
    settled = []
    monkeypatch.setattr(worker.budget, 'finish_request', lambda *a: settled.append(a))
    monkeypatch.setattr(worker, 'track_api_call', lambda **kw: None)
    monkeypatch.setattr(worker, 'check_tariff', lambda: None)
    def response(payload, *args):
        choices = dict(signal='change', mechanism='trade', stage='proposal', country='MD')
        answers = {key: {'type':'choice', 'choice':choices[key.rsplit('_',1)[-1]],
                   'confidence':.95, 'confidence_kind':'self_reported', 'evidence':[
                       {'article_id':'article_1','quote':'Молдова обсуждает поставки газа.'}]}
                   for key in payload['questions']}
        return {'status':'ok','data':{'answers':answers,'usage':{'cost':.001}}}
    monkeypatch.setattr(worker, '_request', response)
    stats = worker.run_screening_cycle(articles=[row], as_of=now, budget_usd=Decimal('2'))
    assert stats['status'] == 'ok' and stats['screened'] == 1
    saved = store.dossiers.load_screenings([screening.source_key(row)])
    classification = next(iter(saved.values()))['classification']
    assert classification['country'] == 'MD' and classification['model'] == screening.MODEL
    assert classification['decisions']['country']['probabilities'] == {}
    assert classification['decisions']['country']['evidence'][0]['quote'] in row['excerpt']
    assert settled == [('test-reservation', .001, 'ok')]
