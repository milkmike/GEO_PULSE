from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from src.signal_workbench import prepare_review, import_screening, assemble_context, review_draft

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/early_signal_example.json'
NOW = datetime(2026, 10, 1, 18, tzinfo=timezone.utc)


def fixture():
    return json.loads(FIXTURE.read_text())


def responses(prepared):
    results = []
    for batch in prepared['batches']:
        answers = {}
        for key, question in batch['payload']['questions'].items():
            choice = {'signal': 'change', 'mechanism': 'education', 'stage': 'proposal', 'country': 'RS'}[key.rsplit('_', 1)[1]]
            labels = question['criteria']
            answers[key] = {'type': 'choice', 'choice': choice, 'confidence': .9,
                            'probabilities': {label: .9 if label == choice else .1/(len(labels)-1) for label in labels}}
        results.append({'request_hash': batch['request_hash'], 'data': {'answers': answers}})
    return results


def test_complete_offline_flow_preserves_legacy_negative_and_old_explicit_context():
    data = fixture()
    sources = data['context']['articles']
    prepared = prepare_review([dict(a, is_relevant=False) for a in sources], as_of=NOW)
    assert prepared['input_count'] == 3 and prepared['selected_count'] == 2
    screened = import_screening(prepared, responses(prepared))
    assert screened['screened_count'] == 2 and screened['unanswered_batches'] == 0
    context = assemble_context(screened, 1, [sources[1], sources[2]])
    assert len(context['articles']) == 3 and 'EVIDENCE_JSON' in context['prompt']
    dossier, html = review_draft(context, data['draft'], example=True)
    assert dossier['status'] == 'needs_review'
    assert dossier['source_snapshot_sha256'] in html and 'МСК' in html
    assert 'ВЫМЫШЛЕННЫЙ ПРИМЕР' in html and '<details>' in html
    assert 'href="https://example.org/' not in html


def test_unknown_or_duplicate_response_and_changed_snapshot_are_rejected():
    prepared = prepare_review(fixture()['context']['articles'], as_of=NOW)
    data = responses(prepared)
    with pytest.raises(ValueError, match='unique'):
        import_screening(prepared, data + data)
    wrong = deepcopy(data)
    wrong[0]['request_hash'] = 'wrong'
    with pytest.raises(ValueError, match='unique'):
        import_screening(prepared, wrong)
    prepared['batches'][0]['articles'][0]['source_name'] = 'Different publisher'
    with pytest.raises(ValueError, match='snapshot'):
        import_screening(prepared, data)


def test_missing_responses_are_not_successfully_screened_and_routine_is_not_context():
    prepared = prepare_review(fixture()['context']['articles'], as_of=NOW)
    empty = import_screening(prepared, [])
    assert empty['screened_count'] == 0 and empty['unanswered_batches'] > 0
    screened = import_screening(prepared, responses(prepared))
    screened['records'][0]['classification']['signal'] = 'routine'
    with pytest.raises(ValueError, match='candidate'):
        assemble_context(screened, screened['records'][0]['article']['id'], [])


def test_context_cannot_silently_extend_screened_excerpt_or_change_metadata():
    source = fixture()['context']['articles'][0]
    source['excerpt'] = 'x' * 2000 + ' unscreened claim' * 400
    prepared = prepare_review([source], as_of=NOW)
    screened = import_screening(prepared, responses(prepared))
    context = assemble_context(screened, 1, [])
    assert len(context['articles'][0]['excerpt']) == 2000
    assert 'unscreened claim' not in context['prompt']
    screened['records'][0]['article']['source_name'] = 'Changed'
    with pytest.raises(ValueError, match='changed'):
        assemble_context(screened, 1, [])


def test_fiction_label_cannot_be_accidentally_omitted_and_bad_quote_cannot_render():
    data = fixture()
    _, html = review_draft(data['context'], data['draft'])
    assert 'ВЫМЫШЛЕННЫЙ ПРИМЕР' in html
    data['draft']['observations'][0]['quote'] = 'invented evidence'
    with pytest.raises(ValueError, match='quote'):
        review_draft(data['context'], data['draft'])


def test_cli_creates_demo_without_credentials_and_never_overwrites(tmp_path):
    out = tmp_path / 'review.html'
    command = [sys.executable, str(ROOT / 'scripts/review_early_signals.py'), 'example', '--out', str(out)]
    result = subprocess.run(command, capture_output=True, text=True, env={'PATH': '/usr/bin:/bin'})
    assert result.returncode == 0, result.stderr
    assert 'ВЫМЫШЛЕННЫЙ ПРИМЕР' in out.read_text()
    before = out.read_bytes()
    second = subprocess.run(command, capture_output=True, text=True)
    assert second.returncode == 2 and out.read_bytes() == before
