import hashlib
from pathlib import Path

import pytest

from src.embedding_store import (
    EmbeddingJob,
    EmbeddingProfile,
    EmbeddingProvider,
    EmbeddingStore,
    LegacyEmbeddingProvider,
    content_hash,
    embedding_job_key,
    semantic_search_response,
)
from scripts.index_embeddings import index_pending


def test_content_hash_is_sha256_of_utf8_content():
    content = "Массаракш: canonical entity"

    assert content_hash(content) == hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_embedding_profile_exposes_provider_neutral_metadata():
    profile = EmbeddingProfile(
        profile_key="jina-v3-docs",
        provider="jina",
        model="jina-embeddings-v3",
        dimensions=1024,
        task="text-matching",
        version="3",
        active=True,
        id=4,
    )

    assert profile.provider == "jina"
    assert profile.model == "jina-embeddings-v3"
    assert profile.dimensions == 1024
    assert profile.task == "text-matching"
    assert profile.version == "3"


def test_legacy_adapter_keeps_document_and_query_methods_separate():
    calls = []
    profile = EmbeddingProfile(
        profile_key="test-profile",
        provider="test",
        model="test-model",
        dimensions=2,
        task="text-matching",
        version="1",
        active=True,
    )

    def embed_documents(texts):
        calls.append(("documents", texts))
        return [[1.0, 0.0] for _ in texts]

    def embed_query(text):
        calls.append(("query", text))
        return [0.0, 1.0]

    provider = LegacyEmbeddingProvider(
        profile,
        document_embedder=embed_documents,
        query_embedder=embed_query,
    )

    assert isinstance(provider, EmbeddingProvider)
    assert provider.profile() == profile
    assert provider.embed_documents(["one", "two"]) == [[1.0, 0.0], [1.0, 0.0]]
    assert provider.embed_query("question") == [0.0, 1.0]
    assert calls == [
        ("documents", ["one", "two"]),
        ("query", "question"),
    ]


def test_embedding_job_key_is_idempotent_and_content_sensitive():
    first = embedding_job_key(
        profile_id=4,
        object_type="article",
        object_id="42",
        content="same content",
    )
    second = embedding_job_key(
        profile_id=4,
        object_type="article",
        object_id="42",
        content="same content",
    )
    changed = embedding_job_key(
        profile_id=4,
        object_type="article",
        object_id="42",
        content="changed content",
    )

    assert first == second
    assert first.idempotency_key == second.idempotency_key
    assert first != changed


@pytest.mark.parametrize("digest", ["", "z" * 64])
def test_embedding_job_key_rejects_invalid_digest(digest):
    with pytest.raises(ValueError, match="SHA-256"):
        embedding_job_key(
            profile_id=4,
            object_type="article",
            object_id="42",
            digest=digest,
        )


def test_semantic_search_is_unavailable_without_active_profile():
    assert semantic_search_response(None) == {"semantic_search": "unavailable"}


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class ProfileResult(FakeResult):
    def fetchone(self):
        return self.rows[0] if self.rows else None


class ProfileSession:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, statement):
        return ProfileResult(self.rows)


def profile_row(profile_id):
    return {
        "id": profile_id,
        "profile_key": f"profile-{profile_id}",
        "provider": "test",
        "model": "test-model",
        "dimensions": 2,
        "task": "text-matching",
        "version": "1",
        "active": True,
    }


def test_active_profile_rejects_multiple_active_rows():
    session = ProfileSession([profile_row(4), profile_row(5)])

    with pytest.raises(RuntimeError, match="multiple active embedding profiles"):
        EmbeddingStore().active_profile(session)


class ClaimingSession:
    def __init__(self):
        self.claimed = False
        self.statements = []

    def execute(self, statement, params):
        sql = str(statement)
        self.statements.append((sql, params))
        if self.claimed:
            return FakeResult([])
        self.claimed = True
        return FakeResult(
            [
                {
                    "id": 11,
                    "profile_id": 4,
                    "object_type": "article",
                    "object_id": "42",
                    "content_hash": "a" * 64,
                    "attempts": 1,
                }
            ]
        )


def test_claim_jobs_atomically_scopes_pending_jobs_to_active_profile():
    session = ClaimingSession()
    store = EmbeddingStore()

    first = store.claim_jobs(session, profile_id=4, batch_size=10)
    second = store.claim_jobs(session, profile_id=4, batch_size=10)

    assert [job.id for job in first] == [11]
    assert second == []
    sql, params = session.statements[0]
    assert "status = 'pending'" in sql
    assert "ep.active = TRUE" in sql
    assert "FOR UPDATE OF ej SKIP LOCKED" in sql
    assert "status = 'processing'" in sql
    assert params == {"profile_id": 4, "batch_size": 10}


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class RecoverySession:
    def __init__(self):
        self.statement = None
        self.params = None

    def execute(self, statement, params):
        self.statement = str(statement)
        self.params = params
        return ScalarResult(2)


def test_stale_processing_jobs_are_requeued_before_claiming():
    session = RecoverySession()

    recovered = EmbeddingStore().recover_stale_jobs(
        session,
        profile_id=4,
        lease_seconds=900,
    )

    assert recovered == 2
    assert "status = 'processing'" in session.statement
    assert "status = 'pending'" in session.statement
    assert "ep.active = TRUE" in session.statement
    assert "updated_at < now() - make_interval(secs => :lease_seconds)" in session.statement
    assert session.params == {"profile_id": 4, "lease_seconds": 900}


class NoExecuteSession:
    def execute(self, statement, params):
        raise AssertionError("invalid vectors must be rejected before persistence")


@pytest.mark.parametrize("vector", [[0.25], [float("nan"), 0.75], [float("inf"), 0.75]])
def test_record_success_rejects_wrong_dimension_and_non_finite_vectors(vector):
    profile = EmbeddingProfile(
        id=4,
        profile_key="test-profile",
        provider="test",
        model="test-model",
        dimensions=2,
        task="text-matching",
        version="1",
        active=True,
    )
    job = EmbeddingJob(
        id=11,
        profile_id=4,
        object_type="article",
        object_id="42",
        content_hash="a" * 64,
        attempts=1,
    )

    with pytest.raises(ValueError, match="embedding vector"):
        EmbeddingStore().record_success(
            NoExecuteSession(),
            job=job,
            profile=profile,
            embedding=vector,
        )


class IndexStore:
    def __init__(self, profile, jobs):
        self.profile = profile
        self.jobs = list(jobs)
        self.successes = []
        self.failures = []
        self.recoveries = []

    def active_profile(self, session):
        return self.profile

    def claim_jobs(self, session, *, profile_id, batch_size, dry_run=False):
        if not self.jobs:
            return []
        jobs, self.jobs = self.jobs[:batch_size], self.jobs[batch_size:]
        return jobs

    def count_pending_jobs(self, session, *, profile_id):
        return len(self.jobs)

    def recover_stale_jobs(self, session, *, profile_id, lease_seconds):
        self.recoveries.append((profile_id, lease_seconds))
        return 0

    def record_success(self, session, *, job, profile, embedding):
        self.successes.append((job, embedding))

    def record_failure(self, session, *, job, error, retry):
        self.failures.append((job, error, retry))


class DocumentOnlyProvider:
    def __init__(self, profile):
        self._profile = profile
        self.documents = []

    def profile(self):
        return self._profile

    def embed_documents(self, texts):
        self.documents.append(texts)
        return [[0.25, 0.75] for _ in texts]

    def embed_query(self, text):
        raise AssertionError("background indexing must not call embed_query")


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


def test_index_command_uses_document_method_for_active_profile():
    profile = EmbeddingProfile(
        id=4,
        profile_key="test-profile",
        provider="test",
        model="test-model",
        dimensions=2,
        task="text-matching",
        version="1",
        active=True,
    )
    document = "index this document"
    job = EmbeddingJob(
        id=11,
        profile_id=4,
        object_type="article",
        object_id="42",
        content_hash=content_hash(document),
        attempts=1,
    )
    store = IndexStore(profile, [job])
    provider = DocumentOnlyProvider(profile)
    sessions = RecordingSessionFactory()

    result = index_pending(
        batch_size=10,
        store=store,
        session_factory=sessions,
        provider_factory=lambda active_profile: provider,
        content_loader=lambda session, claimed_job: document,
    )

    assert result == {"processed": 1, "indexed": 1, "failed": 0}
    assert provider.documents == [[document]]
    assert store.successes == [(job, [0.25, 0.75])]
    assert store.failures == []
    assert store.recoveries == [(4, 900)]


def test_index_command_does_not_create_provider_without_active_profile():
    provider_calls = []
    store = IndexStore(None, [])

    result = index_pending(
        store=store,
        session_factory=RecordingSessionFactory(),
        provider_factory=lambda profile: provider_calls.append(profile),
    )

    assert result == {
        "processed": 0,
        "indexed": 0,
        "failed": 0,
        "semantic_search": "unavailable",
    }
    assert provider_calls == []


def test_index_command_rejects_provider_version_mismatch():
    active_profile = EmbeddingProfile(
        id=4,
        profile_key="active-v2",
        provider="test",
        model="test-model",
        dimensions=2,
        task="text-matching",
        version="2",
        active=True,
    )
    adapter_profile = EmbeddingProfile(
        profile_key="adapter-v1",
        provider="test",
        model="test-model",
        dimensions=2,
        task="text-matching",
        version="1",
        active=True,
    )

    with pytest.raises(RuntimeError, match="metadata does not match"):
        index_pending(
            store=IndexStore(active_profile, []),
            session_factory=RecordingSessionFactory(),
            provider_factory=lambda profile: DocumentOnlyProvider(adapter_profile),
        )


class MalformedProvider(DocumentOnlyProvider):
    def embed_documents(self, texts):
        self.documents.append(texts)
        return [[float("nan"), 0.75] for _ in texts]


def test_index_command_records_malformed_provider_vector_as_failure():
    profile = EmbeddingProfile(
        id=4,
        profile_key="test-profile",
        provider="test",
        model="test-model",
        dimensions=2,
        task="text-matching",
        version="1",
        active=True,
    )
    document = "index this document"
    job = EmbeddingJob(
        id=11,
        profile_id=4,
        object_type="article",
        object_id="42",
        content_hash=content_hash(document),
        attempts=1,
    )
    store = IndexStore(profile, [job])

    result = index_pending(
        store=store,
        session_factory=RecordingSessionFactory(),
        provider_factory=lambda active_profile: MalformedProvider(active_profile),
        content_loader=lambda session, claimed_job: document,
    )

    assert result == {"processed": 1, "indexed": 0, "failed": 1}
    assert store.successes == []
    assert len(store.failures) == 1
    assert "embedding vector" in store.failures[0][1]


@pytest.mark.parametrize(
    ("limit", "expected"),
    [(0, 5), (3, 3), (8, 5)],
)
def test_dry_run_counts_all_eligible_jobs_without_batch_truncation(limit, expected):
    profile = EmbeddingProfile(
        id=4,
        profile_key="test-profile",
        provider="test",
        model="test-model",
        dimensions=2,
        task="text-matching",
        version="1",
        active=True,
    )
    jobs = [
        EmbeddingJob(
            id=job_id,
            profile_id=4,
            object_type="article",
            object_id=str(job_id),
            content_hash="a" * 64,
            attempts=0,
        )
        for job_id in range(1, 6)
    ]
    store = IndexStore(profile, jobs)
    provider_calls = []

    result = index_pending(
        batch_size=2,
        limit=limit,
        dry_run=True,
        store=store,
        session_factory=RecordingSessionFactory(),
        provider_factory=lambda active_profile: provider_calls.append(active_profile),
    )

    assert result == {
        "processed": expected,
        "indexed": 0,
        "failed": 0,
        "dry_run": True,
    }
    assert provider_calls == []
    assert store.recoveries == []


def test_analyzer_image_includes_one_off_knowledge_and_index_commands():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile.analyzer").read_text()

    assert "scripts/backfill_knowledge.py" in dockerfile
    assert "scripts/index_embeddings.py" in dockerfile


def test_embedding_indexer_is_not_scheduled_in_compose():
    root = Path(__file__).resolve().parents[1]

    for filename in ("docker-compose.yml", "docker-compose.v2-dev.yml"):
        assert "index_embeddings.py" not in (root / filename).read_text()
