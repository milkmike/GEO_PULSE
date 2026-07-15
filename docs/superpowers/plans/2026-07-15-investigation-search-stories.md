# Investigation Search and Stories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build hybrid article search, canonical knowledge and vector-ready foundations, cross-country stories, evidence-rich signals, RRI shift investigation, transparent Thermometer methodology, and their integrated web experiences.

**Architecture:** PostgreSQL remains the source of truth and search engine. New focused domain modules sit behind additive FastAPI v2 routers; existing collectors, API v1, country threads, Thermometer v1, and RRI v1 remain compatible. The Next.js application adds shareable search, story, and signal routes plus investigation panels, while paid LLM and embedding work stays in idempotent background jobs.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL 16 with `pg_trgm` and pgvector, pytest, Next.js 15, React 19, TypeScript, Motion, Vitest, Testing Library.

## Global Constraints

- Public product name is «Массаракш» and public domain is `massaraksh.tech`.
- Do not break API v1, Thermometer v1, RRI v1, or existing country-thread responses.
- Keep the current dark editorial visual identity and reduced-motion behavior.
- Public GET requests must never trigger an LLM or embedding-provider call.
- PostgreSQL is the only new retrieval dependency; do not add OpenSearch, Meilisearch, or a graph database.
- Lexical search must work when embeddings are unavailable.
- Real vector retrieval is not activated in this package; only model-versioned storage, jobs, adapters, and response contracts are added.
- Migrations are additive, idempotent, and mirrored in `data/init.sql`.
- Every ranked or clustered item exposes why it was included, relevance, confidence, and evidence.
- Exact RRI component changes and contextual correlations are separate fields and separate UI sections.
- External article links are emitted only for valid HTTP(S) URLs.
- All backend behavior changes follow red-green-refactor TDD.

---

### Task 1: Additive Search, Knowledge, Story, and Evidence Schema

**Files:**
- Create: `scripts/migrations/019_search_knowledge.sql`
- Create: `scripts/migrations/020_global_stories.sql`
- Create: `scripts/migrations/021_signal_evidence_explanations.sql`
- Modify: `data/init.sql`
- Modify: `src/db.py`
- Create: `tests/test_schema_contract.py`

**Interfaces:**
- Produces: search vector and tables `canonical_entities`, `entity_aliases`, `article_entity_mentions`, `knowledge_edges`, `embedding_profiles`, `content_embeddings`, `embedding_jobs`, `stories`, `story_articles`, `story_countries`, `story_entities`, `story_events`, `signal_evidence`, and `index_change_explanations`.
- Produces: SQLAlchemy models with the same table and field names for later tasks.
- Preserves: existing `analysis.embedding vector` column and all existing tables.

- [ ] **Step 1: Write the failing schema contract test**

```python
# tests/test_schema_contract.py
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def migration(name: str) -> str:
    return (ROOT / "scripts" / "migrations" / name).read_text()


def test_search_and_knowledge_schema_contract():
    sql = migration("019_search_knowledge.sql")
    for fragment in (
        "ADD COLUMN IF NOT EXISTS search_vector tsvector",
        "CREATE INDEX IF NOT EXISTS idx_articles_search_vector",
        "CREATE TABLE IF NOT EXISTS canonical_entities",
        "CREATE TABLE IF NOT EXISTS article_entity_mentions",
        "CREATE TABLE IF NOT EXISTS knowledge_edges",
        "CREATE TABLE IF NOT EXISTS embedding_profiles",
        "CREATE TABLE IF NOT EXISTS content_embeddings",
        "CREATE TABLE IF NOT EXISTS embedding_jobs",
        "ALTER TABLE analysis ADD COLUMN IF NOT EXISTS embedding vector",
    ):
        assert fragment in sql


def test_story_schema_contract():
    sql = migration("020_global_stories.sql")
    for table in ("stories", "story_articles", "story_countries", "story_entities", "story_events"):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in sql


def test_evidence_schema_contract():
    sql = migration("021_signal_evidence_explanations.sql")
    assert "CREATE TABLE IF NOT EXISTS signal_evidence" in sql
    assert "CREATE TABLE IF NOT EXISTS index_change_explanations" in sql


def test_init_schema_mirrors_new_tables():
    init = (ROOT / "data" / "init.sql").read_text()
    for table in (
        "canonical_entities", "entity_aliases", "article_entity_mentions",
        "knowledge_edges", "embedding_profiles", "content_embeddings",
        "embedding_jobs", "stories", "story_articles", "story_countries",
        "story_entities", "story_events", "signal_evidence",
        "index_change_explanations",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in init
```

- [ ] **Step 2: Run the contract test and verify it fails because migration 019 is missing**

Run: `pytest tests/test_schema_contract.py -q`  
Expected: FAIL with `FileNotFoundError` for `019_search_knowledge.sql`.

- [ ] **Step 3: Add migration 019 with generated lexical search and canonical knowledge tables**

Implement these exact invariants:

```sql
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE analysis ADD COLUMN IF NOT EXISTS embedding vector;

ALTER TABLE articles ADD COLUMN IF NOT EXISTS search_vector tsvector
GENERATED ALWAYS AS (
  setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
  setweight(to_tsvector('simple', coalesce(summary, '')), 'B') ||
  setweight(to_tsvector('simple', coalesce(body, '')), 'C')
) STORED;
CREATE INDEX IF NOT EXISTS idx_articles_search_vector ON articles USING gin(search_vector);

CREATE TABLE IF NOT EXISTS canonical_entities (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind VARCHAR(24) NOT NULL CHECK (kind IN ('person','organization','location','event')),
  canonical_name TEXT NOT NULL,
  normalized_name TEXT NOT NULL,
  labels JSONB NOT NULL DEFAULT '{}',
  country_codes TEXT[] NOT NULL DEFAULT '{}',
  provenance JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(kind, normalized_name)
);

CREATE TABLE IF NOT EXISTS entity_aliases (
  id BIGSERIAL PRIMARY KEY,
  entity_id UUID NOT NULL REFERENCES canonical_entities(id) ON DELETE CASCADE,
  alias TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  language VARCHAR(8),
  ambiguous BOOLEAN NOT NULL DEFAULT FALSE,
  provenance JSONB NOT NULL DEFAULT '{}',
  UNIQUE(entity_id, normalized_alias)
);

CREATE TABLE IF NOT EXISTS article_entity_mentions (
  article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  entity_id UUID NOT NULL REFERENCES canonical_entities(id) ON DELETE CASCADE,
  mention_text TEXT,
  char_start INTEGER,
  char_end INTEGER,
  extractor VARCHAR(80) NOT NULL,
  extractor_version VARCHAR(40),
  confidence NUMERIC(4,3) NOT NULL DEFAULT 1.0,
  evidence JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(article_id, entity_id, extractor)
);

CREATE TABLE IF NOT EXISTS knowledge_edges (
  id BIGSERIAL PRIMARY KEY,
  source_node TEXT NOT NULL,
  target_node TEXT NOT NULL,
  relation VARCHAR(80) NOT NULL,
  confidence NUMERIC(4,3) NOT NULL,
  evidence JSONB NOT NULL DEFAULT '[]',
  valid_from TIMESTAMPTZ,
  valid_to TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(source_node, target_node, relation)
);

CREATE TABLE IF NOT EXISTS embedding_profiles (
  id SERIAL PRIMARY KEY,
  profile_key VARCHAR(80) UNIQUE NOT NULL,
  provider VARCHAR(40) NOT NULL,
  model VARCHAR(120) NOT NULL,
  dimensions INTEGER NOT NULL CHECK (dimensions > 0),
  task VARCHAR(40) NOT NULL DEFAULT 'text-matching',
  version VARCHAR(40) NOT NULL,
  active BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS content_embeddings (
  id BIGSERIAL PRIMARY KEY,
  profile_id INTEGER NOT NULL REFERENCES embedding_profiles(id),
  object_type VARCHAR(24) NOT NULL CHECK (object_type IN ('article','entity','event','story')),
  object_id TEXT NOT NULL,
  content_hash CHAR(64) NOT NULL,
  embedding vector,
  status VARCHAR(20) NOT NULL DEFAULT 'ready',
  error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(profile_id, object_type, object_id, content_hash)
);

CREATE TABLE IF NOT EXISTS embedding_jobs (
  id BIGSERIAL PRIMARY KEY,
  profile_id INTEGER NOT NULL REFERENCES embedding_profiles(id),
  object_type VARCHAR(24) NOT NULL,
  object_id TEXT NOT NULL,
  content_hash CHAR(64) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(profile_id, object_type, object_id, content_hash)
);
```

Add GIN and lookup indexes for alias normalization, mention entity/article, graph endpoints, embedding object lookup, and pending jobs.

- [ ] **Step 4: Add migrations 020 and 021 with foreign keys and audit fields**

Use these table contracts in migration 020:

```sql
CREATE TABLE IF NOT EXISTS stories (
  id BIGSERIAL PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  title_ru TEXT NOT NULL,
  title_en TEXT,
  summary TEXT,
  lifecycle VARCHAR(20) NOT NULL CHECK (lifecycle IN ('emerging','developing','escalating','cooling','resolved')),
  first_seen TIMESTAMPTZ NOT NULL,
  last_seen TIMESTAMPTZ NOT NULL,
  article_count INTEGER NOT NULL DEFAULT 0,
  source_count INTEGER NOT NULL DEFAULT 0,
  country_count INTEGER NOT NULL DEFAULT 0,
  highest_action_level INTEGER NOT NULL DEFAULT 1,
  clustering_confidence NUMERIC(4,3) NOT NULL DEFAULT 0,
  summary_model VARCHAR(120),
  source_hash CHAR(64),
  meta JSONB NOT NULL DEFAULT '{}',
  generated_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS story_articles (
  story_id BIGINT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  membership_confidence NUMERIC(4,3) NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}',
  added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(story_id, article_id)
);

CREATE TABLE IF NOT EXISTS story_countries (
  story_id BIGINT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  country_code CHAR(2) NOT NULL REFERENCES countries(code),
  article_count INTEGER NOT NULL DEFAULT 0,
  source_count INTEGER NOT NULL DEFAULT 0,
  media_tone NUMERIC(6,2),
  first_seen TIMESTAMPTZ,
  last_seen TIMESTAMPTZ,
  PRIMARY KEY(story_id, country_code)
);

CREATE TABLE IF NOT EXISTS story_entities (
  story_id BIGINT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  entity_id UUID NOT NULL REFERENCES canonical_entities(id) ON DELETE CASCADE,
  mentions INTEGER NOT NULL DEFAULT 0,
  confidence NUMERIC(4,3) NOT NULL,
  evidence JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY(story_id, entity_id)
);

CREATE TABLE IF NOT EXISTS story_events (
  story_id BIGINT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
  entity_id UUID NOT NULL REFERENCES canonical_entities(id) ON DELETE CASCADE,
  event_key TEXT NOT NULL,
  event_at TIMESTAMPTZ,
  action_level INTEGER NOT NULL DEFAULT 1,
  evidence JSONB NOT NULL DEFAULT '{}',
  PRIMARY KEY(story_id, entity_id)
);
```

Use these table contracts in migration 021:

```sql
CREATE TABLE IF NOT EXISTS signal_evidence (
  id BIGSERIAL PRIMARY KEY,
  signal_id INTEGER UNIQUE NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
  detector VARCHAR(80) NOT NULL,
  detector_version VARCHAR(40) NOT NULL,
  threshold JSONB NOT NULL,
  observed JSONB NOT NULL,
  baseline JSONB NOT NULL,
  window_start TIMESTAMPTZ,
  window_end TIMESTAMPTZ,
  article_ids INTEGER[] NOT NULL DEFAULT '{}',
  story_ids BIGINT[] NOT NULL DEFAULT '{}',
  rri_points JSONB NOT NULL DEFAULT '[]',
  confidence NUMERIC(4,3) NOT NULL,
  completeness VARCHAR(20) NOT NULL CHECK (completeness IN ('complete','partial')),
  explanation JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS index_change_explanations (
  id BIGSERIAL PRIMARY KEY,
  country_code CHAR(2) NOT NULL REFERENCES countries(code),
  from_time TIMESTAMPTZ NOT NULL,
  to_time TIMESTAMPTZ NOT NULL,
  rri_version VARCHAR(16) NOT NULL,
  input_hash CHAR(64) NOT NULL,
  exact_changes JSONB NOT NULL,
  estimated_contributions JSONB NOT NULL DEFAULT '[]',
  context JSONB NOT NULL DEFAULT '[]',
  evidence_completeness VARCHAR(20) NOT NULL,
  limitations JSONB NOT NULL DEFAULT '[]',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(country_code, from_time, to_time, rri_version, input_hash)
);
```

Add indexes for story lifecycle/last seen, story-country lookup, story membership, signal evidence, and explanation cache lookup.

- [ ] **Step 5: Mirror the migrations in `data/init.sql` and add SQLAlchemy models**

Use `UUID(as_uuid=True)`, `JSONB`, `ARRAY(Text)`, and raw SQL-managed vector/search columns. Do not add a fixed-dimension SQLAlchemy vector field to a polymorphic embedding table.

- [ ] **Step 6: Run schema contract tests and migration smoke checks**

Run: `pytest tests/test_schema_contract.py -q`  
Expected: `4 passed`.

Run against a disposable database:

```bash
docker compose up -d db
docker compose exec -T db psql -U thermo -d cis_thermometer -v ON_ERROR_STOP=1 < scripts/migrations/019_search_knowledge.sql
docker compose exec -T db psql -U thermo -d cis_thermometer -v ON_ERROR_STOP=1 < scripts/migrations/020_global_stories.sql
docker compose exec -T db psql -U thermo -d cis_thermometer -v ON_ERROR_STOP=1 < scripts/migrations/021_signal_evidence_explanations.sql
```

Expected: all commands exit `0`; re-running each migration also exits `0`.

- [ ] **Step 7: Commit the schema foundation**

```bash
git add scripts/migrations/019_search_knowledge.sql scripts/migrations/020_global_stories.sql scripts/migrations/021_signal_evidence_explanations.sql data/init.sql src/db.py tests/test_schema_contract.py
git commit -m "feat: add investigation data foundation"
```

---

### Task 2: Deterministic Hybrid Article Search API

**Files:**
- Create: `src/search.py`
- Create: `src/api/routes/search.py`
- Modify: `src/api/main.py`
- Create: `tests/test_search.py`

**Interfaces:**
- Produces: `SearchQuery`, `SearchScore`, `normalize_query()`, `validate_search_query()`, `combine_scores()`, `explain_match()`, and `GET /api/v2/search/articles`.
- Consumes: article search vector, analysis topics/entities, canonical mentions, source metadata, and optional story membership.
- Response: snake_case native fields plus `why_included`, `relevance_score`, `confidence`, `evidence`, and component scores.

- [ ] **Step 1: Write failing ranking and validation tests**

```python
# tests/test_search.py
import pytest
from src.search import combine_scores, normalize_query, validate_search_query


def test_normalize_query_handles_russian_punctuation_and_yo():
    assert normalize_query("  ПУТИН, в Испании!  ") == "путин в испании"


def test_empty_query_requires_a_structured_filter():
    with pytest.raises(ValueError, match="query or filter"):
        validate_search_query("", {})
    validate_search_query("", {"country": "ES"})


def test_exact_entity_match_outranks_body_only_lexical_match():
    entity = combine_scores(lexical=.3, entity=1, topic=0, freshness=.5, trust=.8, story=0)
    body = combine_scores(lexical=.8, entity=0, topic=0, freshness=.5, trust=.8, story=0)
    assert entity.final > body.final


def test_missing_vector_score_does_not_reduce_v1_score():
    score = combine_scores(lexical=.8, entity=.4, topic=.2, freshness=.7, trust=.8, story=.1)
    assert score.vector is None
    assert 0 <= score.final <= 1
```

- [ ] **Step 2: Run tests and verify missing module failure**

Run: `pytest tests/test_search.py -q`  
Expected: collection FAIL with `ModuleNotFoundError: src.search`.

- [ ] **Step 3: Implement pure normalization, validation, scoring, and explanations**

Use exact v1 weights from the spec. Redistribute no vector weight because semantic scoring is inactive. Return a frozen dataclass containing every component and final score. Explanations must name the strongest observed match and never claim semantic similarity.

- [ ] **Step 4: Run pure tests to green**

Run: `pytest tests/test_search.py -q`  
Expected: `4 passed`.

- [ ] **Step 5: Add search endpoint tests with a fake query service**

Test `422` for invalid query, `limit <= 100`, country normalization, stable cursor serialization, HTTP(S)-only URL output, and the Spain/Putin response shape. Inject a service callable into the router so endpoint tests do not require a live database.

- [ ] **Step 6: Implement parameterized SQL candidate retrieval and serialization**

Use `websearch_to_tsquery('simple', :q)` for full text, canonical mention/entity joins for entity filters, array operators for topics, and indexed date/source filters. If full-text candidate count is below 10 and `q` is present, union a trigram title fallback. Fetch at most 500 candidates, calculate deterministic component scores, then return 25 by default with an opaque cursor.

- [ ] **Step 7: Register the focused router and run all Python tests**

Run: `pytest -q`  
Expected: all tests pass.

- [ ] **Step 8: Commit deterministic search**

```bash
git add src/search.py src/api/routes/search.py src/api/main.py tests/test_search.py
git commit -m "feat: add explainable article search"
```

---

### Task 3: Canonical Entity Backfill and Vector-ready Adapters

**Files:**
- Create: `src/knowledge.py`
- Create: `src/embedding_store.py`
- Create: `src/api/routes/entities.py`
- Create: `scripts/backfill_knowledge.py`
- Create: `scripts/index_embeddings.py`
- Modify: `src/api/main.py`
- Modify: `Dockerfile.analyzer`
- Create: `tests/test_knowledge.py`
- Create: `tests/test_embedding_store.py`

**Interfaces:**
- Produces: stable node IDs, canonical alias resolution, evidence-bearing mentions, entity suggest/detail APIs, `EmbeddingProvider` protocol, content hashing, and idempotent job claims.
- Consumes: current `src/entities.py` registry and legacy `analysis.entities` keys.
- Preserves: legacy JSON entity reads.

- [ ] **Step 1: Write failing canonicalization tests**

Test normalization of `ё`, punctuation, whitespace, alias ambiguity, stable `kind:<uuid>` node IDs, and conversion of a current registry key into a canonical mention with extractor version and evidence.

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_knowledge.py -q`  
Expected: FAIL because `src.knowledge` does not exist.

- [ ] **Step 3: Implement canonical domain types and legacy backfill service**

Use dataclasses for `CanonicalEntity`, `EntityAlias`, and `EntityMention`. `resolve_alias()` returns no automatic match when more than one non-disambiguated entity shares the normalized alias. Backfill batches are ordered by analysis ID and upserted transactionally.

- [ ] **Step 4: Add failing embedding adapter tests**

Test SHA-256 content hashing, provider metadata, document/query method separation, idempotent job keys, and fallback response `semantic_search="unavailable"` when there is no active profile.

- [ ] **Step 5: Implement provider-neutral embedding store**

Define:

```python
class EmbeddingProvider(Protocol):
    def profile(self) -> EmbeddingProfile:
        raise NotImplementedError

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError
```

Wrap the existing Jina/OpenAI helper behind this protocol. Do not activate vector ranking and do not generate embeddings from a GET request.

- [ ] **Step 6: Add entity suggest and detail endpoints**

`GET /api/v2/entities/suggest?q=` returns canonical ID, kind, label, aliases, and match explanation. `GET /api/v2/entities/{id}` returns the canonical record and evidence-bearing recent mentions. Both use pagination and safe URLs.

- [ ] **Step 7: Add one-off backfill and indexing commands**

`backfill_knowledge.py` migrates registry keys and mentions. `index_embeddings.py` claims only pending jobs for an active profile, records usage/errors, and can be run with `--dry-run`; it is not scheduled in compose in this package.

- [ ] **Step 8: Run tests and commit**

Run: `pytest tests/test_knowledge.py tests/test_embedding_store.py -q`  
Expected: all tests pass.

```bash
git add src/knowledge.py src/embedding_store.py src/api/routes/entities.py src/api/main.py scripts/backfill_knowledge.py scripts/index_embeddings.py Dockerfile.analyzer tests/test_knowledge.py tests/test_embedding_store.py
git commit -m "feat: add canonical knowledge foundation"
```

---

### Task 4: Cross-country Story Builder and APIs

**Files:**
- Create: `src/stories.py`
- Create: `src/api/routes/stories.py`
- Modify: `src/api/main.py`
- Modify: `scripts/build_threads.py`
- Create: `tests/test_stories.py`

**Interfaces:**
- Produces: `StoryCandidate`, `StorySimilarity`, `score_story_match()`, `should_merge()`, lifecycle transitions, background persistence, and story list/detail/country APIs.
- Consumes: existing country threads, analyzed articles, canonical mentions, topics, and signals.
- Preserves: existing `/api/v1/countries/{code}/threads`.

- [ ] **Step 1: Write failing story similarity tests**

Cover: two countries with matching event key plus entity overlap merge; title-only similarity does not merge; score `0.64` does not merge; score `0.65` merges with two independent features; gaps over 14 days do not merge unless reactivation conditions hold; deterministic lifecycle transitions.

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_stories.py -q`  
Expected: FAIL because `src.stories` does not exist.

- [ ] **Step 3: Implement pure story scoring and lifecycle logic**

Return individual normalized components and evidence, not only a total. Use event-key trigram similarity, entity/topic Jaccard overlap, time proximity, source/country diversity, and title fallback. Enforce two-feature and time-window gates after scoring.

- [ ] **Step 4: Implement idempotent builder persistence**

Build candidates from existing threads and their articles. Upsert story membership by article ID, recompute counts, country slices, entities, lifecycle, and source hash. Call an LLM only from the background cycle and only when the source hash changes; deterministic copy is always available.

- [ ] **Step 5: Integrate story building into the existing thread worker**

After a successful country-thread cycle, call the global story builder. Failure logs and metrics must not terminate existing thread building. Do not add a new compose service.

- [ ] **Step 6: Add API tests and routers**

Test filters, cursor pagination, story detail evidence, country slices, primary URLs, and `404`. Add:

- `GET /api/v2/stories`
- `GET /api/v2/stories/{story_id}`
- `GET /api/v2/countries/{code}/stories`

- [ ] **Step 7: Run full Python tests and commit**

Run: `pytest -q`  
Expected: all tests pass.

```bash
git add src/stories.py src/api/routes/stories.py src/api/main.py scripts/build_threads.py tests/test_stories.py
git commit -m "feat: add cross-country stories"
```

---

### Task 5: Evidence-rich Signal Detail

**Files:**
- Modify: `src/engine/signals.py`
- Create: `src/api/routes/signal_detail.py`
- Modify: `src/api/main.py`
- Create: `tests/test_signal_evidence.py`

**Interfaces:**
- Produces: `SignalEvidence` domain record and `GET /api/v2/signals/{signal_id}`.
- Consumes: detector inputs at signal creation, related story membership, articles, and RRI points.
- Preserves: existing signal list endpoint and payload fields.

- [ ] **Step 1: Write failing evidence contract tests**

Each new detector result must include detector/version, threshold, observed value, baseline, window, confidence, evidence completeness, and stable evidence IDs. Test old-signal fallback returns `partial` and never invents threshold inputs.

- [ ] **Step 2: Verify red**

Run: `pytest tests/test_signal_evidence.py -q`  
Expected: contract assertions fail on current detector output.

- [ ] **Step 3: Implement evidence capture beside every detector**

Build evidence from the values already loaded by the detector. Persist it in the same transaction as a newly inserted signal. Deduplicated existing signals keep their original trigger evidence.

- [ ] **Step 4: Implement detail serialization and route**

Return concrete summary, rule/version, observed and baseline values, chart points, supporting articles, related story, countries, creation/expiration state, confidence, and limitations. Validate all URLs.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_signal_evidence.py -q`  
Expected: all tests pass.

```bash
git add src/engine/signals.py src/api/routes/signal_detail.py src/api/main.py tests/test_signal_evidence.py
git commit -m "feat: persist signal evidence"
```

---

### Task 6: RRI Shift Explanation and Thermometer Methodology APIs

**Files:**
- Create: `src/engine/explanations.py`
- Create: `src/methodology.py`
- Create: `src/api/routes/investigations.py`
- Modify: `src/api/main.py`
- Modify: `src/engine/index.py`
- Create: `tests/test_explanations.py`
- Create: `tests/test_methodology.py`

**Interfaces:**
- Produces: exact RRI delta decomposition, estimated counterfactual event contribution, contextual evidence, cached explanation API, and machine-readable Thermometer methodology.
- Endpoints: `GET /api/v2/countries/{code}/index-explanation` and `GET /api/v2/methodology/temperature`.
- Preserves: current calculation output.

- [ ] **Step 1: Write failing exact decomposition tests**

Given two stored RRI points, assert exact total, structural, media, and boost deltas. Assert rounding residual is separate. Assert context items never enter the exact subtotal.

- [ ] **Step 2: Write failing counterfactual labelling tests**

Given a removable event cluster, assert the difference is returned as `estimated`, includes method and input IDs, and is omitted with a concrete reason when article inputs are missing.

- [ ] **Step 3: Verify red and implement explanation domain logic**

Run: `pytest tests/test_explanations.py -q` before implementation and confirm missing-module failure. Implement pure functions first, then the database loader/cache keyed by country, from/to timestamps, RRI version, and input hash.

- [ ] **Step 4: Extract Thermometer constants into a shared methodology definition**

Move the current window, time decay, source weights, event weights, action-level weights, cluster diminishing, normalization, anomaly, and trend metadata into immutable definitions imported by the existing engine. Do not change numeric values.

- [ ] **Step 5: Test methodology matches the engine**

Assert the API definition and engine use the same objects/constants, the window is 14 days, every coefficient category is present, and a worked example can be reproduced from returned values.

- [ ] **Step 6: Implement read-only routers with no generation side effects**

The explanation endpoint accepts `at` plus `window_hours`, or explicit `from`/`to`. It returns `exact_changes`, `estimated_contributions`, `context`, related story/signal IDs, evidence completeness, and limitations. The methodology endpoint returns plain-language sections and exact technical constants.

- [ ] **Step 7: Run tests and commit**

Run: `pytest tests/test_explanations.py tests/test_methodology.py -q`  
Expected: all tests pass and existing engine tests remain unchanged.

```bash
git add src/engine/explanations.py src/methodology.py src/api/routes/investigations.py src/api/main.py src/engine/index.py tests/test_explanations.py tests/test_methodology.py
git commit -m "feat: explain index shifts and thermometer"
```

---

### Task 7: Frontend Test Foundation, Navigation, and Search Page

**Files:**
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Create: `web/vitest.config.ts`
- Create: `web/test/setup.ts`
- Modify: `web/components/SiteHeader.tsx`
- Modify: `web/lib/api.ts`
- Modify: `web/lib/types.ts`
- Create: `web/app/search/page.tsx`
- Create: `web/components/SearchResults.tsx`
- Create: `web/components/SearchResults.test.tsx`

**Interfaces:**
- Produces: `/search` UI and typed API methods for search/entity suggestions.
- Consumes: Task 2 and Task 3 endpoints.

- [ ] **Step 1: Add Vitest and Testing Library configuration**

Add scripts `test` and `test:watch`, jsdom environment, `@testing-library/jest-dom`, React plugin, and `@/` alias. Install exact compatible current versions through npm and commit the resulting lockfile.

- [ ] **Step 2: Write a failing SearchResults component test**

Render a Spain/Putin result. Assert visible title, Spain/source/date metadata, highlighted evidence, why-included disclosure, story link, and safe primary URL. Assert a `javascript:` URL is not rendered as a link.

- [ ] **Step 3: Verify red**

Run: `cd web && npm test -- SearchResults.test.tsx --run`  
Expected: FAIL because `SearchResults` does not exist.

- [ ] **Step 4: Implement typed result component and page state**

The page reads/writes `q`, `country`, `topic`, `entity_id`, `from`, `to`, `tier`, `language`, and `sort` in URL search params. It debounces entity suggestions, submits article queries, handles loading/error/empty states, and appends cursor pages without reordering existing results.

- [ ] **Step 5: Add the header destinations**

Add labelled «Поиск новостей» and «Сюжеты» destinations. Search activation opens `/search` and the search page focuses its input. Preserve existing responsive navigation and active states.

- [ ] **Step 6: Run frontend tests, typecheck, and build**

Run:

```bash
cd web
npm test -- --run
npx tsc --noEmit
npm run build
```

Expected: all commands exit `0`.

- [ ] **Step 7: Commit frontend search**

```bash
git add web/package.json web/package-lock.json web/vitest.config.ts web/test/setup.ts web/components/SiteHeader.tsx web/lib/api.ts web/lib/types.ts web/app/search/page.tsx web/components/SearchResults.tsx web/components/SearchResults.test.tsx
git commit -m "feat: add news search experience"
```

---

### Task 8: Stories Pages and Story Placements

**Files:**
- Create: `web/app/stories/page.tsx`
- Create: `web/app/stories/[id]/page.tsx`
- Create: `web/components/StoryCard.tsx`
- Create: `web/components/StoryTimeline.tsx`
- Create: `web/components/StoriesPanel.tsx`
- Create: `web/components/StoryCard.test.tsx`
- Modify: `web/app/page.tsx`
- Modify: `web/app/country/[code]/page.tsx`
- Modify: `web/lib/api.ts`
- Modify: `web/lib/types.ts`

**Interfaces:**
- Produces: global story listing, detail, home placement, and country placement.
- Consumes: Task 4 APIs.

- [ ] **Step 1: Write failing story card and timeline tests**

Assert lifecycle label, countries, counts, last update, related signal/RRI shift, evidence explanation, primary URLs, and the same story ID in global and country-slice props.

- [ ] **Step 2: Verify red and implement focused components**

Run: `cd web && npm test -- StoryCard.test.tsx --run` and confirm missing-component failure. Implement compact cards matching current card/grid tokens and a chronological timeline with text alternative.

- [ ] **Step 3: Implement `/stories` filters and durable URLs**

Support country, topic, lifecycle, entity, and period. Active stories sort by lifecycle priority, activity, then last seen. Resolved stories remain searchable.

- [ ] **Step 4: Implement story detail**

Render summary, timeline, countries and media-tone differences, entities, linked signals and RRI shifts, clustering explanation, limitations, and cursor-paginated article sources.

- [ ] **Step 5: Add visible home and country story panels**

Home shows 5–7 stories. Every country page renders a `StoriesPanel`, including an explicit empty state rather than silently hiding the feature.

- [ ] **Step 6: Verify and commit**

Run: `cd web && npm test -- --run && npx tsc --noEmit && npm run build`  
Expected: all commands exit `0`.

```bash
git add web/app/stories web/components/StoryCard.tsx web/components/StoryTimeline.tsx web/components/StoriesPanel.tsx web/components/StoryCard.test.tsx web/app/page.tsx web/app/country/[code]/page.tsx web/lib/api.ts web/lib/types.ts
git commit -m "feat: surface cross-country stories"
```

---

### Task 9: Unified Investigation, Signal Detail, and Methodology UI

**Files:**
- Create: `web/components/InvestigationPanel.tsx`
- Create: `web/components/InvestigationPanel.test.tsx`
- Create: `web/app/signals/[id]/page.tsx`
- Create: `web/components/SignalEvidence.tsx`
- Modify: `web/components/SignalFeed.tsx`
- Modify: `web/components/SparklineStrip.tsx`
- Modify: `web/app/country/[code]/page.tsx`
- Modify: `web/app/about/page.tsx`
- Modify: `web/lib/api.ts`
- Modify: `web/lib/types.ts`

**Interfaces:**
- Produces: chart-to-investigation flow, durable signal details, concrete home signal cards, and two-level methodology content.
- Consumes: Tasks 5 and 6 APIs.

- [ ] **Step 1: Write failing investigation separation tests**

Assert «Что изменило расчёт» contains exact values, estimated event contributions are visibly labelled, «Что происходило рядом» is separate, limitations render, and article links are safe.

- [ ] **Step 2: Verify red and implement responsive investigation panel**

Use a side panel at desktop widths and accessible bottom sheet on narrow screens. Manage focus, Escape close, keyboard chart-marker activation, reduced motion, and URL `at` state.

- [ ] **Step 3: Wire chart points and meaningful-shift markers**

Markers use existing RRI history deltas. Clicking loads the explanation endpoint. The chart retains a textual list of marked shifts for keyboard and screen-reader access.

- [ ] **Step 4: Implement concrete signal cards and `/signals/[id]`**

Card headline uses the human explanation, while detector type is secondary. Detail renders observed/baseline/threshold, chart window, evidence, story, countries, primary URLs, limitations, and active/expired state. Old partial signals carry a visible badge.

- [ ] **Step 5: Implement the About methodology layers**

Render the backend plain-language sections immediately. Put exact formula constants, worked example, versions, and limitations in an accessible disclosure. Do not duplicate numeric constants in TypeScript.

- [ ] **Step 6: Run UI verification and commit**

Run: `cd web && npm test -- --run && npx tsc --noEmit && npm run build`  
Expected: all commands exit `0`.

```bash
git add web/components/InvestigationPanel.tsx web/components/InvestigationPanel.test.tsx web/app/signals/[id]/page.tsx web/components/SignalEvidence.tsx web/components/SignalFeed.tsx web/components/SparklineStrip.tsx web/app/country/[code]/page.tsx web/app/about/page.tsx web/lib/api.ts web/lib/types.ts
git commit -m "feat: connect signals shifts and methodology"
```

---

### Task 10: Integration, Backfill Safety, Documentation, and Release Gate

**Files:**
- Create: `tests/test_investigation_flows.py`
- Create: `scripts/backfill_investigation_data.py`
- Create: `docs/release/investigation-search-stories.md`
- Modify: `README.md`
- Modify: `deploy/README.md`
- Modify: `deploy/.env.example`

**Interfaces:**
- Produces: one dry-run/apply backfill command, release metrics checklist, rollback instructions, and end-to-end flow coverage.
- Consumes: all earlier tasks.

- [ ] **Step 1: Write failing integration scenarios**

Cover the acceptance flows: Putin+Spain source result, cross-country story and country slices, signal evidence detail, chart explanation separation, methodology constants, provider-unavailable search, and no side-effecting GET calls.

- [ ] **Step 2: Implement safe orchestration backfill**

The command runs knowledge mentions, story membership, signal evidence where reconstructable, and explanation warmup in bounded batches. `--dry-run` is default, `--apply` is explicit, each stage records a cursor, and reruns are idempotent.

- [ ] **Step 3: Document feature flags and operations**

Define server-side flags for search navigation, stories navigation, investigation, and signal detail. Document backfill order, metrics, query-plan checks, rollback by disabling flags/workers, and the fact that additive tables remain.

- [ ] **Step 4: Run fresh verification**

Run:

```bash
pytest -q
python -m compileall -q src scripts
cd web && npm test -- --run && npx tsc --noEmit && npm run build
```

Then run migration scripts twice against the disposable database and the backfill in dry-run mode. Expected: all commands exit `0`, no public GET emits an LLM/embedding API call, and git diff has no generated artifacts.

- [ ] **Step 5: Perform code review and resolve findings**

Use `superpowers:requesting-code-review`. Address every valid high/medium issue through a failing regression test before changing production code.

- [ ] **Step 6: Commit release readiness changes**

```bash
git add tests/test_investigation_flows.py scripts/backfill_investigation_data.py docs/release/investigation-search-stories.md README.md deploy/README.md deploy/.env.example
git commit -m "docs: add investigation release gate"
```

- [ ] **Step 7: Final branch verification**

Run `git status --short --branch`, `git log --oneline origin/main..HEAD`, and the full verification suite again. Do not push or deploy until explicitly requested.
