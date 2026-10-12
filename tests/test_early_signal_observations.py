from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from copy import deepcopy
import hashlib

from fastapi.testclient import TestClient

from src import early_signal_observations as observations
from src import early_signal_store as store
from src.early_signals import MODEL, VERSION, source_key
from src.signal_workbench import article_snapshot
from src.api.main import app
from tests.test_early_signals import article

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def screening(i, event='RS', publisher='GE'):
    a = article_snapshot(article(i, country=publisher, published_at=NOW-timedelta(hours=3), collected_at=NOW), screened=True)
    quote = a['excerpt']
    labels={'signal':'change','mechanism':'education','stage':'proposal','country':event}
    decisions={key:{'type':'choice','choice':value,'confidence':.95,
                    'confidence_kind':'self_reported','probabilities':{},
                    'evidence':[{'article_id':f'article_{i}','quote':quote}]}
               for key,value in labels.items()}
    return dict(source_key=source_key(a), snapshot_hash=hashlib.sha256(store._json(a).encode()).hexdigest(),
                article=a, classification={**labels,'status':'needs_review','decisions':decisions,
                                           'model':MODEL,'version':VERSION})


def row(record, **changes):
    a = record['article']
    return dict(record, current_article=deepcopy(a), title_ru='Новый учебный обмен',
                translation_source_title=a['title'], annotation_source_title=None,
                annotation_source_excerpt=None, annotation=None) | changes


def test_single_source_observation_is_provisional_and_exactly_bound():
    r = row(screening(1))
    item = observations.project_row(r, NOW)
    assert item['type'] == 'observation' and item['status'] == 'provisional'
    assert item['country_code'] == 'RS' and item['publisher_country_code'] == 'GE'
    assert item['title_ru'] == 'Новый учебный обмен'
    assert item['title_origin'] == 'machine_translation'
    assert item['evidence_quote'] in (r['article']['title'] + '\n' + r['article']['excerpt'])
    assert item['source_url'] == r['article']['url']
    assert 'confirmation' not in str(item).lower()


def test_changed_source_unknown_geography_and_untranslated_title():
    r = row(screening(2), current_article=article(2, country='GE', title='Corrected headline',
                published_at=NOW-timedelta(hours=3), collected_at=NOW))
    assert observations.project_row(r, NOW) is None
    r = row(screening(3, event='unknown'))
    assert observations.project_row(r, NOW) is None
    r = row(screening(4), title_ru='Гарбар', translation_source_title='Old title')
    item = observations.project_row(r, NOW)
    assert item['title_ru'] is None and item['title_origin'] == 'unavailable'
    assert item['source_title'] == r['article']['title']
    assert item['label_ru']


def test_cyrillic_source_title_requires_russian_language_tag():
    r=row(screening(30),title_ru=None)
    r['article']['title']='Српски наслов'
    r['current_article']['title']='Српски наслов'
    r['source_key']=source_key(r['article'])
    r['snapshot_hash']=hashlib.sha256(store._json(r['article']).encode()).hexdigest()
    r['current_article']['language']='sr'
    item=observations.project_row(r,NOW)
    assert item['title_ru'] is None and item['title_origin']=='unavailable'
    r['current_article']['language']='ru'
    item=observations.project_row(r,NOW)
    assert item['title_ru']=='Српски наслов' and item['title_origin']=='source'


def test_rejects_stale_future_invalid_url_and_nonchange():
    for modified in [
        {'article': article(5, country='GE', published_at=NOW-timedelta(days=8), collected_at=NOW)},
        {'article': article(5, country='GE', published_at=NOW+timedelta(hours=1), collected_at=NOW+timedelta(hours=1))},
        {'article': article(5, country='GE', url='http://127.0.0.1/private', published_at=NOW-timedelta(hours=3), collected_at=NOW)},
    ]:
        base = screening(5)
        a = article_snapshot(modified['article'], screened=True)
        base.update(article=a, source_key=source_key(a) if a['url'].startswith('https://') else base['source_key'],
                    snapshot_hash=hashlib.sha256(store._json(a).encode()).hexdigest())
        assert observations.project_row(row(base), NOW) is None
    r = row(screening(6))
    r['classification']['signal'] = 'routine'
    assert observations.project_row(r, NOW) is None
    r = row(screening(7))
    r['classification'] = {key:r['classification'][key] for key in
                           ('signal','mechanism','stage','country','status')}
    assert observations.project_row(r, NOW) is None
    r = row(screening(8))
    r['classification']['decisions']['country']['choice'] = 'GE'
    assert observations.project_row(r, NOW) is None


def test_world_fairness_and_bounded_readonly_query(monkeypatch):
    records = [row(screening(i, 'RS')) for i in range(1, 10)] + [row(screening(20, 'GE'))]
    class Result:
        def mappings(self): return self
        def all(self): return records
    class Session:
        def __init__(self): self.sql=[]
        def execute(self, statement, params=None):
            self.sql.append((str(statement), params))
            return Result()
    session=Session()
    @contextmanager
    def fake_session(): yield session
    monkeypatch.setattr(observations, 'get_session', fake_session)
    assert [x['country_code'] for x in observations.list_observations(as_of=NOW, limit=2)] == ['GE','RS']
    assert len(session.sql)==1 and 'LIMIT' in session.sql[0][0]
    assert session.sql[0][1]['country'] is None
    assert 'INSERT' not in session.sql[0][0]


def test_world_rotation_reaches_countries_after_first_page(monkeypatch):
    from src.monitoring_registry import MONITORING_COUNTRIES
    codes=[code for code in sorted(MONITORING_COUNTRIES) if code != 'RU'][:14]
    records=[row(screening(index+1,code)) for index,code in enumerate(codes)]
    class Result:
        def mappings(self): return self
        def all(self): return records
    class Session:
        def execute(self,*a): return Result()
    @contextmanager
    def fake_session(): yield Session()
    monkeypatch.setattr(observations,'get_session',fake_session)
    first={item['country_code'] for item in observations.list_observations(as_of=NOW,limit=6)}
    later={item['country_code'] for item in observations.list_observations(as_of=NOW+timedelta(hours=1),limit=6)}
    assert len(first)==len(later)==6
    assert first.isdisjoint(later)


def test_endpoint_keeps_dossiers_and_adds_observations_without_models(monkeypatch):
    from src.api.routes import early_signals as route
    monkeypatch.setattr(route, 'list_dossiers', lambda **kw: {'as_of':NOW.isoformat(),'items':[{'id':'reviewed'}],'notice':'notice'})
    monkeypatch.setattr(route, 'list_observations', lambda **kw: [{'type':'observation','country_code':kw['country']}])
    result=TestClient(app).get('/api/v2/early-signals?country=RS').json()
    assert result['items']==[{'id':'reviewed'}]
    assert result['observations']==[{'type':'observation','country_code':'RS'}]
