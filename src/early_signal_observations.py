"""Read-only, source-bound provisional observations from screened reports.

A screening is a lead, not an independently verified event or forecast. This
projection retains the literal source and refuses changed or expired snapshots.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
import hashlib
import re

from sqlalchemy import text

from src.db import get_session
from src import decision_extraction, decision_model
from src.early_signal_store import _json, _loaded, _valid_classification
from src.early_signals import MECHANISM, SIGNAL, STAGE, source_key, _safe_url
from src.monitoring_registry import MONITORING_COUNTRIES
from src.signal_workbench import article_snapshot, instant

_LABELS = {
    'education': 'Изменения в образовании', 'mobility': 'Изменения в мобильности',
    'trade': 'Изменения в торговле', 'infrastructure': 'Инфраструктурные изменения',
    'institutions': 'Институциональные изменения', 'culture': 'Изменения в культуре',
    'safety': 'Изменения в сфере безопасности', 'other': 'Общественные изменения',
}
_STAGE = {
    'proposal': 'Сообщается о предложении или намерении; решение и реализация требуют проверки.',
    'decision': 'Сообщается о решении; его исполнение требует проверки.',
    'implementation': 'Сообщается о начале или ходе исполнения; итог требует проверки.',
}
_RUSSIAN = re.compile(r'[А-Яа-яЁё]')
_SAFE_TEXT = re.compile(r'[<>\x00-\x1f\x7f]')


def _text(value, maximum):
    return isinstance(value, str) and 0 < len(value.strip()) <= maximum and not _SAFE_TEXT.search(value)


def _screening_quote(classification, article):
    """Use the screened signal's exact evidence, never a guessed source line."""
    decisions = classification.get('decisions')
    if not isinstance(decisions, dict):
        return None
    for field, labels in (('signal', SIGNAL), ('mechanism', MECHANISM),
                          ('stage', STAGE), ('country', set(MONITORING_COUNTRIES) | {'none','unknown'})):
        answer = decisions.get(field)
        if (not isinstance(answer, dict) or answer.get('choice') != classification[field]
                or not decision_model.supported_choice(answer, labels)
                or not answer.get('evidence')):
            return None
    quote = decisions['signal']['evidence'][0]['quote']
    return quote if _text(quote, 320) and quote in article['title'] + '\n' + article['excerpt'] else None


def _current(row):
    if 'current_article' in row:
        return row['current_article']
    return {'id': row['article_id'], 'title': row['current_title'],
            'excerpt': row['current_excerpt'], 'source_id': row['current_source_id'],
            'source_name': row['current_source_name'], 'country_code': row['current_country_code'],
            'url': row['current_url'], 'published_at': row['current_published_at'],
            'collected_at': row['current_collected_at'], 'language': row['current_language']}


def _annotation_title(row, article):
    annotation = _loaded(row.get('annotation'))
    if (not isinstance(annotation, dict) or 'evidence_review' not in annotation
            or row.get('annotation_source_title') != article['title']
            or row.get('annotation_source_excerpt') != row.get('current_decision_excerpt')
            or row.get('annotation_model') != decision_extraction.MODEL
            or row.get('annotation_version') != decision_extraction.VERSION):
        return None
    evidence = {'id': article['id'], 'title': article['title'],
                'excerpt': row['annotation_source_excerpt']}
    try:
        decision_extraction.validate_annotation(annotation, evidence,
            set(MONITORING_COUNTRIES), reviewed=True)
    except (ValueError, TypeError, KeyError):
        return None
    title = annotation.get('headline_ru')
    return title if annotation.get('relevant') is True and _text(title, 180) else None


def project_row(row, as_of):
    """Validate bytes, provenance, time and labels before one public card."""
    try:
        now = instant(as_of)
        article, classification = _loaded(row['article']), _loaded(row['classification'])
        current_row = _current(row)
        current = article_snapshot(current_row, screened=True)
        if (not _valid_classification(classification, article)
                or classification['signal'] != 'change'
                or classification['country'] not in MONITORING_COUNTRIES
                or classification['mechanism'] not in _LABELS
                or classification['stage'] not in _STAGE
                or current != article_snapshot(article, screened=True)):
            return None
        fingerprint = source_key(article)
        if (str(row['source_key']).strip() != fingerprint
                or str(row['snapshot_hash']).strip() != hashlib.sha256(_json(article).encode()).hexdigest()
                or not _safe_url(article['url'])):
            return None
        published, collected = instant(article['published_at']), instant(article['collected_at'])
        if not now-timedelta(days=7) <= published <= collected <= now:
            return None
        quote = _screening_quote(classification, article)
        if not quote:
            return None
        title = article['title']
        translated = row.get('title_ru') if row.get('translation_source_title') == title else None
        title_ru = translated if _text(translated, 500) and _RUSSIAN.search(translated) else None
        origin = 'machine_translation' if title_ru else 'unavailable'
        if title_ru is None:
            title_ru = _annotation_title(row, article)
            if title_ru is not None:
                origin = 'reviewed_annotation'
        if (title_ru is None and current_row.get('language') == 'ru'
                and _text(title, 512) and _RUSSIAN.search(title)):
            title_ru = title
            origin = 'source'
        return {
            'id': fingerprint, 'type': 'observation', 'status': 'provisional',
            'country_code': classification['country'],
            'mechanism': classification['mechanism'], 'stage': classification['stage'],
            'source_title': title, 'title_ru': title_ru, 'title_origin': origin,
            'label_ru': _LABELS[classification['mechanism']],
            'why_watch_ru': _STAGE[classification['stage']],
            'source_name': article['source_name'], 'source_url': article['url'],
            'publisher_country_code': article['country_code'],
            'published_at': published.isoformat(), 'collected_at': collected.isoformat(),
            'evidence_quote': quote, 'source_fingerprint': fingerprint,
        }
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        return None


_SQL = text("""
WITH current_sources AS MATERIALIZED (
 SELECT s.source_key,s.snapshot_hash,s.article,s.classification,s.article_id,s.screened_at,
        ar.title AS current_title,ar.language AS current_language,
        LEFT(COALESCE(NULLIF(ar.body,''),ar.summary,''),2000) AS current_excerpt,
        publisher.id AS current_source_id,publisher.name AS current_source_name,
        TRIM(publisher.country_code) AS current_country_code,
        COALESCE(NULLIF(ar.resolved_url,''),ar.url) AS current_url,
        ar.published_at AS current_published_at,ar.collected_at AS current_collected_at,
        LEFT(COALESCE(NULLIF(ar.body,''),ar.summary,''),4000) AS current_decision_excerpt
 FROM early_signal_screenings s JOIN articles ar ON ar.id=s.article_id
JOIN sources discovery ON discovery.id=ar.source_id
JOIN sources publisher ON publisher.id=CASE
   WHEN COALESCE(discovery.config->>'feed_mode','publisher')='publisher_discovery'
   THEN ar.publisher_source_id ELSE COALESCE(ar.publisher_source_id,ar.source_id) END
 WHERE s.screened_at BETWEEN :week_start AND :as_of
   AND s.classification->>'signal'='change'
   AND (CAST(:country AS text) IS NULL OR s.classification->>'country'=:country)
   AND ar.is_duplicate IS FALSE AND ar.geo_status IN
       ('source_verified','publisher_verified','publisher_reassigned')
   AND (COALESCE(discovery.config->>'feed_mode','publisher')<>'publisher_discovery'
        OR ar.publisher_source_id IS NOT NULL)
   AND ar.geo_country_code=publisher.country_code
   AND ar.published_at BETWEEN :week_start AND :as_of AND ar.collected_at<=:as_of
   AND s.article->>'title'=ar.title
   AND s.article->>'excerpt'=LEFT(COALESCE(NULLIF(ar.body,''),ar.summary,''),2000)
   AND s.article->>'source_id'=publisher.id::text
   AND s.article->>'source_name'=publisher.name
   AND s.article->>'country_code'=TRIM(publisher.country_code)
   AND s.article->>'url'=COALESCE(NULLIF(ar.resolved_url,''),ar.url)
   AND s.article->>'published_at'=(
     to_char(ar.published_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS') ||
     CASE WHEN mod(extract(microseconds FROM ar.published_at)::integer,1000000)=0
          THEN '' ELSE '.' || to_char(ar.published_at AT TIME ZONE 'UTC','US') END || '+00:00')
   AND s.article->>'collected_at'=(
     to_char(ar.collected_at AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS') ||
     CASE WHEN mod(extract(microseconds FROM ar.collected_at)::integer,1000000)=0
          THEN '' ELSE '.' || to_char(ar.collected_at AT TIME ZONE 'UTC','US') END || '+00:00')
), ranked AS MATERIALIZED (
 SELECT current_sources.*,
        row_number() OVER (PARTITION BY classification->>'country'
                           ORDER BY screened_at DESC,article_id DESC) AS country_rank
 FROM current_sources
), bounded AS MATERIALIZED (
 SELECT * FROM ranked WHERE country_rank<=12
 ORDER BY screened_at DESC,article_id DESC LIMIT 3000
)
SELECT b.source_key,b.snapshot_hash,b.article,b.classification,
 b.article_id,b.current_title,b.current_language,b.current_excerpt,b.current_source_id,
 b.current_source_name,b.current_country_code,b.current_url,
 b.current_published_at,b.current_collected_at,b.current_decision_excerpt,
 tr.source_title AS translation_source_title,tr.title_ru,
 ada.source_title AS annotation_source_title,
 ada.source_excerpt AS annotation_source_excerpt,
 ada.annotation,ada.model AS annotation_model,ada.version AS annotation_version
FROM bounded b
LEFT JOIN article_title_translations tr ON tr.article_id=b.article_id AND tr.source_title=b.current_title
LEFT JOIN article_decision_annotations ada ON ada.article_id=b.article_id
ORDER BY b.screened_at DESC,b.article_id DESC
LIMIT 3000
""")


def list_observations(country=None, limit=6, as_of=None):
    if country is not None and country not in MONITORING_COUNTRIES:
        raise ValueError('invalid country')
    if type(limit) is not int or not 1 <= limit <= 12:
        raise ValueError('invalid limit')
    now = min(instant(as_of), datetime.now(timezone.utc)) if as_of else datetime.now(timezone.utc)
    with get_session() as session:
        rows = session.execute(_SQL, {'country': country, 'week_start': now-timedelta(days=7),
                                      'as_of': now}).mappings().all()
    grouped = defaultdict(deque)
    for raw in rows:
        item = project_row(dict(raw), now)
        if item is not None and (country is None or item['country_code'] == country):
            grouped[item['country_code']].append(item)
    for code in grouped:
        grouped[code] = deque(sorted(grouped[code], key=lambda x:(x['published_at'],x['id']), reverse=True))
    result = []
    codes = sorted(grouped)
    if codes:
        offset = (int(now.timestamp()) // 3600 * limit) % len(codes)
        codes = codes[offset:] + codes[:offset]
    while grouped and len(result) < limit:
        for code in codes:
            if code not in grouped:
                continue
            result.append(grouped[code].popleft())
            if not grouped[code]:
                del grouped[code]
            if len(result) >= limit:
                break
    return result
