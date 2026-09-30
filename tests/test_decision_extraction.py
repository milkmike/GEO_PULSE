from datetime import datetime, timezone
from decimal import Decimal
from contextlib import contextmanager
from pathlib import Path
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src import decision_extraction as extraction


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
    finally:
        local.dispose()
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        engine.dispose()
