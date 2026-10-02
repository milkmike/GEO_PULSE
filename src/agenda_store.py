"""Persist inspectable news agendas independently of the country/RRI filter."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import text

from src.db import get_session
from src.api.public_urls import safe_public_url

from src.agenda_discovery import MODEL, VERSION, accepted_decision

# Include the decision identity and every state input, plus publisher identity. Epochs
# keep the persisted signature independent of a reader's session time zone.
CONTENT_SQL = f"""md5(jsonb_build_array(
 '{MODEL}', '{VERSION}', ar.id, COALESCE(ar.title,''),
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
 WHERE g.model='{MODEL}' AND m.content_hash=ar.content_hash AND m.anchor_hash=anchor.content_hash
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
        # Freeze the admission window once. Quarantined/old/duplicate IDs are
        # excluded below, never replaced by progressively older archive rows.
        ids = session.execute(text(
            'SELECT id FROM articles ORDER BY id DESC LIMIT :limit'
        ), {'limit':limit}).scalars().all()
        rows=[]
        for offset in range(0,len(ids),1000):
            # The materialization fence makes the primary-key batch precede the
            # date predicate, whose cardinality is underestimated in production.
            # Only bounded excerpts are materialized, not complete article bodies.
            rows.extend(session.execute(text(f"""
             WITH batch AS MATERIALIZED (
               SELECT id,source_id,publisher_source_id,geo_status,is_duplicate,
                      title,published_at,collected_at,resolved_url,url,
                      LEFT(COALESCE(NULLIF(summary,''),body,''),1200) AS summary,
                      NULL::text AS body
               FROM articles WHERE id=ANY(:ids)
             )
             SELECT {ARTICLE_FIELDS} FROM batch ar {PUBLISHER_JOINS}
             WHERE {ELIGIBILITY}
               AND ar.collected_at >= now()-make_interval(hours=>:hours)
               AND ar.collected_at <= now()
            """), {'ids':ids[offset:offset+1000],'hours':hours}).mappings().all())
    return [_article(row) for row in sorted(rows,key=lambda row:(row['collected_at'],row['id']),reverse=True)]



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
            AND stats->>'model'=:model AND stats->>'version'=:version
          ORDER BY id DESC LIMIT 1
        """), {'model':MODEL,'version':VERSION}).scalar_one_or_none()
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


def _grounded_pair(decision, anchor, article):
    if decision.get('confidence_kind') != 'self_reported':
        return True
    sources = {str(anchor['id']):anchor, str(article['id']):article}
    grounded = set()
    for item in decision.get('evidence', []):
        if not isinstance(item, dict):
            return False
        source = sources.get(item.get('article_id'))
        quote = item.get('quote')
        if (not source or not isinstance(quote, str) or not quote
                or (quote not in source['title'] and quote not in source['excerpt'])):
            return False
        grounded.add(item['article_id'])
    return grounded == set(sources)


def attach_decisions(anchor, decisions, articles_by_id):
    # Recheck acceptance and exact article snapshots after the network wait.
    accepted=[d for d in decisions if d.get('model')==MODEL and accepted_decision(d) and d['anchor_id']==anchor['id']
              and d['article_id'] in articles_by_id and d['article_id']!=anchor['id']
              and _grounded_pair(d, anchor, articles_by_id[d['article_id']])]
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
         VALUES(:id,:model) ON CONFLICT(anchor_article_id) DO UPDATE SET model=EXCLUDED.model,updated_at=now() RETURNING id"""),
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
    stats = {**stats, 'model':MODEL, 'version':VERSION}
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
             SELECT valid.*,translation.title_ru FROM valid
             LEFT JOIN article_title_translations translation ON translation.article_id=valid.id
               AND translation.source_title=valid.title
             WHERE agenda_id=:id ORDER BY collected_at DESC,id DESC LIMIT 50'''),
             {'id':group['agenda_id']}).mappings().all()
            if len(evidence)<2:
                continue
            articles=[]
            for row in evidence:
                item=_article(row)
                articles.append({key:item[key] for key in ('id','title','title_ru','source_name','country_code','published_at','collected_at','relation','confidence','date_warning')} | {'url':safe_public_url(item['url'])})
            representative=articles[0]
            items.append(dict(group) | {'id':group['agenda_id'],'title':representative['title'],'title_ru':representative['title_ru'],'articles':articles})
        run=session.execute(text('SELECT status,stats,created_at FROM news_agenda_runs ORDER BY id DESC LIMIT 1')).mappings().first()
    coverage={'last_run_at':None,'status':'never_run','articles_scanned':0,'candidate_groups':0,'decisions':0,'accepted':0,'remaining_budget_usd':None}
    if run:
        coverage.update({k:run['stats'][k] for k in coverage if k in run['stats']})
        coverage.update(status=run['status'],last_run_at=run['created_at'])
    return {'items':items,'coverage':coverage,'has_more':len(groups)>limit}


def load_translation_candidates(limit=400):
    """Unreviewed leads, then agenda representatives; cache hits stay local."""
    if type(limit) is not int or not 1 <= limit <= 400:
        raise ValueError('Invalid translation candidate bound')
    items = list_agendas(limit=50)['items']
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    ordered = [item['articles'][0] for item in items]
    ordered.extend(article for item in items for article in item['articles'][1:])
    from src.news_discovery import MODEL as triage_model, VERSION as triage_version
    with get_session() as session:
        leads = session.execute(text("""
            SELECT ar.id,ar.title FROM article_news_triage nt
            JOIN articles ar ON ar.id=nt.article_id
            JOIN article_country_facts src ON src.article_id=ar.id
            LEFT JOIN article_title_translations tr ON tr.article_id=ar.id AND tr.source_title=ar.title
            WHERE nt.model=:model AND nt.version=:version AND nt.source_title=ar.title
              AND nt.source_excerpt=LEFT(COALESCE(NULLIF(ar.body,''),ar.summary,''),2000)
              AND nt.classification->>'russia_relation' IN ('direct','indirect','uncertain')
              AND jsonb_typeof(nt.classification->'countries')='array'
              AND ar.published_at BETWEEN now()-interval '7 days' AND now()
              AND ar.collected_at<=now() AND ar.is_duplicate IS FALSE
              AND ar.geo_status IN ('source_verified','publisher_verified','publisher_reassigned')
              AND tr.article_id IS NULL
            ORDER BY ar.published_at DESC,ar.id DESC LIMIT :limit
        """), {'model':triage_model,'version':triage_version,'limit':limit}).mappings().all()
    candidates = {r['id']:dict(r) for r in leads}
    for article in ordered:
        if (article['collected_at'] >= cutoff and article['collected_at'] <= datetime.now(timezone.utc)
                and not article.get('title_ru')):
            candidates.setdefault(article['id'], {'id': article['id'], 'title': article['title']})
    return list(candidates.values())[:limit]


def save_title_translations(translations, model):
    """A response for an old source headline cannot replace a current translation."""
    with get_session() as session:
        # Serialize source corrections and competing cache saves; the following
        # statement rechecks source titles after any concurrent writer commits.
        session.execute(text('SELECT id FROM articles WHERE id=ANY(:ids) ORDER BY id FOR UPDATE'),
                        {'ids':sorted({item['article_id'] for item in translations})}).all()
        for item in translations:
            session.execute(text("""INSERT INTO article_title_translations
              (article_id,source_title,title_ru,model)
              SELECT id,title,:title_ru,:model FROM articles
              WHERE id=:article_id AND title=:source_title
              ON CONFLICT(article_id) DO UPDATE SET source_title=EXCLUDED.source_title,
                title_ru=EXCLUDED.title_ru,model=EXCLUDED.model,translated_at=now()
            """), {**item, 'model':model})
