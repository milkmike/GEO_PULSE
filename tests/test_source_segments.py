import copy

import pytest

from src import source_segments as evidence


def article():
    return {'id': 7, 'title': 'Serbia and Russia expand research cooperation.',
            'excerpt': 'The Serbian university signed an agreement with a Russian university. No visa rules changed.'}


def annotation():
    return {'relevant': True, 'headline_ru': 'Сербия и Россия расширяют научное сотрудничество',
            'summary_ru': '', 'russia_explanation_ru': 'Соглашение с российским университетом.',
            'russia_evidence_quote': article()['title'], 'kind': 'cooperation',
            'countries': [{'code': 'RS', 'evidence_quote': article()['title']}],
            'positions': [], 'changes': []}


def test_fragments_preserve_exact_unicode_source_and_have_stable_ids():
    source = article() | {'excerpt': 'Молдова — не США.\n' + 'Длинное предложение ' * 35}
    lines = evidence.segments(source)
    assert lines == evidence.segments(source)
    assert len(lines) < 100
    for item in lines.values():
        assert 0 < len(item['quote']) <= 200
        assert item['quote'] in source[item['source_part']]


def test_line_references_are_resolved_without_generating_or_rewriting_quotes():
    lines = evidence.segments(article())
    first = next(iter(lines))
    value = annotation()
    value['russia_evidence_quote'] = first
    value['countries'][0]['evidence_quote'] = first
    resolved = evidence.resolve_draft_quotes(value, article())
    assert resolved['russia_evidence_quote'] == lines[first]['quote']
    assert value['russia_evidence_quote'] == first
    value['countries'][0]['evidence_quote'] = 'invented_fragment'
    with pytest.raises(ValueError):
        evidence.resolve_draft_quotes(value, article())


def test_proof_is_bound_to_both_exact_source_and_retained_claims():
    lines = evidence.segments(article())
    first = next(iter(lines))
    proof = {key: first for key in ('headline', 'russia', 'country_RS')}
    value = evidence.seal_review(article(), annotation(), proof)
    quotes = evidence.public_quotes(article(), value, 'RS')
    assert {row['claim'] for row in quotes} == {'headline', 'russia', 'country'}
    assert all(row['quote'] == article()['title'] for row in quotes)
    for tampered in (article() | {'excerpt': 'Changed source.'}, article() | {'id': 8}):
        with pytest.raises(ValueError):
            evidence.public_quotes(tampered, value, 'RS')
    changed = copy.deepcopy(value)
    changed['headline_ru'] = 'Совершенно другой факт'
    with pytest.raises(ValueError):
        evidence.public_quotes(article(), changed, 'RS')
    assert evidence.public_quotes(article(), annotation(), 'RS') == []


def test_another_country_proof_never_leaks_into_selected_country():
    source = article() | {'excerpt': 'France and Russia also signed a separate agreement.'}
    value = annotation()
    value['countries'].append({'code': 'FR', 'evidence_quote': source['excerpt']})
    lines = evidence.segments(source)
    title = next(key for key, line in lines.items() if line['source_part'] == 'title')
    french = next(key for key, line in lines.items() if 'France' in line['quote'])
    signed = evidence.seal_review(source, value, {'headline': title, 'russia': title,
                                                'country_RS': title, 'country_FR': french})
    selected = evidence.public_quotes(source, signed, 'RS')
    assert all('France' not in row['quote'] for row in selected)
