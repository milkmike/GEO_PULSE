from datetime import datetime, timezone
from decimal import Decimal
from contextlib import contextmanager
from pathlib import Path
import os
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src import decision_extraction as extraction
from src import source_segments


def test_jev_draft_uses_fragment_ids_and_does_not_repair_country_quote(monkeypatch):
    source = article()
    legacy_key = extraction.source_key(source)
    monkeypatch.setenv('JEV_EVIDENCE_MODE', 'apply')
    lines = source_segments.segments(source)
    identity = next(key for key, line in lines.items() if 'Serbia' in line['quote'])
    draft = annotation(russia_evidence_quote=identity, countries=[{'code': 'RS', 'evidence_quote': identity}], positions=[])
    assert identity in extraction.prepare_prompt(source)
    assert extraction.source_key(source) != legacy_key
    result = extraction.parse_annotation(json.dumps(draft), source, {'RS'})
    assert result['countries'][0]['evidence_quote'] == lines[identity]['quote']
    assert result['russia_evidence_quote'] == lines[identity]['quote']
    draft['countries'][0]['evidence_quote'] = 'Serbia'
    with pytest.raises(ValueError):
        extraction.parse_annotation(json.dumps(draft), source, {'RS'})


def test_only_reviewed_annotations_accept_bound_evidence_audit():
    source = article()
    lines = source_segments.segments(source)
    identity = next(key for key, line in lines.items() if 'Serbia' in line['quote'])
    draft = annotation(russia_evidence_quote=lines[identity]['quote'],
                       countries=[{'code':'RS','evidence_quote':lines[identity]['quote']}], summary_ru='')
    signed = source_segments.seal_review(source, draft, {'headline':identity,'russia':identity,'country_RS':identity})
    assert extraction.validate_annotation(signed, source, {'RS'}, reviewed=True) == signed
    with pytest.raises(ValueError):
        extraction.validate_annotation(signed, source, {'RS'})
    signed['headline_ru'] = 'Выдуманный факт'
    with pytest.raises(ValueError):
        extraction.validate_annotation(signed, source, {'RS'}, reviewed=True)


def article(**changes):
    row = dict(id=1, title="Serbia minister said Russian citizens may enter", excerpt="The ministry said Russian citizens may enter Serbia.", country_code="RS", published_at=datetime(2026, 9, 30, tzinfo=timezone.utc), collected_at=datetime(2026, 9, 30, tzinfo=timezone.utc), is_relevant=True)
    return row | changes


def annotation(**changes):
    return dict(relevant=True, headline_ru="Министр Сербии высказался", summary_ru="Министерство сообщило об условиях въезда.", russia_explanation_ru="Речь о гражданах России.", russia_evidence_quote="Russian citizens", countries=[dict(code="RS", evidence_quote="Serbia")], kind="position", positions=[dict(actor="Министерство", actor_type="government", position_ru="Сообщило об условиях въезда", evidence_quote="The ministry said")], changes=[]) | changes


def test_exact_quotes_and_known_country_are_required():
    good = annotation()
    assert extraction.validate_annotation(good, article(), {"RS"}) == good
    with pytest.raises(ValueError):
        extraction.validate_annotation(annotation(russia_evidence_quote="Russian tourists"), article(), {"RS"})
    with pytest.raises(ValueError):
        extraction.validate_annotation(annotation(countries=[dict(code="FR", evidence_quote="Serbia")]), article(), {"RS"})
    with pytest.raises(ValueError):
        extraction.validate_annotation(annotation(countries=[dict(code="RU", evidence_quote="Russian")]), article(), {"RS", "RU"})


def test_strict_shape_abstention_and_unsafe_generated_text():
    with pytest.raises(ValueError):
        extraction.validate_annotation(annotation(extra="invented"), article(), {"RS"})
    with pytest.raises(ValueError):
        extraction.validate_annotation(annotation(relevant=False), article(), {"RS"})
    with pytest.raises(ValueError):
        extraction.validate_annotation(annotation(headline_ru="https://example.com"), article(), {"RS"})
    empty = annotation(relevant=False, headline_ru="", summary_ru="", russia_explanation_ru="", russia_evidence_quote="", countries=[], positions=[], changes=[])
    assert extraction.validate_annotation(empty, article(), {"RS"}) == empty
    with pytest.raises(ValueError, match="missing Russian text"):
        extraction.validate_annotation(annotation(positions=[dict(
            actor="Students in Blockade", actor_type="ngo",
            position_ru="Выступили против санкций", evidence_quote="The ministry said")]), article(), {"RS"})
    wrapped = annotation(positions=[dict(actor="Движение Students in Blockade", actor_type="ngo",
        position_ru="Выступило против санкций", evidence_quote="The ministry said")])
    assert extraction.validate_annotation(wrapped, article(), {"RS"}) == wrapped


def test_prompt_covers_public_stance_truncation_and_attribution_in_each_claim():
    source = article(title="Student List declares stance on sanctions against Russia",
        excerpt="The Republic of Serbia will maintain its principled stance against imposing restrictive measures on the Russian Federation, the Students in Blockade m")
    prompt = extraction.prepare_prompt(source)
    assert source["title"] + "\n" + source["excerpt"] in prompt
    for phrase in ("Публичная позиция по санкциям", "даже без изменения правил", "именно эту страну",
                   "Не дописывай оборванные фразы", "В КАЖДОМ position_ru и change_ru", "конкретный названный автор",
                   "назови затронутую сторону"):
        assert phrase in prompt
    assert len(prompt.encode()) <= 32000


@pytest.mark.parametrize("bad", [
    {"kind": []},
    {"positions": None},
    {"positions": [{"actor":"Министерство", "actor_type":[], "position_ru":"Сообщило об условиях въезда", "evidence_quote":"The ministry said"}]},
    {"changes": [{"category":[], "change_ru":"Изменились условия въезда", "evidence_quote":"may enter"}]},
])
def test_malformed_model_types_fail_closed_with_value_error(bad):
    with pytest.raises(ValueError):
        extraction.validate_annotation(annotation(**bad), article(), {"RS"})


def test_duplicate_json_keys_are_rejected():
    raw = '{"relevant":true,"relevant":false}'
    with pytest.raises(ValueError, match="duplicate JSON key"):
        extraction.parse_annotation(raw, article(), {"RS"})


def test_candidate_order_round_robins_countries_and_prioritizes_relevance():
    rows = [article(id=i, country_code=country, is_relevant=relevant) for i, country, relevant in [(1,"RS",True),(2,"RS",True),(3,"GE",True),(4,"GE",False),(5,"RS",False)]]
    assert [row["id"] for row in extraction.fair_candidates(rows)] == [1, 3, 2, 4, 5]


def test_current_triage_leads_precede_legacy_relevance():
    rows = [article(id=1, is_relevant=True),
            article(id=2, is_relevant=None, triage_positive=True),
            article(id=3, is_relevant=False, triage_positive=True)]
    assert [row["id"] for row in extraction.fair_candidates(rows)] == [3, 2, 1]


def test_triage_selection_version_matches_producer():
    from src import news_triage
    assert extraction.TRIAGE_MODEL == news_triage.MODEL
    assert extraction.TRIAGE_VERSION == news_triage.VERSION == "news-triage-v3-chat-grounded"


def test_source_key_changes_with_exact_input_and_limit_validation():
    a = article()
    assert extraction.source_key(a) != extraction.source_key(article(excerpt=a["excerpt"] + "!"))
    with pytest.raises(ValueError):
        extraction.run_decision_cycle(budget_usd=Decimal("0"), campaign="x", max_calls=5)
    with pytest.raises(ValueError):
        extraction.run_decision_cycle(budget_usd=Decimal("0"), campaign="x", article_ids=[1] * 2)
    with pytest.raises(ValueError):
        extraction.run_decision_cycle(budget_usd=Decimal("0"), campaign="x", article_ids=[[1]])


def test_reservation_precedes_http_and_invalid_response_is_not_saved(monkeypatch):
    calls = []
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(extraction, "load_candidates", lambda article_ids=None: ([article()], {"RS"}))
    monkeypatch.setattr(extraction.budget, "get_attempted_pair_keys", lambda campaign: set())
    monkeypatch.setattr(extraction.budget, "get_budget", lambda campaign: 2.0)
    def reserve(*args, **kwargs):
        calls.append("reserve")
        expected = hashlib.sha256(json.dumps([extraction.VERSION, extraction.MODEL,
            extraction.prepare_prompt(article())], ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        assert args[2] == expected
        return "reserved-id"
    monkeypatch.setattr(extraction.budget, "reserve_request", reserve)
    monkeypatch.setattr(extraction.budget, "finish_request", lambda ident, cost, status: calls.append(("finish", ident, cost, status)))
    monkeypatch.setattr(extraction, "track_api_call", lambda **kwargs: None)
    monkeypatch.setattr(extraction, "save_if_current", lambda *args: pytest.fail("invalid output saved"))
    class FakeChat:
        def __init__(self, *args, **kwargs):
            self.requests = []
        def chat(self, *args, **kwargs):
            calls.append("http")
            self.requests.append({"finish_reason":"stop", "usage":{"cost":0.12}})
            return '{"bad":"shape"}', extraction.MODEL
    monkeypatch.setattr(extraction, "BudgetedChat", FakeChat)
    stats = extraction.run_decision_cycle(budget_usd=Decimal("3"), campaign="existing", max_calls=1)
    assert stats["calls"] == 1 and stats["saved"] == 0 and stats["invalid"] == 1
    assert calls == ["reserve", "http", ("finish", "reserved-id", 0.12, "invalid_response")]


@pytest.mark.parametrize(("content", "finish_reason", "reason"), [
    ('{"relevant":', "stop", "invalid_json"),
    ('{"bad":"shape"}', "stop", "validation_error"),
    ('{"bad":"shape"}', "length", "incomplete_output"),
])
def test_failed_extraction_reports_fixed_reason_without_changing_budget(
        monkeypatch, content, finish_reason, reason):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(extraction, "load_candidates", lambda article_ids=None: ([article()], {"RS"}))
    monkeypatch.setattr(extraction.budget, "get_attempted_pair_keys", lambda campaign: set())
    monkeypatch.setattr(extraction.budget, "get_budget", lambda campaign: 2.0)
    monkeypatch.setattr(extraction.budget, "reserve_request", lambda *args, **kwargs: "reserved-id")
    settled, tracked = [], []
    monkeypatch.setattr(extraction.budget, "finish_request", lambda *args: settled.append(args))
    monkeypatch.setattr(extraction, "track_api_call", lambda **kwargs: tracked.append(kwargs))
    monkeypatch.setattr(extraction, "save_if_current", lambda *args: pytest.fail("invalid output saved"))
    class FakeChat:
        def __init__(self, *args, **kwargs):
            self.requests = []
        def chat(self, *args, **kwargs):
            self.requests.append({"finish_reason": finish_reason, "usage": {"cost": .0003}})
            return content, extraction.MODEL
    monkeypatch.setattr(extraction, "BudgetedChat", FakeChat)
    stats = extraction.run_decision_cycle(budget_usd=Decimal("3"), campaign="existing", max_calls=1)
    assert stats["last_error"] == tracked[0]["error"] == reason
    assert settled == [("reserved-id", .0003, "invalid_response")]


def test_source_snapshot_is_invalidated_and_rechecked_before_save(monkeypatch):
    url = os.getenv("GEO_PULSE_TEST_DATABASE_URL")
    if not url:
        pytest.skip("GEO_PULSE_TEST_DATABASE_URL required")
    engine = create_engine(url)
    schema = "decision_test_" + uuid4().hex
    with engine.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    local = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        with local.begin() as conn:
            conn.exec_driver_sql("""
                CREATE TABLE sources(id int primary key,name text,country_code text,config jsonb default '{}');
                CREATE TABLE countries(code char(2) primary key);
                CREATE TABLE articles(id bigint primary key,source_id int,title text,body text,summary text,
                    published_at timestamptz,collected_at timestamptz,is_duplicate bool default false,
                    publisher_source_id int,geo_status text default 'source_verified',geo_country_code char(2));
                CREATE TABLE analysis(article_id bigint primary key,is_relevant bool);
                CREATE VIEW article_country_facts AS SELECT a.id article_id,s.country_code
                    FROM articles a JOIN sources s ON s.id=a.source_id WHERE a.geo_status='source_verified';
                INSERT INTO sources VALUES(1,'Example','RS','{}');
                INSERT INTO countries VALUES('RS');
                INSERT INTO articles VALUES(1,1,'Serbia minister said Russian citizens may enter',
                    'The ministry said Russian citizens may enter Serbia.',NULL,now()-interval '1 hour',now(),false,NULL,'source_verified','RS');
                INSERT INTO articles VALUES
                    (2,1,'Future publication should not enter', 'Text',NULL,now()+interval '1 hour',now(),false,NULL,'source_verified','RS'),
                    (3,1,'Old publication should not revive', 'Text',NULL,now()-interval '8 days',now(),false,NULL,'source_verified','RS'),
                    (4,1,'Duplicate report should not enter', 'Text',NULL,now()-interval '1 hour',now(),true,NULL,'source_verified','RS'),
                    (5,1,'Unverified publisher should not enter', 'Text',NULL,now()-interval '1 hour',now(),false,NULL,'unverified','RS');
                INSERT INTO analysis VALUES(1,true);
            """)
            conn.exec_driver_sql(Path("scripts/migrations/037_article_decision_annotations.sql").read_text())
            conn.exec_driver_sql(Path("scripts/migrations/038_article_news_triage.sql").read_text())
        factory = sessionmaker(bind=local)
        @contextmanager
        def session():
            with factory() as db:
                try:
                    yield db
                    db.commit()
                except Exception:
                    db.rollback()
                    raise
        monkeypatch.setattr(extraction, "get_session", session)
        rows, codes = extraction.load_candidates()
        assert len(rows) == 1 and codes == {"RS"}
        assert extraction.save_if_current(rows[0], annotation())
        assert extraction.load_candidates()[0] == []
        with monkeypatch.context() as evidence_mode:
            evidence_mode.setenv('JEV_EVIDENCE_MODE', 'apply')
            assert [r['id'] for r in extraction.load_candidates()[0]] == [1]
            original = rows[0]
            lines = source_segments.segments(original)
            identity = next(key for key, line in lines.items() if 'Serbia' in line['quote'])
            proven = annotation(russia_evidence_quote=lines[identity]['quote'],
                countries=[{'code':'RS', 'evidence_quote':lines[identity]['quote']}])
            proven = source_segments.seal_review(original, proven,
                {key:identity for key in ('headline','summary','russia','country_RS')})
            assert extraction.save_if_current(original, proven)
            assert extraction.load_candidates()[0] == []
        with session() as db:
            db.execute(text("UPDATE articles SET body='Corrected source' WHERE id=1"))
        assert not extraction.save_if_current(rows[0], annotation())
        changed = extraction.load_candidates()[0]
        assert len(changed) == 1 and changed[0]["excerpt"] == "Corrected source"
        with session() as db:
            db.execute(text("INSERT INTO sources VALUES(2,'Russian publisher','RU','{}')"))
            db.execute(text("INSERT INTO countries VALUES('RU')"))
            db.execute(text("INSERT INTO countries VALUES('IL')"))
            db.execute(text("""INSERT INTO articles VALUES
                (6,2,'Russian publisher reports Serbia decision','Serbia and Russian citizens',NULL,
                 now()-interval '30 minutes',now(),false,NULL,'source_verified','RU'),
                (7,1,'Stale mismatched country attribution','Text',NULL,
                 now()-interval '30 minutes',now(),false,NULL,'source_verified','IL')"""))
        assert {row["id"] for row in extraction.load_candidates()[0]} == {1, 6}
        assert {row["id"] for row in extraction.load_candidates(article_ids=[6])[0]} == {6}
        with session() as db:
            db.execute(text("""
                INSERT INTO articles
                  (id,source_id,title,body,published_at,collected_at,is_duplicate,geo_status,geo_country_code)
                SELECT 100+i,1,'Cached recent publication '||i,'Already reviewed',
                  now()-interval '10 minutes',now(),false,'source_verified','RS'
                FROM generate_series(1,60) AS i
            """))
            db.execute(text("""
                INSERT INTO article_decision_annotations
                  (article_id,source_title,source_excerpt,annotation,model,version)
                SELECT id,title,'Already reviewed','{}'::jsonb,:model,:version
                FROM articles WHERE id BETWEEN 101 AND 160
            """), {"model": extraction.MODEL, "version": extraction.VERSION})
        assert {row["id"] for row in extraction.load_candidates()[0]} == {1, 6}
        with session() as db:
            db.execute(text("""
                INSERT INTO articles
                  (id,source_id,title,body,published_at,collected_at,is_duplicate,geo_status,geo_country_code)
                SELECT 200+i,1,'Newer unclassified report '||i,'No verified detail',
                  now()-interval '5 minutes',now(),false,'source_verified','RS'
                FROM generate_series(1,61) AS i
            """))
            db.execute(text("""
                INSERT INTO article_news_triage
                  (article_id,source_title,source_excerpt,classification,model,version)
                SELECT id,title,LEFT(COALESCE(NULLIF(body,''),summary,''),2000),
                  CAST(:classification AS jsonb),:model,:version
                FROM articles WHERE id IN (1,261)
            """), {"model": extraction.TRIAGE_MODEL, "version": extraction.TRIAGE_VERSION,
                   "classification": json.dumps({"russia_relation":"direct", "countries":["RS"]})})
            db.execute(text("""
                UPDATE article_news_triage
                SET classification=CAST(:classification AS jsonb) WHERE article_id=261
            """), {"classification": json.dumps({"russia_relation":"none", "countries":[]})})
        selected, _ = extraction.load_candidates()
        assert selected[0]["id"] == 1 and selected[0]["triage_positive"] is True
        assert 261 not in {row["id"] for row in selected}
        assert any(row["id"] >= 201 for row in selected)
        assert extraction.load_candidates(article_ids=[261])[0] == []
        with session() as db:
            db.execute(text("UPDATE articles SET body='Corrected unclassified source' WHERE id=261"))
        assert {row["id"] for row in extraction.load_candidates(article_ids=[261])[0]} == {261}
    finally:
        local.dispose()
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()

@pytest.mark.parametrize('verdict', ['accept', 'reject', 'error'])
def test_positive_extraction_is_reviewed_before_any_save(monkeypatch, verdict):
    calls = []
    monkeypatch.setenv('OPENROUTER_API_KEY', 'test-key')
    monkeypatch.setattr(extraction, 'load_candidates', lambda article_ids=None: ([article()], {'RS'}))
    monkeypatch.setattr(extraction.budget, 'get_attempted_pair_keys', lambda campaign: set())
    monkeypatch.setattr(extraction.budget, 'get_budget', lambda campaign: 2.0)
    monkeypatch.setattr(extraction.budget, 'reserve_request', lambda *a, **k: 'generated')
    monkeypatch.setattr(extraction.budget, 'finish_request', lambda *a: calls.append('settled'))
    monkeypatch.setattr(extraction, 'track_api_call', lambda **k: None)
    class FakeChat:
        def __init__(self, *a, **k): self.requests = []
        def chat(self, *a, **k):
            self.requests.append({'finish_reason':'stop', 'usage':{'cost':.001}})
            return json.dumps(annotation()), extraction.MODEL
    monkeypatch.setattr(extraction, 'BudgetedChat', FakeChat)
    reviewed = annotation(positions=[])
    def review(source, value, **kwargs):
        assert calls == ['settled']
        assert kwargs == {'campaign':'existing', 'budget_usd':Decimal('3')}
        calls.append('review')
        if verdict == 'error': raise RuntimeError('unavailable')
        return reviewed if verdict == 'accept' else None
    monkeypatch.setattr(extraction, 'verify_annotation', review)
    def save(source, value):
        assert value == reviewed
        calls.append('save')
        return True
    monkeypatch.setattr(extraction, 'save_if_current', save)
    result = extraction.run_decision_cycle(budget_usd=Decimal('3'), campaign='existing', max_calls=1)
    assert result['saved'] == (1 if verdict == 'accept' else 0)
    assert calls == ['settled','review'] + (['save'] if verdict == 'accept' else [])


def test_single_json_fence_and_exact_original_actor_are_normalized_without_guessing():
    source = article(excerpt='The ministry said Russian citizens may enter Serbia. Students in Blockade said it.')
    value = annotation(positions=[dict(actor='Students in Blockade',actor_type='ngo',position_ru='Высказалось о въезде',evidence_quote='Students in Blockade said it.')])
    parsed = extraction.parse_annotation('```json\n'+json.dumps(value)+'\n```', source, {'RS'})
    assert parsed['positions'][0]['actor'] == 'Участник: Students in Blockade'
    value['positions'][0]['actor'] = 'Unmentioned author'
    with pytest.raises(ValueError):
        extraction.parse_annotation(json.dumps(value), source, {'RS'})
    with pytest.raises(ValueError):
        extraction.parse_annotation('Here is my answer:\n```json\n'+json.dumps(annotation())+'\n```', source, {'RS'})


def test_country_citation_uses_existing_source_line_without_inventing_country_relation():
    source=article()
    value=annotation(countries=[dict(code='RS',evidence_quote='Russian citizens')])
    result=extraction.parse_annotation(json.dumps(value),source,{'RS'})
    assert 'Serbia' in result['countries'][0]['evidence_quote']
    assert result['countries'][0]['evidence_quote'] in source['title']+'\n'+source['excerpt']


def test_only_reviewed_annotation_may_drop_optional_generated_text():
    value=annotation(summary_ru='',russia_explanation_ru='')
    with pytest.raises(ValueError): extraction.validate_annotation(value,article(),{'RS'})
    assert extraction.validate_annotation(value,article(),{'RS'},reviewed=True)==value
