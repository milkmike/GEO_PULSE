"""Read-only evidence projection for article-first news agendas."""
from fastapi import APIRouter, Query
from src.agenda_store import list_agendas

router=APIRouter(prefix='/api/v2',tags=['agendas'])

@router.get('/agendas')
def agendas(limit:int=Query(20,ge=1,le=50),q:str=Query('',max_length=120)):
    return list_agendas(limit=limit,q=q.strip())
