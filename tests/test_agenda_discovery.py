from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.agenda_discovery import (
    accepted_decision, candidate_groups, pair_cache_key, parse_pair_response,
    prepare_pair_payload,
)

NOW = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)


def article(identity, title, **changes):
    return dict(id=identity, title=title, excerpt=changes.pop('excerpt', ''),
                published_at=NOW, collected_at=NOW, source_id=identity,
                source_name=f'Source {identity}', country_code='RU',
                url=f'https://example.org/{identity}', content_hash=f'hash-{identity}') | changes


def answer(choice='same_event', confidence=.91, probabilities=None):
    return {'type': 'choice', 'choice': choice, 'confidence': confidence,
            'probabilities': probabilities if probabilities is not None else
            {'same_event': .91, 'development': .03, 'unrelated': .04, 'uncertain': .02}}


def response(value=None):
    return {'answers': {'pair_1_2': answer() if value is None else value}, 'usage': {'cost': .0001}}


def test_cross_country_and_rri_rejected_articles_are_candidates_without_mutation():
    rows = [article(1, 'Flydubai suspends flights to Dubai', country_code='AE'),
            article(2, 'Flydubai suspends Dubai flights', country_code='KZ', rri_relevant=False)]
    before = copy.deepcopy(rows)
    groups = candidate_groups(rows)
    assert len(groups) == 1
    assert {groups[0]['anchor']['id'], groups[0]['candidates'][0]['id']} == {1, 2}
    assert rows == before


def test_shared_publisher_country_does_not_propose_unrelated_articles():
    rows = [article(1, 'Россия открыла новую библиотеку для студентов'),
            article(2, 'Россия запустила спутник связи на орбиту')]
    assert candidate_groups(rows) == []


def test_cyrillic_transliteration_and_name_variants_retrieve_suspects():
    rows = [article(1, 'Flydubai cancels flights after airport closure'),
            article(2, 'Флайдубай отменяет рейсы после закрытия аэропорта')]
    assert candidate_groups(rows)
    rows = [article(3, 'Kaliningrad transit disruption reported'),
            article(4, 'Калининград: перевозки остановлены')]
    assert candidate_groups(rows)


def test_existing_anchor_has_first_chance_and_candidates_do_not_bridge_transitively():
    a = article(1, 'Orchid laboratory chemical explosion investigation')
    b = article(2, 'Orchid laboratory chemical explosion; Meridian ferry rescue')
    c = article(3, 'Meridian ferry rescue operation completed')
    groups = candidate_groups([b, c], [{'id': 45, 'anchor': a, 'articles': [a]}])
    assert groups[0]['existing_id'] == 45
    assert [row['id'] for row in groups[0]['candidates']] == [2]


def test_existing_members_are_not_proposed_again():
    a = article(1, 'Orchid laboratory chemical explosion')
    b = article(2, a['title'])
    assert candidate_groups([a, b], [{'id': 45, 'anchor': a, 'articles': [a, b]}]) == []


def test_separate_collection_dates_exclude_repeated_headlines():
    a = article(1, 'Kaliningrad airport closure')
    b = article(2, a['title'], published_at=NOW-timedelta(days=8), collected_at=NOW-timedelta(days=8))
    assert candidate_groups([a, b]) == []


def test_inconsistent_publication_uses_collection_or_explicit_effective_time():
    a = article(1, 'Kaliningrad airport closure')
    b = article(2, a['title'], published_at=NOW-timedelta(days=500))
    assert candidate_groups([a, b])
    b['effective_time'] = NOW-timedelta(days=8)
    assert candidate_groups([a, b]) == []


@pytest.mark.parametrize('change', [{'title': None}, {'title': '   '}, {'title': ['headline']},
    {'collected_at': '2026-09-30'}, {'published_at': 42}, {'effective_time': False},
    {'id': True}, {'source_id': None}, {'country_code': ''}])
def test_invalid_article_types_are_excluded(change):
    a = article(1, 'Kaliningrad airport closure')
    assert candidate_groups([a, article(2, a['title']) | change]) == []


def test_candidate_order_and_limits_are_deterministic():
    rows = [article(i, f'Orchid laboratory chemical explosion update {i}') for i in range(1, 50)]
    first = candidate_groups(rows, max_groups=3, max_members=4)
    assert first == candidate_groups(list(reversed(rows)), max_groups=3, max_members=4)
    assert 0 < len(first) <= 3
    assert all(0 < len(g['candidates']) <= 4 for g in first)


def test_payload_is_bounded_and_identifies_actual_articles_in_each_question():
    a = article(1, 'Калининград ' * 100, excerpt='世界' * 1000)
    candidates = [article(i, 'Флайдубай ' * 100, excerpt='世界' * 1000) for i in range(2, 30)]
    payload, pairs = prepare_pair_payload(a, candidates)
    assert 1 <= len(pairs) <= 8
    assert len(json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode()) <= 24000
    assert set(payload['questions']) == set(pairs)
    assert len(payload['state']['articles']) == len(pairs) + 1
    for key, pair in pairs.items():
        instruction = payload['questions'][key]['instructions']
        assert "['1']" in instruction and f"['{pair['article_id']}']" in instruction
        assert set(payload['questions'][key]['criteria']) == {'same_event', 'development', 'unrelated', 'uncertain'}
    for row in payload['state']['articles'].values():
        assert len(row['title']) <= 320 and len(row['excerpt']) <= 800


def test_payload_preserves_suspicious_original_date_and_separate_effective_time():
    a = article(1, 'Old date', published_at=NOW-timedelta(days=500))
    payload, pairs = prepare_pair_payload(a, [article(2, 'Update')])
    state = payload['state']['articles']['1']
    assert state['published_at'] == (NOW-timedelta(days=500)).isoformat()
    assert state['effective_time'] == NOW.isoformat()
    assert pairs['pair_1_2']['cache_key'] == pair_cache_key(a, article(2, 'Update'))


def test_parser_preserves_all_raw_probabilities_and_pair_identity():
    _, pairs = prepare_pair_payload(article(1, 'A'), [article(2, 'B')])
    decisions, cost = parse_pair_response(response(), pairs)
    assert cost == .0001
    assert decisions[0]['article_id'] == 2 and decisions[0]['anchor_id'] == 1
    assert decisions[0]['probabilities'] == answer()['probabilities']
    assert accepted_decision(decisions[0])


@pytest.mark.parametrize('bad', [None, [], {}, {'type': 'score'},
    answer(choice='topic'), answer(confidence=True), answer(confidence=float('nan')),
    answer(confidence=1.1), answer(probabilities={}),
    answer(probabilities={'same_event': .95}),
    answer(probabilities=dict(answer()['probabilities'], alien=0)),
    answer(probabilities=dict(answer()['probabilities'], same_event=float('inf'))),
    answer(probabilities=dict(answer()['probabilities'], same_event=True)),
    answer(probabilities=dict(answer()['probabilities'], unrelated=-.1)),
])
def test_parser_rejects_malformed_answers(bad):
    _, pairs = prepare_pair_payload(article(1, 'A'), [article(2, 'B')])
    data = response()
    data['answers']['pair_1_2'] = bad
    with pytest.raises(ValueError):
        parse_pair_response(data, pairs)


@pytest.mark.parametrize('data', [{}, {'answers': {}}, {'answers': {'extra': answer()}},
    {'answers': {'pair_1_2': answer(), 'extra': answer()}},
    response() | {'usage': {'cost': -1}}, response() | {'usage': {'cost': float('nan')}}])
def test_parser_requires_exact_answer_ids_and_valid_usage(data):
    _, pairs = prepare_pair_payload(article(1, 'A'), [article(2, 'B')])
    with pytest.raises(ValueError):
        parse_pair_response(data, pairs)


@pytest.mark.parametrize('choice,probability,confidence,want', [
    ('same_event', .8, .7, True), ('development', .8, .7, True),
    ('same_event', .79, .99, False), ('development', .99, .69, False),
    ('unrelated', .99, .99, False), ('uncertain', .99, .99, False)])
def test_acceptance_requires_relation_probability_and_confidence(choice, probability, confidence, want):
    values = dict.fromkeys(['same_event', 'development', 'unrelated', 'uncertain'], .01)
    values[choice] = probability
    assert accepted_decision(answer(choice, confidence, values)) is want


def test_cache_invalidates_for_changed_content_dates_or_anchor_direction():
    a, b = article(1, 'A'), article(2, 'B')
    key = pair_cache_key(a, b)
    assert key == pair_cache_key(copy.deepcopy(a), copy.deepcopy(b))
    for changed in [b | {'content_hash': 'changed'}, b | {'title': 'New'},
                    b | {'published_at': NOW-timedelta(days=1)}]:
        assert pair_cache_key(a, changed) != key
    assert pair_cache_key(b, a) != key


@pytest.mark.parametrize('bad', [answer(choice=[]), answer(choice={}), answer(confidence=10**1000),
    answer(probabilities=dict(answer()['probabilities'], same_event=10**1000))])
def test_hostile_json_values_fail_closed_with_value_error(bad):
    _, pairs = prepare_pair_payload(article(1, 'A'), [article(2, 'B')])
    with pytest.raises(ValueError):
        parse_pair_response(response(bad), pairs)
    assert not accepted_decision(bad)


def test_duplicate_or_self_candidates_are_not_sent_twice():
    a, b = article(1, 'A'), article(2, 'B')
    payload, pairs = prepare_pair_payload(a, [a, b, b])
    assert list(pairs) == ['pair_1_2']
    assert set(payload['state']['articles']) == {'1', '2'}


def test_cached_rejected_pairs_cannot_fill_the_first_group_forever():
    from src.agenda_discovery import candidate_group_page

    rows = [article(4, 'Orchid laboratory chemical explosion'), article(3, 'Orchid laboratory chemical explosion'),
            article(2, 'Meridian ferry rescue operation'), article(1, 'Meridian ferry rescue operation')]
    first = candidate_group_page(rows, max_groups=1)
    assert first['groups'][0]['anchor']['id'] == 4
    known = {pair_cache_key(rows[0], rows[1]), pair_cache_key(rows[1], rows[0])}
    second = candidate_group_page(rows, max_groups=1, known_pair_keys=known)
    assert second['groups'][0]['anchor']['id'] == 2
    assert [r['id'] for r in second['groups'][0]['candidates']] == [1]


def test_continuation_advances_through_empty_anchors_and_reaches_older_events():
    from src.agenda_discovery import candidate_group_page

    rows = [article(5, 'Alpha'), article(4, 'Bravo'), article(3, 'Delta'),
            article(2, 'Meridian ferry rescue operation'), article(1, 'Meridian ferry rescue operation')]
    first = candidate_group_page(rows, scan_limit=2)
    assert first == {'groups': [], 'group_cursors': [], 'next_cursor': 2, 'anchors_scanned': 2, 'has_more': True}
    second = candidate_group_page(rows, cursor=first['next_cursor'], scan_limit=2)
    assert second['groups'][0]['anchor']['id'] == 2
    assert second['group_cursors'] == [{'cursor': 3, 'next_cursor': 4}]
    assert second['anchors_scanned'] == 2
    last = candidate_group_page(rows, cursor=second['next_cursor'], scan_limit=2)
    assert last['has_more'] is False and last['next_cursor'] == 0


def test_call_cap_can_resume_same_anchor_without_repeating_cached_questions():
    from src.agenda_discovery import candidate_group_page

    rows = [article(i, 'Orchid laboratory chemical explosion') for i in range(1, 6)]
    first = candidate_group_page(rows, max_groups=1)
    group = first['groups'][0]
    known = {pair_cache_key(group['anchor'], group['candidates'][0])}
    resumed = candidate_group_page(rows, max_groups=1, cursor=first['group_cursors'][0]['cursor'], known_pair_keys=known)
    assert resumed['groups'][0]['anchor']['id'] == group['anchor']['id']
    assert group['candidates'][0]['id'] not in [a['id'] for a in resumed['groups'][0]['candidates']]
    assert len(resumed['groups'][0]['candidates']) == 3


def test_older_cohort_is_retrievable_beyond_popular_token_posting_window():
    from src.agenda_discovery import candidate_group_page

    rows = [article(i, 'Orchid laboratory chemical explosion') for i in range(1, 401)]
    page = candidate_group_page(rows, cursor=200, max_groups=1, max_members=4)
    group = page['groups'][0]
    assert group['anchor']['id'] == 200
    assert any(a['id'] < 200 for a in group['candidates'])
    assert all(abs(a['id'] - 200) <= 64 for a in group['candidates'])


def test_existing_rejected_candidates_remain_available_for_a_new_anchor():
    from src.agenda_discovery import candidate_group_page

    old = article(100, 'Orchid laboratory chemical explosion')
    a, b = article(2, 'Orchid laboratory chemical explosion followup'), article(1, 'Orchid laboratory chemical explosion followup')
    known = {pair_cache_key(old, a), pair_cache_key(old, b)}
    page = candidate_group_page([a, b], [{'id': 40, 'anchor': old, 'articles': [old]}], known_pair_keys=known)
    assert page['groups'][0]['existing_id'] is None
    assert page['groups'][0]['anchor']['id'] == 2
