from uuid import UUID, uuid4

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

    def suggest(self, *, query, limit, offset):
        self.calls.append(("suggest", query, limit, offset))
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
        }

    def detail(self, *, entity_id, limit, offset):
        self.calls.append(("detail", entity_id, limit, offset))
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
        ("suggest", "Путин", 5, 2),
        ("detail", entity_id, 3, 1),
    ]


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
