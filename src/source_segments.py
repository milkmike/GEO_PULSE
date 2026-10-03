"""Exact source fragments and source-bound proof; no network or generated quotes."""
from copy import deepcopy
import hashlib
import json
import re

MODEL = 'typesafe/jev-1.13'
VERSION = 'source-line-review-v1'


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def source_hash(article):
    return _hash([article['id'], article['title'], article['excerpt']])


def segments(article):
    if (not isinstance(article, dict) or type(article.get('id')) is not int or article['id'] <= 0
            or not isinstance(article.get('title'), str) or len(article['title']) > 2000
            or not isinstance(article.get('excerpt'), str) or len(article['excerpt']) > 4000):
        raise ValueError('invalid fragment source')
    result = {}
    for part, prefix in (('title', 't'), ('excerpt', 'e')):
        text = article[part]
        for match in re.finditer(r'[^\n.!?。！？]+[.!?。！？]*|[.!?。！？]+', text):
            start, stop = match.span()
            while start < stop:
                end = min(start + 200, stop)
                if end < stop:
                    word = text.rfind(' ', start + 100, end)
                    if word > start:
                        end = word
                raw = text[start:end]
                quote = raw.strip()
                offset = start + len(raw) - len(raw.lstrip())
                if quote:
                    result[f'{prefix}{offset:06d}'] = {'quote': quote, 'source_part': part}
                start = end
    if not result or len(result) > 128:
        raise ValueError('invalid fragment count')
    return result


def resolve_draft_quotes(annotation, article):
    """Draft model emits existing ids; code copies original bytes, never repairs them."""
    lines = segments(article)
    result = deepcopy(annotation)
    if result.get('relevant') is not True:
        return result
    try:
        result['russia_evidence_quote'] = lines[result['russia_evidence_quote']]['quote']
        for field in ('countries', 'positions', 'changes'):
            for item in result[field]:
                item['evidence_quote'] = lines[item['evidence_quote']]['quote']
    except (KeyError, TypeError):
        raise ValueError('unknown source fragment') from None
    return result


def seal_review(article, annotation, selected):
    result = deepcopy(annotation)
    result.pop('evidence_review', None)
    lines = segments(article)
    keys = {'headline', 'russia'} | {f"country_{c['code']}" for c in result['countries']}
    if result.get('summary_ru'):
        keys.add('summary')
    if not isinstance(selected, dict) or not keys <= set(selected):
        raise ValueError('missing source proof')
    try:
        quotes = {key: {'segment_id': selected[key], **lines[selected[key]]} for key in sorted(keys)}
    except (KeyError, TypeError):
        raise ValueError('unknown source proof') from None
    result['evidence_review'] = {'model': MODEL, 'version': VERSION,
        'source_hash': source_hash(article), 'annotation_hash': _hash(result), 'quotes': quotes}
    validate_review(article, result)
    return result


def validate_review(article, annotation):
    review = annotation.get('evidence_review')
    if review is None:
        return {}
    plain = {key: value for key, value in annotation.items() if key != 'evidence_review'}
    if (not isinstance(review, dict) or set(review) != {'model', 'version', 'source_hash', 'annotation_hash', 'quotes'}
            or review['model'] != MODEL or review['version'] != VERSION
            or review['source_hash'] != source_hash(article) or review['annotation_hash'] != _hash(plain)):
        raise ValueError('stale or changed source proof')
    lines = segments(article)
    quotes = review['quotes']
    expected = {'headline', 'russia'} | {f"country_{c['code']}" for c in annotation['countries']}
    if annotation.get('summary_ru'):
        expected.add('summary')
    if not isinstance(quotes, dict) or set(quotes) != expected:
        raise ValueError('invalid source proof fields')
    for key, item in quotes.items():
        if (not isinstance(item, dict) or set(item) != {'segment_id', 'quote', 'source_part'}
                or not isinstance(item['segment_id'], str) or item['segment_id'] not in lines
                or {k: item[k] for k in ('quote', 'source_part')} != lines[item['segment_id']]):
            raise ValueError('unknown source proof')
        if key == 'russia' and item['quote'] != annotation['russia_evidence_quote']:
            raise ValueError('Russia proof mismatch')
        if key.startswith('country_') and not any(c['code'] == key[8:] and c['evidence_quote'] == item['quote']
                                                  for c in annotation['countries']):
            raise ValueError('country proof mismatch')
    return quotes


def public_quotes(article, annotation, country):
    quotes = validate_review(article, annotation)
    result = []
    for key, claim in (('headline', 'headline'), ('summary', 'summary'), ('russia', 'russia'),
                       (f'country_{country}', 'country')):
        if key in quotes:
            result.append({'claim': claim, 'quote': quotes[key]['quote'],
                           'source_part': quotes[key]['source_part']})
    return result
