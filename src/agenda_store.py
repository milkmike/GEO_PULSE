"""Persist inspectable news agendas independently of the country/RRI filter."""
from __future__ import annotations

import json
from datetime import timedelta
from sqlalchemy import text

from src.db import get_session
from src.api.public_urls import safe_public_url

from src.agenda_discovery import VERSION, accepted_decision

MODEL = 'typesafe/jev-1.13'
# Conservatively include every Jev state input, plus publisher identity. Epochs
# keep the persisted signature independent of a reader's session time zone.
CONTENT_SQL = f"""md5(jsonb_build_array(
 '{VERSION}', ar.id, COALESCE(ar.title,''),
 LEFT(COALESCE(NULLIF(ar.summary,''),ar.body,''),1200),
 EXTRACT(EPOCH FROM ar.published_at),EXTRACT(EPOCH FROM ar.collected_at),
 src.id,TRIM(src.country_code))::text)"""
ARTICLE_FIELDS = f"""ar.id, ar.title,
 LEFT(COALESCE(NULLIF(ar.summary,''),ar.body,''),1200) AS excerpt,
 ar.published_at,ar.collected_at,COALESCE(ar.resolved_url,ar.url) AS url,
 src.id AS source_id,src.name AS source_name,TRIM(src.country_code) AS country_code,
 {CONTENT_SQL} AS content_hash"""
# This is the exact publisher resolution and quarantine contract of migration
# 024, applied directly to the bounded article rows instead of rescanning them.
PUBLISHER_JOINS = """
 JOIN sources discovery ON discovery.id=ar.source_id
 JOIN sources src ON src.id=CASE
   WHEN COALESCE(discovery.config->>'feed_mode','publisher')='publisher_discovery'
   THEN ar.publisher_source_id ELSE COALESCE(ar.publisher_source_id,ar.source_id) END
"""
ELIGIBILITY = """
 ar.geo_status IN ('source_verified','publisher_verified','publisher_reassigned')
 AND (COALESCE(discovery.config->>'feed_mode','publisher')<>'publisher_discovery'
      OR ar.publisher_source_id IS NOT NULL)
 AND NOT ar.is_duplicate AND ar.collected_at IS NOT NULL
 AND LENGTH(TRIM(COALESCE(ar.title,'')))>=8
"""
CURRENT_ARTICLES = f'SELECT {ARTICLE_FIELDS} FROM articles ar {PUBLISHER_JOINS} WHERE {ELIGIBILITY}'
VALID_MEMBERS = f"""
 SELECT ar.*,m.agenda_id,m.relation,m.confidence,g.model,g.updated_at,g.anchor_article_id
 FROM news_agenda_articles m JOIN news_agendas g ON g.id=m.agenda_id
 JOIN ({CURRENT_ARTICLES}) ar ON ar.id=m.article_id
 JOIN ({CURRENT_ARTICLES}) anchor ON anchor.id=g.anchor_article_id
 JOIN news_agenda_articles seed ON seed.article_id=g.anchor_article_id
   AND seed.agenda_id=g.id AND seed.relation='seed'
 WHERE m.content_hash=ar.content_hash AND m.anchor_hash=anchor.content_hash
   AND seed.content_hash=anchor.content_hash AND seed.anchor_hash=anchor.content_hash
"""


def _article(row):
    result = dict(row)
    published, collected = result['published_at'], result['collected_at']
    result['date_warning'] = published is None or published > collected + timedelta(hours=1) or published < collected - timedelta(days=7)
    result['effective_time'] = collected if result['date_warning'] else published
    return result


def load_articles(hours=72, limit=30000):
    if not 1 <= hours <= 168 or not 1 <= limit <= 30000:
        raise ValueError('Invalid agenda collection bounds')
    with get_session() as session:
        session.execute(text("SET LOCAL statement_timeout='15s'"))
        rows = session.execute(text(f"""
         WITH recent AS MATERIALIZED (SELECT id FROM articles ORDER BY id DESC LIMIT :limit)
         SELECT {ARTICLE_FIELDS} FROM recent
         JOIN articles ar ON ar.id=recent.id {PUBLISHER_JOINS}
         WHERE {ELIGIBILITY}
           AND ar.collected_at >= now()-make_interval(hours=>:hours)
           AND ar.collected_at <= now()
         ORDER BY ar.collected_at DESC,ar.id DESC
        """), {'limit':limit,'hours':hours}).mappings().all()
    return [_article(row) for row in rows]


def load_groups(hours=72):
    with get_session() as session:
        session.execute(text("SET LOCAL statement_timeout='15s'"))
        rows = session.execute(text(f"""
         SELECT * FROM ({VALID_MEMBERS}) valid
         WHERE updated_at >= now()-make_interval(hours=>:hours)
         ORDER BY agenda_id,id
        """), {'hours':hours}).mappings().all()
    groups = {}
    for row in rows:
        item = _article(row)
        group = groups.setdefault(row['agenda_id'], {'id':row['agenda_id'],'anchor':None,'articles':[]})
        if row['id']==row['anchor_article_id']:
            group['anchor']=item
        group['articles'].append(item)
    return [g for g in groups.values() if g['anchor']]


def get_discovery_state():
    """Resume the latest valid cursor; replay accepted cache after partial saves."""
    with get_session() as session:
        cursor = session.execute(text("""
          SELECT stats->>'discovery_cursor' FROM news_agenda_runs
          WHERE jsonb_typeof(stats->'discovery_cursor')='number'
            AND stats->>'discovery_cursor' ~ '^[0-9]{1,10}$'
          ORDER BY id DESC LIMIT 1
        """)).scalar_one_or_none()
        rows = session.execute(text("""
          SELECT cache_key,decision FROM news_agenda_decisions
          WHERE created_at>=now()-interval '72 hours'
          ORDER BY created_at DESC,cache_key LIMIT 50000
        """)).mappings().all()
    return {'cursor':int(cursor or 0),
            'known_pair_keys':{row['cache_key'] for row in rows if not accepted_decision(row['decision'])},
            'cached_pair_keys':{row['cache_key'] for row in rows}}


def get_cached_decisions(keys):
    if not keys:
        return {}
    with get_session() as session:
        rows=session.execute(text('SELECT cache_key,decision FROM news_agenda_decisions WHERE cache_key=ANY(:keys)'),{'keys':keys}).mappings().all()
    return {row['cache_key']:row['decision'] for row in rows}


def save_decisions(decisions):
    with get_session() as session:
        for item in decisions:
            session.execute(text('''INSERT INTO news_agenda_decisions(cache_key,anchor_id,article_id,decision)
             VALUES(:cache_key,:anchor_id,:article_id,CAST(:decision AS jsonb)) ON CONFLICT(cache_key) DO NOTHING'''),
             {**item,'decision':json.dumps(item)})


def attach_decisions(anchor, decisions, articles_by_id):
    # Recheck acceptance and exact article snapshots after the network wait.
    accepted=[d for d in decisions if accepted_decision(d) and d['anchor_id']==anchor['id']
              and d['article_id'] in articles_by_id and d['article_id']!=anchor['id']]
    if not accepted:
        return None
    with get_session() as session:
        ids=sorted({anchor['id'], *(d['article_id'] for d in accepted)})
        session.execute(text('SELECT id FROM articles WHERE id=ANY(:ids) ORDER BY id FOR UPDATE'),{'ids':ids}).all()
        current=session.execute(text(f'SELECT * FROM ({CURRENT_ARTICLES}) current WHERE id=ANY(:ids)'),{'ids':ids}).mappings().all()
        hashes={r['id']:r['content_hash'] for r in current}
        if hashes.get(anchor['id'])!=anchor['content_hash']:
            return None
        accepted=[d for d in accepted if hashes.get(d['article_id'])==articles_by_id[d['article_id']]['content_hash']]
        if not accepted:
            return None
        # Reclaim only invalidated evidence. Valid memberships are never bridges
        # between agendas, including when another writer attaches concurrently.
        session.execute(text(f"""
          DELETE FROM news_agenda_articles stale WHERE stale.article_id=ANY(:ids)
          AND NOT EXISTS (SELECT 1 FROM ({VALID_MEMBERS}) valid WHERE valid.id=stale.article_id)
        """),{'ids':ids})
        owners=dict(session.execute(text("""SELECT m.article_id,g.anchor_article_id
          FROM news_agenda_articles m JOIN news_agendas g ON g.id=m.agenda_id
          WHERE m.article_id=ANY(:ids)"""),{'ids':ids}).all())
        if anchor['id'] in owners and owners[anchor['id']]!=anchor['id']:
            return None
        accepted=[d for d in accepted if owners.get(d['article_id'],anchor['id'])==anchor['id']]
        if not accepted:
            return None
        gid=session.execute(text("""INSERT INTO news_agendas(anchor_article_id,model)
         VALUES(:id,:model) ON CONFLICT(anchor_article_id) DO UPDATE SET updated_at=now() RETURNING id"""),
         {'id':anchor['id'],'model':MODEL}).scalar_one()
        members=[(anchor,'seed',None,{})]+[(articles_by_id[d['article_id']],d['choice'],d['confidence'],d['probabilities']) for d in accepted]
        for article,relation,confidence,probabilities in members:
            session.execute(text("""INSERT INTO news_agenda_articles
             (article_id,agenda_id,relation,confidence,probabilities,content_hash,anchor_hash)
             VALUES(:article_id,:agenda_id,:relation,:confidence,CAST(:probabilities AS jsonb),:content_hash,:anchor_hash)
             ON CONFLICT(article_id) DO UPDATE SET relation=EXCLUDED.relation,confidence=EXCLUDED.confidence,
             probabilities=EXCLUDED.probabilities,content_hash=EXCLUDED.content_hash,anchor_hash=EXCLUDED.anchor_hash
             WHERE news_agenda_articles.agenda_id=EXCLUDED.agenda_id"""),
             {'article_id':article['id'],'agenda_id':gid,'relation':relation,'confidence':confidence,
              'probabilities':json.dumps(probabilities),'content_hash':article['content_hash'],'anchor_hash':anchor['content_hash']})
        return gid


def record_run(status, stats):
    with get_session() as session:
        session.execute(text('INSERT INTO news_agenda_runs(status,stats) VALUES(:status,CAST(:stats AS jsonb))'),
                        {'status':status,'stats':json.dumps(stats,default=str)})


def list_agendas(limit=20, q=''):
    query='%' + q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
    with get_session() as session:
        session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        session.execute(text("SET LOCAL statement_timeout='15s'"))
        groups=session.execute(text(f'''WITH valid AS ({VALID_MEMBERS})
         SELECT agenda_id,COUNT(*) AS article_count,COUNT(DISTINCT source_id) AS source_count,
          array_agg(DISTINCT country_code ORDER BY country_code) AS countries,
          MIN(collected_at) AS first_seen,MAX(collected_at) AS last_seen,MAX(updated_at) AS updated_at,
          COUNT(*) FILTER(WHERE relation='same_event') AS same_event_count,
          COUNT(*) FILTER(WHERE relation='development') AS development_count,MAX(model) AS model
         FROM valid GROUP BY agenda_id HAVING COUNT(*)>=2 AND bool_or(title ILIKE :q)
         ORDER BY MAX(collected_at) DESC,COUNT(DISTINCT source_id) DESC,agenda_id DESC LIMIT :limit
        '''),{'limit':limit+1,'q':query}).mappings().all()
        items=[]
        for group in groups[:limit]:
            evidence=session.execute(text(f'''WITH valid AS ({VALID_MEMBERS})
             SELECT * FROM valid WHERE agenda_id=:id ORDER BY collected_at DESC,id DESC LIMIT 50'''),
             {'id':group['agenda_id']}).mappings().all()
            if len(evidence)<2:
                continue
            articles=[]
            for row in evidence:
                item=_article(row)
                articles.append({key:item[key] for key in ('id','title','source_name','country_code','published_at','collected_at','relation','confidence','date_warning')} | {'url':safe_public_url(item['url'])})
            representative=next((a for a in articles if any('а'<=c.lower()<='я' for c in a['title'])),articles[0])
            items.append(dict(group) | {'id':group['agenda_id'],'title':representative['title'],'articles':articles})
        run=session.execute(text('SELECT status,stats,created_at FROM news_agenda_runs ORDER BY id DESC LIMIT 1')).mappings().first()
    coverage={'last_run_at':None,'status':'never_run','articles_scanned':0,'candidate_groups':0,'decisions':0,'accepted':0,'remaining_budget_usd':None}
    if run:
        coverage.update({k:run['stats'][k] for k in coverage if k in run['stats']})
        coverage.update(status=run['status'],last_run_at=run['created_at'])
    return {'items':items,'coverage':coverage,'has_more':len(groups)>limit}
