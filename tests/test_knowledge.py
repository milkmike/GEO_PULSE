import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.knowledge import (
    BackfillBatch,
    EntityAlias,
    KnowledgeBackfillService,
    legacy_entity_keys,
    mention_from_legacy_key,
    normalize_entity_name,
    registry_entity,
    resolve_alias,
    stable_node_id,
)
from scripts.backfill_knowledge import run_backfill
from src.api.routes.entities import (
    SqlEntityQueryService,
    decode_entity_cursor,
    encode_entity_cursor,
    get_entity_query_service,
    router,
    safe_public_url,
)


def test_normalize_entity_name_folds_yo_punctuation_and_whitespace():
    assert normalize_entity_name("  Алё!  На,   связи... ") == "але на связи"


def test_resolve_alias_refuses_ambiguous_matches():
    first_id = uuid4()
    second_id = uuid4()
    aliases = [
        EntityAlias(entity_id=first_id, alias="Союз"),
        EntityAlias(entity_id=second_id, alias=" союз "),
    ]

    assert resolve_alias("СОЮЗ", aliases) is None


def test_resolve_alias_refuses_explicitly_ambiguous_alias():
    entity_id = uuid4()
    aliases = [EntityAlias(entity_id=entity_id, alias="Грузия", ambiguous=True)]

    assert resolve_alias("грузия", aliases) is None


def test_resolve_alias_returns_only_unique_unambiguous_entity():
    entity_id = uuid4()
    aliases = [EntityAlias(entity_id=entity_id, alias="Владимир Путин")]

    assert resolve_alias(" владимир, путин ", aliases) == entity_id


def test_registry_entity_and_public_node_id_are_stable():
    first = registry_entity("putin")
    second = registry_entity("putin")

    assert first.id == second.id
    assert first.kind == "person"
    assert stable_node_id(first.kind, first.id) == f"person:{first.id}"
    assert UUID(str(first.id)) == first.id


def test_current_registry_key_becomes_evidence_bearing_mention():
    mention = mention_from_legacy_key(
        article_id=42,
        analysis_id=7,
        registry_key="putin",
        extractor_version="registry-2026-07-15",
    )

    assert mention.entity_id == registry_entity("putin").id
    assert mention.article_id == 42
    assert mention.extractor == "legacy_registry"
    assert mention.extractor_version == "registry-2026-07-15"
    assert mention.evidence == {
        "source": "analysis.entities",
        "analysis_id": 7,
        "legacy_key": "putin",
    }


def test_legacy_entity_json_remains_readable_and_unchanged():
    stored = ["putin", "lavrov", "unknown"]

    assert legacy_entity_keys(stored) == ("putin", "lavrov", "unknown")
    assert stored == ["putin", "lavrov", "unknown"]


class FakeEntityQueryService:
    def __init__(self, entity_id):
        self.entity_id = entity_id
        self.calls = []

    def suggest(self, *, query, limit, offset, cursor=None):
        self.calls.append(("suggest", query, limit, offset, cursor))
        return {
            "items": [
                {
                    "id": str(self.entity_id),
                    "node_id": f"person:{self.entity_id}",
                    "kind": "person",
                    "label": "Владимир Путин",
                    "aliases": ["Путин", "Putin"],
                    "match_explanation": "exact alias",
                }
            ],
            "limit": limit,
            "offset": offset,
            "has_more": False,
            "next_cursor": None,
        }

    def detail(self, *, entity_id, limit, offset, cursor=None):
        self.calls.append(("detail", entity_id, limit, offset, cursor))
        return {
            "id": str(entity_id),
            "node_id": f"person:{entity_id}",
            "kind": "person",
            "label": "Владимир Путин",
            "aliases": ["Путин", "Putin"],
            "mentions": {
                "items": [
                    {
                        "article_id": 42,
                        "evidence": {"source": "analysis.entities"},
                    }
                ],
                "limit": limit,
                "offset": offset,
                "has_more": False,
                "next_cursor": None,
            },
        }


def test_entity_routes_use_injected_query_service_without_live_database():
    entity_id = uuid4()
    service = FakeEntityQueryService(entity_id)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_entity_query_service] = lambda: service
    client = TestClient(app)

    suggest = client.get(
        "/api/v2/entities/suggest",
        params={"q": "Путин", "limit": 5, "offset": 2},
    )
    detail = client.get(
        f"/api/v2/entities/{entity_id}",
        params={"limit": 3, "offset": 1},
    )

    assert suggest.status_code == 200
    assert suggest.json()["items"][0]["match_explanation"] == "exact alias"
    assert detail.status_code == 200
    assert detail.json()["mentions"]["items"][0]["evidence"]
    assert service.calls == [
        ("suggest", "Путин", 5, 2, None),
        ("detail", entity_id, 3, 1, None),
    ]


def test_entity_cursor_round_trip_validates_scope_binding_and_shape():
    entity_id = uuid4()
    suggest_key = {
        "match_rank": 1,
        "canonical_name": "Владимир Путин",
        "id": str(entity_id),
    }
    token = encode_entity_cursor(
        scope="entity_suggest",
        binding={"q": "путин"},
        key=suggest_key,
    )

    assert decode_entity_cursor(
        token,
        scope="entity_suggest",
        binding={"q": "путин"},
    ) == suggest_key
    with pytest.raises(ValueError, match="binding"):
        decode_entity_cursor(
            token,
            scope="entity_suggest",
            binding={"q": "лавров"},
        )
    with pytest.raises(ValueError, match="cursor"):
        decode_entity_cursor(
            "not-valid-base64",
            scope="entity_suggest",
            binding={"q": "путин"},
        )


def raw_entity_cursor(*, scope, binding, key):
    payload = {"v": 1, "scope": scope, "binding": binding, "key": key}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_entity_routes_return_422_for_invalid_rebound_and_mixed_cursors():
    entity_id = uuid4()
    other_entity_id = uuid4()
    service = FakeEntityQueryService(entity_id)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_entity_query_service] = lambda: service
    client = TestClient(app)
    token = encode_entity_cursor(
        scope="entity_suggest",
        binding={"q": "путин"},
        key={
            "match_rank": 1,
            "canonical_name": "Владимир Путин",
            "id": str(entity_id),
        },
    )
    mention_token = raw_entity_cursor(
        scope="entity_mentions",
        binding={"entity_id": str(entity_id)},
        key={
            "published_at": datetime(2026, 7, 15, tzinfo=timezone.utc).isoformat(),
            "created_at": datetime(2026, 7, 15, tzinfo=timezone.utc).isoformat(),
            "article_id": 42,
            "extractor": "legacy_registry",
        },
    )

    suggest_invalid = client.get(
        "/api/v2/entities/suggest",
        params={"q": "Путин", "cursor": "not-valid-base64"},
    )
    suggest_rebound = client.get(
        "/api/v2/entities/suggest",
        params={"q": "Лавров", "cursor": token},
    )
    suggest_mixed = client.get(
        "/api/v2/entities/suggest",
        params={"q": "Путин", "cursor": token, "offset": 1},
    )
    detail_invalid = client.get(
        f"/api/v2/entities/{entity_id}",
        params={"cursor": "not-valid-base64"},
    )
    detail_rebound = client.get(
        f"/api/v2/entities/{other_entity_id}",
        params={"cursor": mention_token},
    )
    detail_mixed = client.get(
        f"/api/v2/entities/{entity_id}",
        params={"cursor": mention_token, "offset": 1},
    )

    assert suggest_invalid.status_code == 422
    assert suggest_rebound.status_code == 422
    assert suggest_mixed.status_code == 422
    assert detail_invalid.status_code == 422
    assert detail_rebound.status_code == 422
    assert detail_mixed.status_code == 422
    assert service.calls == []


def test_entity_routes_reject_json_boole_as_integer_cursor_keys():
    entity_id = uuid4()
    service = FakeEntityQueryService(entity_id)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_entity_query_service] = lambda: service
    client = TestClient(app)
    suggest_token = raw_entity_cursor(
        scope="entity_suggest",
        binding={"q": "путин"},
        key={
            "match_rank": True,
            "canonical_name": "Владимир Путин",
            "id": str(entity_id),
        },
    )
    mention_token = raw_entity_cursor(
        scope="entity_mentions",
        binding={"entity_id": str(entity_id)},
        key={
            "published_at": datetime(2026, 7, 15, tzinfo=timezone.utc).isoformat(),
            "created_at": datetime(2026, 7, 15, tzinfo=timezone.utc).isoformat(),
            "article_id": True,
            "extractor": "legacy_registry",
        },
    )

    suggest = client.get(
        "/api/v2/entities/suggest",
        params={"q": "Путин", "cursor": suggest_token},
    )
    detail = client.get(
        f"/api/v2/entities/{entity_id}",
        params={"cursor": mention_token},
    )

    assert suggest.status_code == 422
    assert detail.status_code == 422
    assert service.calls == []


class QueryResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class SequentialQuerySession:
    def __init__(self, result_sets):
        self.result_sets = list(result_sets)
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return QueryResult(self.result_sets.pop(0))


def session_factory_for(session):
    @contextmanager
    def factory():
        yield session

    return factory


def test_suggest_service_uses_keyset_tuple_and_emits_bound_next_cursor(monkeypatch):
    first_id = uuid4()
    second_id = uuid4()
    third_id = uuid4()
    rows = [
        {
            "id": first_id,
            "kind": "person",
            "canonical_name": "Владимир Путин",
            "normalized_name": "владимир путин",
            "labels": {"ru": "Владимир Путин"},
            "aliases": ["Путин"],
            "normalized_aliases": ["путин"],
            "match_rank": 1,
        },
        {
            "id": second_id,
            "kind": "person",
            "canonical_name": "Путин Второй",
            "normalized_name": "путин второй",
            "labels": {"ru": "Путин Второй"},
            "aliases": ["Путин II"],
            "normalized_aliases": ["путин ii"],
            "match_rank": 2,
        },
        {
            "id": third_id,
            "kind": "person",
            "canonical_name": "Путин Третий",
            "normalized_name": "путин третий",
            "labels": {"ru": "Путин Третий"},
            "aliases": [],
            "normalized_aliases": [],
            "match_rank": 2,
        },
    ]
    session = SequentialQuerySession([rows])
    monkeypatch.setattr(
        "src.api.routes.entities.get_session",
        session_factory_for(session),
    )
    cursor_key = {
        "match_rank": 0,
        "canonical_name": "Предыдущий",
        "id": str(uuid4()),
    }

    response = SqlEntityQueryService().suggest(
        query="Путин",
        limit=2,
        offset=0,
        cursor=cursor_key,
    )

    sql, params = session.calls[0]
    assert "c.match_rank > :cursor_rank" in sql
    assert "c.canonical_name > :cursor_name" in sql
    assert "c.id > CAST(:cursor_id AS uuid)" in sql
    assert "ORDER BY c.match_rank, c.canonical_name, c.id" in sql
    assert params["cursor_rank"] == 0
    assert response["has_more"] is True
    assert decode_entity_cursor(
        response["next_cursor"],
        scope="entity_suggest",
        binding={"q": "путин"},
    ) == {
        "match_rank": 2,
        "canonical_name": "Путин Второй",
        "id": str(second_id),
    }


def test_detail_mentions_use_descending_keyset_and_entity_bound_cursor(monkeypatch):
    entity_id = uuid4()
    published = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    created = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    older = datetime(2026, 7, 14, 10, 0, tzinfo=timezone.utc)
    entity_rows = [{
        "id": entity_id,
        "kind": "person",
        "canonical_name": "Владимир Путин",
        "normalized_name": "владимир путин",
        "labels": {"ru": "Владимир Путин"},
        "country_codes": ["RU"],
        "provenance": {},
        "created_at": created,
        "updated_at": created,
    }]
    mention_rows = [
        {
            "article_id": 42,
            "mention_text": "Путин",
            "char_start": 0,
            "char_end": 5,
            "extractor": "legacy_registry",
            "extractor_version": "1",
            "confidence": 0.9,
            "evidence": {"source": "analysis.entities"},
            "created_at": created,
            "title": "Article",
            "url": "https://example.org/article",
            "published_at": published,
        },
        {
            "article_id": 41,
            "mention_text": "Путин",
            "char_start": 0,
            "char_end": 5,
            "extractor": "legacy_registry",
            "extractor_version": "1",
            "confidence": 0.9,
            "evidence": {"source": "analysis.entities"},
            "created_at": older,
            "title": "Older",
            "url": "https://example.org/older",
            "published_at": older,
        },
    ]
    session = SequentialQuerySession([entity_rows, [], mention_rows])
    monkeypatch.setattr(
        "src.api.routes.entities.get_session",
        session_factory_for(session),
    )
    cursor_key = {
        "published_at": datetime(2026, 7, 16, tzinfo=timezone.utc).isoformat(),
        "created_at": datetime(2026, 7, 16, tzinfo=timezone.utc).isoformat(),
        "article_id": 99,
        "extractor": "zeta",
    }

    response = SqlEntityQueryService().detail(
        entity_id=entity_id,
        limit=1,
        offset=0,
        cursor=cursor_key,
    )

    sql, params = session.calls[2]
    assert "ar.published_at < CAST(:cursor_published_at AS timestamptz)" in sql
    assert "aem.created_at < CAST(:cursor_created_at AS timestamptz)" in sql
    assert "aem.article_id < :cursor_article_id" in sql
    assert "aem.extractor < :cursor_extractor" in sql
    assert "ORDER BY ar.published_at DESC" in sql
    assert "aem.article_id DESC" in sql
    assert "aem.extractor DESC" in sql
    assert params["cursor_article_id"] == 99
    assert params["cursor_extractor"] == "zeta"
    assert response["created_at"] == created.isoformat()
    assert response["updated_at"] == created.isoformat()
    assert response["mentions"]["has_more"] is True
    assert decode_entity_cursor(
        response["mentions"]["next_cursor"],
        scope="entity_mentions",
        binding={"entity_id": str(entity_id)},
    ) == {
        "published_at": published.isoformat(),
        "created_at": created.isoformat(),
        "article_id": 42,
        "extractor": "legacy_registry",
    }


def test_detail_mentions_do_not_skip_same_article_mentions_by_extractor(monkeypatch):
    entity_id = uuid4()
    published = datetime(2026, 7, 15, 10, 0, tzinfo=timezone.utc)
    created = datetime(2026, 7, 15, 11, 0, tzinfo=timezone.utc)
    entity_rows = [{
        "id": entity_id,
        "kind": "person",
        "canonical_name": "Владимир Путин",
        "normalized_name": "владимир путин",
        "labels": {"ru": "Владимир Путин"},
        "country_codes": ["RU"],
        "provenance": {},
        "created_at": created,
        "updated_at": created,
    }]

    def mention(extractor):
        return {
            "article_id": 42,
            "mention_text": "Путин",
            "char_start": 0,
            "char_end": 5,
            "extractor": extractor,
            "extractor_version": "1",
            "confidence": 0.9,
            "evidence": {"source": extractor},
            "created_at": created,
            "title": "Article",
            "url": "https://example.org/article",
            "published_at": published,
        }

    zeta = mention("zeta")
    alpha = mention("alpha")
    session = SequentialQuerySession(
        [entity_rows, [], [zeta, alpha], entity_rows, [], [alpha]]
    )
    monkeypatch.setattr(
        "src.api.routes.entities.get_session",
        session_factory_for(session),
    )
    service = SqlEntityQueryService()

    first = service.detail(entity_id=entity_id, limit=1, offset=0)
    cursor = decode_entity_cursor(
        first["mentions"]["next_cursor"],
        scope="entity_mentions",
        binding={"entity_id": str(entity_id)},
    )
    second = service.detail(
        entity_id=entity_id,
        limit=1,
        offset=0,
        cursor=cursor,
    )

    extractors = [
        first["mentions"]["items"][0]["extractor"],
        second["mentions"]["items"][0]["extractor"],
    ]
    assert extractors == ["zeta", "alpha"]
    assert len(set(extractors)) == 2
    second_sql, second_params = session.calls[5]
    assert "aem.extractor < :cursor_extractor" in second_sql
    assert second_params["cursor_extractor"] == "zeta"


def test_entity_detail_returns_not_found_from_injected_service():
    entity_id = uuid4()
    service = FakeEntityQueryService(entity_id)
    service.detail = lambda **_: None
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_entity_query_service] = lambda: service

    response = TestClient(app).get(f"/api/v2/entities/{entity_id}")

    assert response.status_code == 404


def test_safe_public_url_allows_only_http_without_credentials():
    assert safe_public_url("https://example.org/story?id=1") == "https://example.org/story?id=1"
    assert safe_public_url("javascript:alert(1)") is None
    assert safe_public_url("https://user:secret@example.org/private") is None
    assert safe_public_url("//example.org/no-scheme") is None
    assert safe_public_url("https://example.org/not safe") is None
    assert safe_public_url("https://example.org\\@evil.example/path") is None


class StubBackfillService:
    def __init__(self):
        self.after_ids = []

    def seed_registry(self, session):
        return 43

    def backfill_batch(self, session, *, after_analysis_id, batch_size):
        self.after_ids.append(after_analysis_id)
        if after_analysis_id == 0:
            return BackfillBatch(
                analyses_seen=2,
                mentions_upserted=3,
                last_analysis_id=8,
                done=False,
            )
        return BackfillBatch(
            analyses_seen=1,
            mentions_upserted=1,
            last_analysis_id=11,
            done=True,
        )


class RecordingSessionFactory:
    def __init__(self):
        self.sessions = []

    def __call__(self):
        factory = self

        class SessionContext:
            def __enter__(self):
                session = object()
                factory.sessions.append(session)
                return session

            def __exit__(self, exc_type, exc, traceback):
                return False

        return SessionContext()


def test_backfill_command_uses_one_transaction_per_ordered_batch():
    service = StubBackfillService()
    sessions = RecordingSessionFactory()

    result = run_backfill(
        batch_size=2,
        session_factory=sessions,
        service=service,
    )

    assert result == {
        "entities": 43,
        "analyses": 3,
        "mentions": 4,
        "last_analysis_id": 11,
    }
    assert service.after_ids == [0, 8]
    assert len(sessions.sessions) == 3  # registry seed + two committed batches


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class RegistrySeedSession:
    def __init__(self):
        self.statements = []

    def execute(self, statement, params):
        sql = str(statement)
        self.statements.append(sql)
        if "INSERT INTO canonical_entities" in sql:
            return ScalarResult(params["id"])
        return ScalarResult(None)


def test_registry_seed_upserts_by_deterministic_id():
    session = RegistrySeedSession()

    KnowledgeBackfillService().seed_registry(session)

    canonical_upserts = [
        statement
        for statement in session.statements
        if "INSERT INTO canonical_entities" in statement
    ]
    assert canonical_upserts
    assert all("ON CONFLICT (id)" in statement for statement in canonical_upserts)


def test_main_app_registers_entity_routes():
    from src.api.main import app

    paths = {route.path for route in app.routes}
    assert "/api/v2/entities/suggest" in paths
    assert "/api/v2/entities/{entity_id}" in paths
