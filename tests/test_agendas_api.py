from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.api.routes import agendas as routes


def test_read_projection_validates_limits_and_trims_query(monkeypatch):
    calls=[]
    def listing(**kw):
        calls.append(kw)
        return {'items':[],'coverage':{'status':'never_run'},'has_more':False}
    monkeypatch.setattr(routes,'list_agendas',listing)
    app=FastAPI();app.include_router(routes.router)
    with TestClient(app) as client:
        assert client.get('/api/v2/agendas?limit=51').status_code==422
        assert client.get('/api/v2/agendas',params={'q':'x'*121}).status_code==422
        response=client.get('/api/v2/agendas',params={'limit':3,'q':'  рейс  '})
        assert response.status_code==200
        assert response.json()['coverage']['status']=='never_run'
        assert calls==[{'limit':3,'q':'рейс'}]
