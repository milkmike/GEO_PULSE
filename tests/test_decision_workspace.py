from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import json
import os
from uuid import uuid4

import pytest

from src.decision_workspace import project_decision_workspace, load_decision_workspace


NOW = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)


def test_api_annotation_contract_tracks_extractor_version_without_postgres():
    import src.decision_workspace as workspace
    from src.decision_extraction import MODEL, VERSION

    assert workspace.CURRENT_ANNOTATION_MODEL == MODEL
    assert workspace.CURRENT_ANNOTATION_VERSION == VERSION


def article(*, article_id=1, publisher_country="US", age_hours=2, collected_hours=1,
            title="Serbia changed entry rules for Russians", url="https://example.com/a",
            source_title=None, source_excerpt="Serbia changed entry rules for Russians today.",
            excerpt=None, relevant=True, country="RS", quote="Serbia changed entry rules for Russians"):
    source_title = title if source_title is None else source_title
    excerpt = source_excerpt if excerpt is None else excerpt
    return {
        "article_id": article_id, "title": title, "body_excerpt": excerpt,
        "source_title": source_title, "source_excerpt": source_excerpt,
        "published_at": NOW - timedelta(hours=age_hours),
        "collected_at": NOW - timedelta(hours=collected_hours),
        "publisher_name": "Example", "publisher_country_code": publisher_country,
        "publisher_url": "https://example.com", "url": url,
        "analyzed_at": NOW - timedelta(minutes=15),
        "annotation": {
            "relevant": relevant, "headline_ru": "Сербия изменила правила въезда",
            "summary_ru": "Источник сообщает об изменении правил въезда для россиян.",
            "russia_explanation_ru": "Затрагивает въезд россиян",
            "russia_evidence_quote": "Russians",
            "countries": [{"code": country, "evidence_quote": "Serbia"}],
            "kind": "decision",
            "positions": [{"actor": "Serbia", "actor_type": "government",
                           "position_ru": "Сообщается об изменении правил", "evidence_quote": quote}],
            "changes": [{"category": "travel", "change_ru": "Изменены правила въезда",
                         "evidence_quote": quote}],
        },
    }


def test_country_involvement_is_annotation_not_publisher_geography():
    result = project_decision_workspace(
        country={"code": "RS", "name": "Сербия", "region": "europe"},
        countries=[{"code": "RS", "name": "Сербия", "region": "europe"}],
        rows=[article(publisher_country="US")], now=NOW,
        attention=[], coverage={}, truncated=False,
    )
    assert result["brief"]["day"][0]["publisher_country_code"] == "US"
    assert result["positions"][0]["evidence"]["country_evidence_quote"] == "Serbia"
    assert result["topics"][0]["title"] == "Сербия изменила правила въезда"
    assert result["topics"][0]["evidence"][0]["article_id"] == 1


def test_reviewed_headline_survives_dropped_optional_prose_without_fallback():
    row = article()
    row["annotation"]["summary_ru"] = ""
    row["annotation"]["russia_explanation_ru"] = ""
    result = project_decision_workspace(
        country={"code": "RS", "name": "Сербия", "region": "europe"},
        countries=[], rows=[row], now=NOW, attention=[], coverage={}, truncated=False,
    )
    evidence = result["brief"]["day"][0]
    assert evidence["title_ru"] == "Сербия изменила правила въезда"
    assert evidence["russia_evidence_quote"] == "Russians"
    assert evidence["country_evidence_quote"] == "Serbia"
    assert evidence["summary_ru"] == ""
    assert evidence["russia_explanation_ru"] == ""


@pytest.mark.parametrize("field,value", [
    ("summary_ru", None),
    ("summary_ru", "a" * 1001),
    ("summary_ru", "<script>unsafe</script>"),
    ("russia_explanation_ru", ["wrong type"]),
    ("russia_explanation_ru", "https://example.com"),
])
def test_optional_prose_still_rejects_invalid_or_unsafe_values(field, value):
    row = article()
    row["annotation"][field] = value
    result = project_decision_workspace(
        country={"code": "RS", "name": "Сербия", "region": "europe"},
        countries=[], rows=[row], now=NOW, attention=[], coverage={}, truncated=False,
    )
    assert result["brief"]["day"] == []


@pytest.mark.parametrize("overrides", [
    {"source_title": "old title"},
    {"excerpt": "changed body"},
    {"age_hours": 24.01},
    {"age_hours": -0.01},
    {"collected_hours": -0.01},
    {"relevant": False},
    {"country": "KZ"},
    {"quote": "invented statement"},
])
def test_unsubstantiated_or_out_of_window_material_is_not_shown_in_day(overrides):
    result = project_decision_workspace(
        country={"code": "RS", "name": "Сербия", "region": "europe"},
        countries=[], rows=[article(**overrides)], now=NOW,
        attention=[], coverage={}, truncated=False,
    )
    if overrides == {"age_hours": 24.01}:
        assert result["brief"]["day"] == []
        assert len(result["brief"]["week"]) == 1
    elif overrides == {"quote": "invented statement"}:
        assert len(result["brief"]["day"]) == 1
        assert result["positions"] == []
        assert result["changes"] == []
    else:
        assert result["brief"]["day"] == []
        assert result["positions"] == []
        assert result["changes"] == []


def test_unsafe_url_is_removed_and_truncation_is_explained():
    result = project_decision_workspace(
        country={"code": "RS", "name": "Сербия", "region": "europe"},
        countries=[], rows=[article(url="javascript:alert(1)")], now=NOW,
        attention=[], coverage={}, truncated=True,
    )
    assert result["brief"]["day"][0]["url"] is None
    assert result["coverage"]["truncated"] is True
    assert result["coverage"]["limitations"]


def test_malformed_claim_arrays_do_not_break_the_read_only_projection():
    row = article()
    row["annotation"]["positions"] = None
    row["annotation"]["changes"] = {"bad": "shape"}
    result = project_decision_workspace(
        country={"code": "RS", "name": "Сербия", "region": "europe"},
        countries=[], rows=[row], now=NOW, attention=[], coverage={}, truncated=False,
    )
    assert len(result["brief"]["day"]) == 1
    assert result["positions"] == []
    assert result["changes"] == []


@pytest.mark.skipif(not os.environ.get("GEO_PULSE_TEST_DATABASE_URL"), reason="local PostgreSQL unavailable")
def test_postgres_projection_counts_country_involvement_without_publisher_confusion(monkeypatch):
    """Exercise the real SQL in an isolated schema, including source snapshot changes."""
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    import src.decision_workspace as module
    from src.decision_extraction import MODEL, VERSION

    engine = create_engine(os.environ["GEO_PULSE_TEST_DATABASE_URL"])
    schema = "decision_test_" + uuid4().hex[:12]
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
        connection.execute(text("""
            CREATE TABLE countries (code char(2), name_ru text, region text, active boolean)
        """))
        connection.execute(text("""
            CREATE TABLE sources (id integer, name text, country_code char(2), url text)
        """))
        connection.execute(text("""
            CREATE TABLE articles (id integer, source_id integer, title text, body text,
              summary text, published_at timestamptz, collected_at timestamptz,
              url text, resolved_url text, is_duplicate boolean,
              geo_country_code char(2), geo_status text)
        """))
        connection.execute(text("""
            CREATE TABLE article_decision_annotations (article_id bigint, source_title text,
              source_excerpt text, annotation jsonb, model text, version text,
              analyzed_at timestamptz)
        """))
        connection.execute(text("""
            CREATE TABLE article_news_triage (article_id bigint, source_title text,
              source_excerpt text, classification jsonb, model text, version text,
              classified_at timestamptz);
            CREATE TABLE article_title_translations (article_id bigint,source_title text,title_ru text);
            CREATE TABLE agenda_budget (campaign text,limit_usd numeric,charged_usd numeric,halted bool);
        """))
        connection.execute(text("""
            CREATE VIEW article_country_facts AS SELECT a.id AS article_id, s.name,
              s.country_code, s.url FROM articles a JOIN sources s ON s.id=a.source_id
        """))
        connection.execute(text("INSERT INTO countries VALUES ('RS','Сербия','europe',true),('US','США','america',true)"))
        connection.execute(text("""
            INSERT INTO sources VALUES
              (1,'Example','US','https://example.com'),
              (2,'Local','RS','https://local.rs')
        """))
        for ident, age, changed, collected_hours in ((1, 2, False, 1), (2, 2, True, 1),
                                                      (3, 200, False, 1), (4, 2, False, -1)):
            record = article(article_id=ident, age_hours=age, collected_hours=collected_hours)
            body = "changed after annotation" if changed else record["source_excerpt"]
            connection.execute(text("""
                INSERT INTO articles VALUES (:id,1,:title,:body,NULL,:published,:collected,
                    :url,NULL,false,'US','source_verified')
            """), {"id": ident, "title": record["title"], "body": body,
                     "published": record["published_at"], "collected": record["collected_at"],
                     "url": record["url"]})
            connection.execute(text("""
                    INSERT INTO article_decision_annotations VALUES
                        (:id,:title,:excerpt,CAST(:annotation AS jsonb),:model,:version,:analyzed)
            """), {"id": ident, "title": record["source_title"],
                     "excerpt": record["source_excerpt"],
                     "annotation": json.dumps(record["annotation"]),
                     "model": MODEL, "version": VERSION,
                     "analyzed": record["analyzed_at"]})
        connection.execute(text("""
            INSERT INTO articles VALUES
              (5,2,'Local item','Local body',NULL,:published,:collected,
                'https://local.rs/5',NULL,false,'US','source_verified'),
              (6,1,'Wrong publisher','Wrong body',NULL,:published,:collected,
                'https://example.com/6',NULL,false,'RS','source_verified'),
              (7,2,'Verified local','Local body',NULL,:published,:collected,
                'https://local.rs/7',NULL,false,'RS','source_verified'),
              (8,2,'Unverified local','Local body',NULL,:published,:collected,
                'https://local.rs/8',NULL,false,'RS','unverified')
        """), {"published": NOW-timedelta(hours=2), "collected": NOW-timedelta(hours=1)})
        for ident, version, model, source_id, geo in (
            (7, "decision-annotation-v1", MODEL, 2, "RS"),
            (9, "decision-annotation-v1", MODEL, 1, "US"),
            (10, VERSION, "other/model", 1, "US"),
        ):
            record = article(article_id=ident)
            if ident != 7:
                connection.execute(text("""
                    INSERT INTO articles VALUES (:id,:source_id,:title,:body,NULL,:published,:collected,
                        :url,NULL,false,:geo,'source_verified')
                """), {"id": ident, "source_id": source_id, "title": record["title"],
                         "body": record["source_excerpt"], "published": record["published_at"],
                         "collected": record["collected_at"], "url": record["url"], "geo": geo})
            else:
                record["title"] = "Verified local"
                record["source_excerpt"] = "Local body"
            connection.execute(text("""
                INSERT INTO article_decision_annotations VALUES
                    (:id,:title,:excerpt,CAST(:annotation AS jsonb),:model,:version,:analyzed)
            """), {"id": ident, "title": record["title"], "excerpt": record["source_excerpt"],
                     "annotation": json.dumps(record["annotation"]), "model": model,
                     "version": version, "analyzed": record["analyzed_at"]})

        connection.execute(text("ALTER TABLE articles ADD COLUMN language varchar(5)"))

    Session = sessionmaker(bind=engine)

    @contextmanager
    def session():
        with Session() as db:
            db.execute(text(f'SET search_path TO "{schema}"'))
            yield db

    monkeypatch.setattr(module, "get_session", session)
    try:
        result = load_decision_workspace("RS", now=NOW)
        assert result["discovery"] == {"day": [], "week": [], "unassigned_day": [], "unassigned_week": []}
        assert result["coverage"]["classified_from_country_7d"] == 0
        assert result["coverage"]["pending_from_country_7d"] == 1
        assert [a["article_id"] for a in result["brief"]["day"]] == [1]
        assert result["attention"][0]["count_24h"] == 1
        assert "Сербия изменила правила въезда" in result["attention"][0]["reason"]
        assert result["coverage"]["collected_from_country_7d"] == 1
        assert result["coverage"]["reviewed_from_country_7d"] == 0
        assert result["coverage"]["relevant_to_country_7d"] == 1
        assert result["coverage"]["publisher_families"] == 1
        assert result["coverage"]["local_publisher_families"] == 1
        with engine.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            connection.execute(text("UPDATE articles SET language='ru', title='Российские граждане обсуждают правила' WHERE id=7"))
            connection.execute(text("""INSERT INTO article_news_triage
                SELECT id,title,LEFT(body,2000),
                '{"countries":["RS"],"russia_relation":"uncertain","topic":"sanctions",
                  "event_type":"proposal","actor_type":"government","uncertain": true}'::jsonb,
                'typesafe/jev-1.13','news-triage-v2',:as_of
                FROM articles WHERE id IN (1,2,3,4,7,8)"""), {"as_of": NOW})
            # Stale classification must not count as processed or visible.
            connection.execute(text("UPDATE article_news_triage SET source_title='stale' WHERE article_id=2"))
        refreshed = load_decision_workspace("RS", now=NOW)
        assert {a["article_id"] for a in refreshed["discovery"]["day"]} == {1,7}
        assert next(a for a in refreshed["discovery"]["day"] if a["article_id"] == 7)["title_ru"] == "Российские граждане обсуждают правила"
        assert refreshed["coverage"]["classified_from_country_7d"] == 1
        assert refreshed["coverage"]["pending_from_country_7d"] == 0
        assert refreshed["coverage"]["discovered_to_country_7d"] == 2
        assert refreshed["coverage"]["triage_status"] == "up_to_date"
        # Discovery does not become a reviewed claim or alter confirmed counts.
        assert refreshed["brief"] == result["brief"]
        assert refreshed["changes"] == result["changes"]
    finally:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()
