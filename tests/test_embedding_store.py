import hashlib
import importlib.util
from pathlib import Path

import pytest

from src import embedding_store as embedding_store_module
from src.embedding_store import (
    EmbeddingJob,
    EmbeddingProfile,
    EmbeddingProvider,
    EmbeddingProviderError,
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


def test_legacy_adapter_labels_openrouter_environment_profile(monkeypatch):
    for name in (
        "EMBEDDING_PROXY_URL",
        "JINA_API_KEY",
        "OPENAI_API_KEY",
        "OPENROUTER_EMBEDDING_MODEL",
        "OPENROUTER_EMBEDDING_DIMENSIONS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")

    provider = LegacyEmbeddingProvider.from_environment()

    assert provider is not None
    assert provider.profile() == EmbeddingProfile(
        profile_key=(
            "openrouter:openai/text-embedding-3-small:1536:text-matching:v1"
        ),
        provider="openrouter",
        model="openai/text-embedding-3-small",
        dimensions=1536,
        task="text-matching",
        version="v1",
        active=True,
    )


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_legacy_adapter_rejects_non_finite_document_and_query_vectors(invalid):
    profile = EmbeddingProfile(
        profile_key="test-profile",
        provider="test",
        model="test-model",
        dimensions=2,
        task="text-matching",
        version="1",
        active=True,
    )
    provider = LegacyEmbeddingProvider(
        profile,
        document_embedder=lambda texts: [[invalid, 0.0] for _ in texts],
        query_embedder=lambda text: [0.0, invalid],
    )

    with pytest.raises(EmbeddingProviderError, match="finite"):
        provider.embed_documents(["document"])
    with pytest.raises(EmbeddingProviderError, match="finite"):
        provider.embed_query("query")


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


class EnsureProfileSession:
    def __init__(self):
        self.profile_id = 17
        self.statements = []

    def execute(self, statement, params):
        sql = str(statement)
        self.statements.append((sql, params))
        if "pg_advisory_xact_lock" in sql:
            return ScalarResult(None)
        return ProfileResult(
            [
                {
                    "id": self.profile_id,
                    "profile_key": params["profile_key"],
                    "provider": params["provider"],
                    "model": params["model"],
                    "dimensions": params["dimensions"],
                    "task": params["task"],
                    "version": params["version"],
                    "active": True,
                }
            ]
        )


def test_ensure_active_profile_creates_or_reuses_configured_profile():
    session = EnsureProfileSession()
    configured = EmbeddingProfile(
        profile_key="openrouter:openai/text-embedding-3-small:1536:text-matching:v1",
        provider="openrouter",
        model="openai/text-embedding-3-small",
        dimensions=1536,
        task="text-matching",
        version="v1",
        active=True,
    )
    store = EmbeddingStore()

    first = store.ensure_active_profile(session, configured)
    second = store.ensure_active_profile(session, configured)

    assert first == second
    assert first.id == 17
    assert first.active is True
    lock_sql, lock_params = session.statements[0]
    assert "pg_advisory_xact_lock(:lock_key)" in lock_sql
    assert lock_params == {
        "lock_key": embedding_store_module.ACTIVE_PROFILE_LOCK_KEY,
    }
    sql, params = session.statements[1]
    assert "INSERT INTO embedding_profiles" in sql
    assert "ON CONFLICT (profile_key)" in sql
    assert "active = FALSE" in sql
    assert params["profile_key"] == configured.profile_key


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


class FencedTransitionSession:
    """Stateful SQL-contract fake for one reclaimed job generation."""

    def __init__(self):
        self.status = "processing"
        self.generation = 2
        self.embeddings = []
        self.statements = []

    def execute(self, statement, params):
        sql = str(statement)
        self.statements.append(sql)
        owned = (
            self.status == "processing"
            and params.get("generation") == self.generation
        )
        if "INSERT INTO content_embeddings" in sql:
            if "attempts = :generation" not in sql:
                self.embeddings.append(params["embedding"])
                self.status = "completed"
                return ScalarResult(True)
            if owned:
                self.embeddings.append(params["embedding"])
                self.status = "completed"
            return ScalarResult(owned)
        if "UPDATE embedding_jobs" in sql:
            if "attempts = :generation" not in sql:
                self.status = params.get("status", "completed")
                return ScalarResult(True)
            if owned:
                self.status = params.get("status", "completed")
            return ScalarResult(owned)
        return ScalarResult(False)


def test_reclaimed_job_generation_fences_late_success_and_failure():
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
    stale_a = EmbeddingJob(
        id=11,
        profile_id=4,
        object_type="article",
        object_id="42",
        content_hash="a" * 64,
        attempts=1,
    )
    owner_b = EmbeddingJob(
        id=11,
        profile_id=4,
        object_type="article",
        object_id="42",
        content_hash="a" * 64,
        attempts=2,
    )
    session = FencedTransitionSession()
    store = EmbeddingStore()

    assert store.record_success(
        session,
        job=stale_a,
        profile=profile,
        embedding=[0.25, 0.75],
    ) is False
    assert store.record_failure(
        session,
        job=stale_a,
        error="late A failure",
        retry=False,
    ) is False
    assert session.status == "processing"
    assert session.embeddings == []

    assert store.record_success(
        session,
        job=owner_b,
        profile=profile,
        embedding=[0.25, 0.75],
    ) is True
    assert session.status == "completed"
    assert len(session.embeddings) == 1

    assert store.record_failure(
        session,
        job=stale_a,
        error="later A failure",
        retry=False,
    ) is False
    assert all("attempts = :generation" in sql for sql in session.statements)


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


class ProjectionSession:
    def __init__(self):
        self.statement = None
        self.params = None

    def execute(self, statement, params):
        self.statement = str(statement)
        self.params = params
        return ScalarResult(True)


def test_article_projection_matches_physical_column_to_persisted_profile_dimensions():
    profile = EmbeddingProfile(
        id=4,
        profile_key="openrouter-profile",
        provider="openrouter",
        model="openai/text-embedding-3-small",
        dimensions=1024,
        task="text-matching",
        version="v1",
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
    session = ProjectionSession()

    assert EmbeddingStore().record_success(
        session,
        job=job,
        profile=profile,
        embedding=[0.25] * 1024,
    )

    assert "INSERT INTO content_embeddings" in session.statement
    assert "UPDATE public.analysis AS legacy_analysis" in session.statement
    assert "FROM stored" in session.statement
    assert "RETURNING profile_id, object_type, object_id, embedding" in session.statement
    assert "SET embedding = stored.embedding" in session.statement
    assert "JOIN embedding_profiles" in session.statement
    assert "JOIN pg_catalog.pg_attribute" in session.statement
    assert "attrelid = 'public.analysis'::regclass" in session.statement
    assert "attname = 'embedding'" in session.statement
    assert "atttypmod = ep.dimensions" in session.statement
    assert "active = TRUE" in session.statement
    assert "1536" not in session.statement
    assert ":dimensions" not in session.statement
    assert "stored.object_type = 'article'" in session.statement
    assert "legacy_analysis.article_id::text = stored.object_id" in session.statement
    assert "dimensions" not in session.params


def test_dimension_mismatch_keeps_authoritative_store_but_skips_legacy_projection():
    profile = EmbeddingProfile(
        id=4,
        profile_key="openrouter-profile",
        provider="openrouter",
        model="openai/text-embedding-3-small",
        dimensions=1536,
        task="text-matching",
        version="v1",
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
    session = ProjectionSession()

    assert EmbeddingStore().record_success(
        session,
        job=job,
        profile=profile,
        embedding=[0.25] * 1536,
    )

    stored_end = session.statement.index("), projected AS")
    stored_sql = session.statement[:stored_end]
    projected_sql = session.statement[stored_end:]
    assert "INSERT INTO content_embeddings" in stored_sql
    assert "atttypmod = ep.dimensions" in projected_sql
    assert "CAST(stored.embedding" not in projected_sql
    assert "CAST(:embedding" not in projected_sql
    assert "SELECT EXISTS(SELECT 1 FROM stored)" in projected_sql


def test_prepare_embedding_jobs_command_exists():
    assert importlib.util.find_spec("scripts.prepare_embedding_jobs") is not None


def _preparation_module():
    return importlib.import_module("scripts.prepare_embedding_jobs")


def configured_preparation_profile(profile_id=4):
    return EmbeddingProfile(
        id=profile_id,
        profile_key="openrouter:openai/text-embedding-3-small:1536:text-matching:v1",
        provider="openrouter",
        model="openai/text-embedding-3-small",
        dimensions=1536,
        task="text-matching",
        version="v1",
        active=True,
    )


class ArticleRowsSession:
    def __init__(self, rows):
        self.rows = rows
        self.statement = None
        self.params = None

    def execute(self, statement, params):
        self.statement = str(statement)
        self.params = params
        return FakeResult(self.rows)


class PreparationSessionFactory:
    def __call__(self):
        class SessionContext:
            def __enter__(self):
                return object()

            def __exit__(self, exc_type, exc, traceback):
                return False

        return SessionContext()


class PreparationStore:
    def __init__(self, profile, *, allow_mutation=True):
        self.profile = profile
        self.allow_mutation = allow_mutation
        self.ensure_calls = 0
        self.enqueue_calls = 0
        self.jobs = {}

    def active_profile(self, session):
        return self.profile

    def ensure_active_profile(self, session, configured):
        if not self.allow_mutation:
            raise AssertionError("dry-run must not create or activate a profile")
        self.ensure_calls += 1
        assert configured.profile_key == self.profile.profile_key
        return self.profile

    def enqueue_job(self, session, key):
        if not self.allow_mutation:
            raise AssertionError("dry-run must not enqueue jobs")
        self.enqueue_calls += 1
        return self.jobs.setdefault(key.idempotency_key, len(self.jobs) + 1)


def test_prepare_embedding_jobs_cli_defaults():
    args = _preparation_module().build_parser().parse_args([])

    assert args.days == 30
    assert args.limit == 500
    assert args.dry_run is False
    assert args.story_candidates is False


def test_prepare_embedding_jobs_cli_enables_story_candidate_mode():
    args = _preparation_module().build_parser().parse_args(
        ["--story-candidates", "--days", "14", "--limit", "8000"]
    )

    assert args.story_candidates is True
    assert args.days == 14
    assert args.limit == 8000


def test_story_eligible_loader_selects_verified_relevant_originals_and_excludes_ready_before_limit():
    """Breaks if scheduled preparation again uses the thread-quota population."""
    expected = {
        "id": 42,
        "title": "Eligible",
        "body": "Body",
        "summary": "Summary",
        "ready_content_hashes": [],
    }
    session = ArticleRowsSession([expected])

    rows = _preparation_module().load_story_eligible_articles(
        session,
        days=30,
        limit=500,
        profile_id=4,
    )

    assert rows == [expected]
    assert "JOIN analysis an ON an.article_id = a.id" in session.statement
    assert "an.is_relevant = TRUE" in session.statement
    assert "a.is_duplicate = FALSE" in session.statement
    assert "a.geo_country_code IS NOT NULL" in session.statement
    assert "a.published_at >= now() - make_interval(days => :days)" in session.statement
    assert "ce.status = 'ready'" in session.statement
    assert "embedding_jobs" in session.statement
    assert "job.status IN ('pending', 'processing', 'completed')" in session.statement
    assert "job.status = 'failed'" in session.statement
    assert session.statement.index("WHERE current_ready.content_hash IS NULL") < session.statement.index("LIMIT :limit")
    assert "ready_content_hashes" in session.statement
    assert "ARRAY(" in session.statement
    assert "ARRAY[" not in session.statement
    assert session.statement.index("WHERE current_ready.content_hash IS NULL") < session.statement.index("LIMIT :limit")
    assert session.params == {"days": 30, "limit": 500, "profile_id": 4}


def test_story_content_hash_sql_matches_empty_secondary_text_contract():
    sql = " ".join(_preparation_module()._embedding_content_sql().split())

    assert "WHEN COALESCE(a.body, '') <> ''" in sql
    assert "ELSE COALESCE(a.title, '') END" in sql


def test_enqueue_job_requeues_only_stale_failed_identity():
    class EnqueueSession:
        def __init__(self):
            self.sql = ""

        def execute(self, statement, _params):
            self.sql = str(statement)
            return ScalarResult(9)

    session = EnqueueSession()
    key = embedding_job_key(
        profile_id=4,
        object_type="article",
        object_id="42",
        digest="a" * 64,
    )

    assert EmbeddingStore().enqueue_job(session, key) == 9
    sql = " ".join(session.sql.split())
    assert "status = CASE" in sql
    assert "embedding_jobs.status = 'failed'" in sql
    assert "updated_at < now() - INTERVAL '6 hours'" in sql
    assert "THEN 'pending'" in sql


def test_story_candidate_loader_uses_canonical_country_and_bounded_priority():
    expected = {
        "id": 42,
        "title": "Eligible story article",
        "body": "Body",
        "summary": "Summary",
        "thread_id": 7,
        "country_code": "ES",
        "cross_country_peer_count": 3,
        "candidate_count": 5,
        "ready_count": 1,
        "ready_content_hashes": [],
    }
    session = ArticleRowsSession([expected])

    rows = _preparation_module().load_story_candidate_articles(
        session,
        days=14,
        limit=8000,
        profile_id=4,
    )

    assert rows == [expected]
    assert "JOIN thread_articles ta ON ta.thread_id = t.id" in session.statement
    assert "JOIN article_country_facts country_fact" in session.statement
    assert "TRIM(country_fact.country_code) = TRIM(t.country_code)" in session.statement
    assert "t.article_count > 0" in session.statement
    assert "a.published_at >= now() - make_interval(days => :days)" in session.statement
    assert "cross_country_peer_count > 0" in session.statement
    assert "CEIL(0.30 * COUNT(*))" in session.statement
    assert "AS needed_count" in session.statement
    assert "WHERE candidate.has_active_ready = FALSE" in session.statement
    assert "missing.missing_rank <= coverage.needed_count" in session.statement
    assert "cross_country_peer_count DESC" in session.statement
    assert "needed_count ASC" in session.statement
    assert "missing_rank ASC" in session.statement
    assert "LIMIT :limit" in session.statement
    assert "SELECT ce.embedding" not in session.statement
    assert session.params == {"days": 14, "limit": 8000, "profile_id": 4}

    useful = session.statement.index("cross_country_peer_count > 0")
    cheapest = session.statement.index("needed_count ASC")
    peers = session.statement.index("cross_country_peer_count DESC")
    thread = session.statement.index("thread_id ASC")
    article = session.statement.index("missing_rank ASC")
    assert useful < cheapest < peers < thread < article


def test_prepare_embedding_jobs_dry_run_does_not_mutate_profiles_or_jobs():
    module = _preparation_module()
    profile = configured_preparation_profile()
    store = PreparationStore(profile, allow_mutation=False)
    rows = [
        {
            "id": 42,
            "title": "Eligible",
            "body": "Body",
            "summary": "Summary",
        }
    ]

    result = module.prepare_jobs(
        days=30,
        limit=500,
        dry_run=True,
        store=store,
        session_factory=PreparationSessionFactory(),
        profile_factory=lambda: profile,
        article_loader=lambda session, **kwargs: rows,
    )

    assert result == {
        "eligible": 1,
        "enqueued": 0,
        "profile": profile.profile_key,
        "dry_run": True,
    }
    assert store.ensure_calls == 0
    assert store.enqueue_calls == 0
    assert store.jobs == {}


def test_story_eligible_dry_run_reports_ready_and_missing_current_coverage():
    module = _preparation_module()
    profile = configured_preparation_profile()
    ready_content = "Ready\nSummary"
    rows = [
        {
            "id": 42,
            "title": "Ready",
            "body": "Body",
            "summary": "Summary",
            "thread_id": 7,
            "country_code": "ES",
            "cross_country_peer_count": 2,
            "candidate_count": 3,
            "ready_count": 1,
            "ready_content_hashes": [content_hash(ready_content)],
        },
        {
            "id": 43,
            "title": "Missing",
            "body": "Body",
            "summary": "Summary",
            "thread_id": 8,
            "country_code": "FR",
            "cross_country_peer_count": 1,
            "candidate_count": 2,
            "ready_count": 0,
            "ready_content_hashes": [],
        },
    ]
    store = PreparationStore(profile, allow_mutation=False)

    result = module.prepare_jobs(
        days=30,
        limit=8000,
        dry_run=True,
        story_eligible_articles=True,
        store=store,
        session_factory=PreparationSessionFactory(),
        profile_factory=lambda: profile,
        article_loader=lambda session, **kwargs: rows,
        coverage_loader=lambda session, **kwargs: {
            "eligible": 2,
            "ready_current": 1,
            "missing_current": 1,
        },
    )

    assert result == {
        "eligible": 2,
        "enqueued": 0,
        "profile": profile.profile_key,
        "dry_run": True,
        "mode": "story_eligible_articles",
        "candidate_articles": 2,
        "ready_current": 1,
        "missing_current": 1,
    }
    assert store.ensure_calls == 0
    assert store.enqueue_calls == 0


def test_repeat_preparation_reuses_profile_and_does_not_duplicate_ready_job():
    module = _preparation_module()
    profile = configured_preparation_profile()
    store = PreparationStore(profile)
    rows = [
        {
            "id": 42,
            "title": "Eligible",
            "body": "Ignored because summary is preferred",
            "summary": "Summary",
        }
    ]
    kwargs = {
        "store": store,
        "session_factory": PreparationSessionFactory(),
        "profile_factory": lambda: profile,
        "article_loader": lambda session, **options: rows,
    }

    first = module.prepare_jobs(**kwargs)
    second = module.prepare_jobs(**kwargs)

    assert first["enqueued"] == second["enqueued"] == 1
    assert store.ensure_calls == 2
    assert store.enqueue_calls == 2
    assert len(store.jobs) == 1
    idempotency_key = next(iter(store.jobs))
    expected_content = "Eligible\nSummary"
    assert idempotency_key == f"4:article:42:{content_hash(expected_content)}"


class IndexStore:
    def __init__(self, profile, jobs, transition_result=True):
        self.profile = profile
        self.jobs = list(jobs)
        self.transition_result = transition_result
        self.successes = []
        self.failures = []
        self.success_attempts = []
        self.failure_attempts = []
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
        self.success_attempts.append((job, embedding))
        if self.transition_result:
            self.successes.append((job, embedding))
        return self.transition_result

    def record_failure(self, session, *, job, error, retry):
        self.failure_attempts.append((job, error, retry))
        if self.transition_result:
            self.failures.append((job, error, retry))
        return self.transition_result


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


def test_index_command_does_not_count_stale_success_or_failure_transitions():
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
    success_job = EmbeddingJob(
        id=11,
        profile_id=4,
        object_type="article",
        object_id="42",
        content_hash=content_hash(document),
        attempts=1,
    )
    stale_success = IndexStore(profile, [success_job], transition_result=False)

    success_result = index_pending(
        store=stale_success,
        session_factory=RecordingSessionFactory(),
        provider_factory=lambda active_profile: DocumentOnlyProvider(active_profile),
        content_loader=lambda session, claimed_job: document,
    )

    failure_job = EmbeddingJob(
        id=12,
        profile_id=4,
        object_type="article",
        object_id="missing",
        content_hash="a" * 64,
        attempts=1,
    )
    stale_failure = IndexStore(profile, [failure_job], transition_result=False)
    failure_result = index_pending(
        store=stale_failure,
        session_factory=RecordingSessionFactory(),
        provider_factory=lambda active_profile: DocumentOnlyProvider(active_profile),
        content_loader=lambda session, claimed_job: None,
    )

    assert success_result == {"processed": 0, "indexed": 0, "failed": 0}
    assert failure_result == {"processed": 0, "indexed": 0, "failed": 0}
    assert len(stale_success.success_attempts) == 1
    assert len(stale_failure.failure_attempts) == 1


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
    assert "scripts/prepare_embedding_jobs.py" in dockerfile
    assert "scripts/index_embeddings.py" in dockerfile


def test_openrouter_workers_accept_https_proxy_without_exposing_public_api():
    import yaml

    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    services = compose["services"]

    for service_name in ("analyzer", "briefs", "threads"):
        assert services[service_name]["environment"]["HTTPS_PROXY"] == "${HTTPS_PROXY:-}"
    assert "HTTPS_PROXY" not in services["api"]["environment"]


def test_embedding_workers_receive_openrouter_dimension_override_without_public_api():
    import yaml

    root = Path(__file__).resolve().parents[1]
    for filename in ("docker-compose.yml", "docker-compose.v2-dev.yml"):
        services = yaml.safe_load((root / filename).read_text())["services"]
        for service_name in ("analyzer", "threads"):
            assert services[service_name]["environment"][
                "OPENROUTER_EMBEDDING_DIMENSIONS"
            ] == "${OPENROUTER_EMBEDDING_DIMENSIONS:-1536}"
        assert "OPENROUTER_EMBEDDING_DIMENSIONS" not in services["api"]["environment"]


def test_embedding_indexer_is_not_scheduled_in_compose():
    root = Path(__file__).resolve().parents[1]

    for filename in ("docker-compose.yml", "docker-compose.v2-dev.yml"):
        assert "index_embeddings.py" not in (root / filename).read_text()
