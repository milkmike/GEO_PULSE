"""Private, idempotent signal work and truthful collection snapshots."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json

from sqlalchemy import text

from src.db import get_session
from src import early_signal_store as dossiers
from src.early_signals import MODEL, VERSION, source_key
from src.signal_workbench import instant

PLANNER_VERSION = f'global-context-v2:{MODEL}:{VERSION}'


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def work_id(key):
    return hashlib.sha256((PLANNER_VERSION + ':' + key).encode()).hexdigest()


def known_work_keys():
    with get_session() as session:
        return set(str(key).strip() for key in session.execute(text(
            'SELECT source_key FROM early_signal_work WHERE planner_version=:version'),
            {'version': PLANNER_VERSION}).scalars().all())


def current_screenings(articles):
    """Only exact current snapshots, filtering the whole bounded sample before selection."""
    keys = []
    for article in articles:
        try:
            keys.append(source_key(article))
        except (ValueError, KeyError, TypeError):
            continue
    result = {}
    for offset in range(0, len(keys), 80):
        result.update(dossiers.load_screenings(keys[offset:offset + 80]))
    return list(result.values())


def sync_candidates(candidates, *, as_of, current_articles=None):
    """Re-running the monitor cannot reopen attempted drafts or editorial releases."""
    now = instant(as_of)
    saved = 0
    with get_session() as session:
        if current_articles:
            current = [{'article_id': article['id'], 'source_key': source_key(article)} for article in current_articles]
            session.execute(text("""UPDATE early_signal_work work
              SET status='expired',last_error='source_changed',updated_at=now()
              FROM jsonb_to_recordset(CAST(:sources AS jsonb)) AS current(article_id bigint,source_key text)
              WHERE work.article_id=current.article_id AND work.source_key<>current.source_key
                AND work.status IN ('needs_geography','needs_context','context_ready')"""), {'sources': _json(current)})
        session.execute(text("""UPDATE early_signal_work SET status='expired',updated_at=now()
          WHERE newest_published_at<:cutoff AND status IN
            ('needs_geography','needs_context','context_ready')"""), {'cutoff': now - timedelta(days=7)})
        # A lost process cannot silently retry a request whose charge is unknown.
        session.execute(text("""UPDATE early_signal_work SET status='blocked',last_error='interrupted_draft',updated_at=now()
          WHERE status='drafting' AND attempted_at<:cutoff"""), {'cutoff': now - timedelta(hours=2)})
        for item in candidates:
            anchor = item['anchor']
            key = item['source_key']
            if key != source_key(anchor) or item['status'] not in ('needs_geography', 'needs_context', 'context_ready'):
                raise ValueError('invalid global candidate')
            context = item.get('context')
            if item['status'] == 'context_ready' and not context:
                raise ValueError('context missing')
            code = item['country_code']
            session.execute(text("""
              INSERT INTO early_signal_work(id,source_key,planner_version,article_id,country_code,status,
                                            context,retrieval_links,newest_published_at)
              VALUES(:id,:key,:version,:article_id,:country,:status,CAST(:context AS jsonb),
                     CAST(:links AS jsonb),:published)
              ON CONFLICT(source_key,planner_version) DO UPDATE SET
                country_code=EXCLUDED.country_code,status=EXCLUDED.status,context=EXCLUDED.context,
                retrieval_links=EXCLUDED.retrieval_links,updated_at=now()
              WHERE early_signal_work.status IN ('needs_geography','needs_context','context_ready')
                AND early_signal_work.attempted_at IS NULL
            """), {'id': work_id(key), 'key': key, 'version': PLANNER_VERSION,
                    'article_id': anchor['id'], 'country': code if code not in ('none', 'unknown') else None,
                    'status': item['status'], 'context': _json(context) if context is not None else None,
                    'links': _json(item.get('retrieval_links', [])),
                    'published': instant(anchor['published_at'])})
            saved += 1
    return saved


def claim_context(*, as_of, current_articles):
    """One claim per source version; countries least recently attempted go first."""
    current = {article['id']: source_key(article) for article in current_articles}
    with get_session() as session:
        rows = session.execute(text("""
          SELECT work.id,work.context FROM early_signal_work work
          LEFT JOIN (SELECT country_code,MAX(attempted_at) AS last_attempt
                     FROM early_signal_work WHERE attempted_at IS NOT NULL GROUP BY country_code) prior
            ON prior.country_code=work.country_code
          WHERE work.status='context_ready' AND work.attempted_at IS NULL
            AND work.planner_version=:version
            AND work.newest_published_at>=:cutoff AND work.newest_published_at<=:as_of
          ORDER BY prior.last_attempt ASC NULLS FIRST,work.created_at,work.id
          LIMIT 80 FOR UPDATE OF work SKIP LOCKED
        """), {'as_of': instant(as_of), 'cutoff': instant(as_of) - timedelta(days=7),
                'version': PLANNER_VERSION}).mappings().all()
        for row in rows:
            context = json.loads(row['context']) if isinstance(row['context'], str) else row['context']
            evidence = context['articles']
            # A missing sampled row is not proof of deletion. Defer it; never
            # pay for evidence whose current bytes could not be checked.
            if any(article['id'] not in current for article in evidence):
                continue
            if any(current[article['id']] != source_key(article) for article in evidence):
                session.execute(text("""UPDATE early_signal_work SET status='needs_context',context=NULL,
                  last_error='context_changed',updated_at=now() WHERE id=:id"""), {'id': row['id']})
                continue
            session.execute(text("""UPDATE early_signal_work SET status='drafting',attempted_at=now(),updated_at=now()
              WHERE id=:id AND status='context_ready' AND attempted_at IS NULL"""), {'id': row['id']})
            return {'id': row['id'], 'context': context}
    return None


def save_private_draft(item, result):
    context = item['context']
    # Explicitly draft: no public release and no fabricated review note.
    draft_id = dossiers.save_dossier(result['draft'], context['articles'], instant(context['as_of']))
    with get_session() as session:
        session.execute(text("""UPDATE early_signal_work SET status='needs_review',draft_id=:draft,updated_at=now()
          WHERE id=:id AND status='drafting'"""), {'id': item['id'], 'draft': draft_id})
    return draft_id


def block_work(item, reason):
    if reason not in ('draft_failed', 'interrupted_draft'):
        raise ValueError('invalid work error')
    with get_session() as session:
        session.execute(text("""UPDATE early_signal_work SET status='blocked',last_error=:reason,updated_at=now()
          WHERE id=:id AND status='drafting'"""), {'id': item['id'], 'reason': reason})


def queue_summary(*, as_of):
    with get_session() as session:
        rows = session.execute(text("""SELECT country_code,status,count(*) AS count FROM early_signal_work
          WHERE newest_published_at>=:cutoff AND newest_published_at<=:as_of
            AND planner_version=:version
          GROUP BY country_code,status"""),
          {'as_of': instant(as_of), 'cutoff': instant(as_of) - timedelta(days=7),
           'version': PLANNER_VERSION}).mappings().all()
    result = {}
    for row in rows:
        result.setdefault(row['country_code'] or 'unknown', {})[row['status']] = row['count']
    return result


def save_monitor_run(payload):
    if 'articles' in payload:
        raise ValueError('raw source text cannot enter coverage snapshot')
    with get_session() as session:
        session.execute(text("""INSERT INTO global_monitor_runs(as_of,payload)
          VALUES(:as_of,CAST(:payload AS jsonb))"""),
          {'as_of': instant(payload['as_of']), 'payload': _json(payload)})
        # Keep six weeks of diagnostics; source-bound work lives separately.
        session.execute(text('DELETE FROM global_monitor_runs WHERE as_of<:cutoff'),
                        {'cutoff': instant(payload['as_of']) - timedelta(days=42)})


def latest_monitor():
    with get_session() as session:
        row = session.execute(text("SELECT payload FROM global_monitor_runs ORDER BY as_of DESC,id DESC LIMIT 1")).scalar_one_or_none()
    if row is None:
        return {'as_of': None, 'status': 'not_started', 'countries': [], 'scope_count': 0}
    payload = json.loads(row) if isinstance(row, str) else row
    now = datetime.now(timezone.utc)
    return {**payload, 'stale': instant(payload['as_of']) < now - timedelta(hours=2)}
