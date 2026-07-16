# Story Pipeline Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Follow superpowers:test-driven-development for every code change.

**Goal:** Restore a useful number of cross-country stories without losing production data by rebuilding canonical entities, adding provider-neutral OpenRouter embeddings, and correcting story candidate boundaries.

**Architecture:** New analysis dual-writes canonical entity mentions while an idempotent checkpointed backfill repairs historical rows. A provider-neutral embedding queue calls OpenRouter through the existing Finland HTTPS proxy, stores vectors in `content_embeddings`, and projects compatible 1536-dimensional article vectors into the legacy `analysis.embedding` column used by thread clustering. Story candidates use the thread's canonical country and reject same-thread pairings before complete-link clustering.

**Tech Stack:** Python 3.12, SQLAlchemy, PostgreSQL/pgvector, pytest, OpenRouter embeddings API, Docker Compose.

## Global Constraints

- Never delete or truncate articles, analysis, temperature history, threads, signals, briefs, or existing stories during recovery.
- Historical backfills must be idempotent, checkpointed, resumable, and selectable by stage.
- Use OpenRouter model `openai/text-embedding-3-small` with 1536 dimensions through the existing proxy-enabled worker containers.
- Keep provider-neutral vectors in `content_embeddings`; project only compatible 1536-dimensional article vectors to `analysis.embedding`.
- Keep story threshold `0.65`, require at least two independent semantic features, and keep complete-link cluster validation.
- A story candidate belongs to exactly one country: `threads.country_code`; defensive code must reject pairs with the same `thread_id`.
- Canary 500 articles before the remaining relevant 30-day window.
- Deploy code once; audit proposed story counts before applying the 30-day thread/story rebuild.

---

### Task 1: Dual-write and backfill canonical entity mentions

**Files:**
- Modify: `src/knowledge.py`
- Modify: `scripts/analyze.py`
- Modify: `scripts/backfill_investigation_data.py`
- Test: `tests/test_knowledge.py`
- Test: `tests/test_investigation_flows.py`

**Interfaces:**
- Produces an idempotent `upsert_analysis_mentions(session, analysis_id, article_id, entities)` helper using the existing canonical registry and mention tables.
- Adds CLI selection `--stage knowledge_mentions` without changing the existing stage implementation or checkpoints.

- [ ] **Step 1: Write failing tests**

Cover repeated mention upserts, normalization through the canonical registry, analysis live-write after the analysis row has an ID, and CLI stage selection that calls only `knowledge_mentions`.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_knowledge.py tests/test_investigation_flows.py -q`

Expected: failures for the missing helper/live-write and missing `--stage` option.

- [ ] **Step 3: Implement idempotent live writes**

Reuse the existing `KnowledgeBackfillService` normalization and upsert path rather than duplicating entity parsing. Flush a saved analysis row, then write canonical mentions in the same database transaction. Log a structured error with `analysis_id` and `article_id`; do not silently discard failures.

- [ ] **Step 4: Add safe stage selection**

Allow repeated `--stage` flags with choices from the existing stage map. Preserve the current all-stage behavior only when no flag is supplied. A dry run or checkpoint resume must still work for the selected stage.

- [ ] **Step 5: Verify GREEN and commit**

Run: `.venv/bin/python -m pytest tests/test_knowledge.py tests/test_investigation_flows.py -q`

Commit: `feat: maintain canonical entity mentions`

---

### Task 2: Correct story candidate country boundaries

**Files:**
- Modify: `src/stories.py`
- Test: `tests/test_stories.py`

**Interfaces:**
- `fetch_story_candidates` returns one candidate per thread using `threads.country_code` and matching canonical country facts.
- Pair generation rejects equal `thread_id` values before scoring.

- [ ] **Step 1: Write failing boundary tests**

Add tests proving one thread cannot appear under two article-fact countries, same-thread candidates never form a story, and candidates more than 14 days apart are skipped before expensive scoring while overlapping/nearby candidates still use the exact existing scorer.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_stories.py -q`

- [ ] **Step 3: Implement the canonical-country query and prefilters**

Select trimmed `threads.country_code`; join or filter `article_country_facts.country_code = threads.country_code`; aggregate only canonical members. Add a defensive same-thread rejection and a safe 14-day activity-window prefilter. Do not modify score weights, threshold, two-feature rule, or complete-link validation.

- [ ] **Step 4: Verify GREEN and commit**

Run: `.venv/bin/python -m pytest tests/test_stories.py -q`

Commit: `fix: enforce canonical story candidate countries`

---

### Task 3: Add OpenRouter as an embedding provider

**Files:**
- Modify: `src/embeddings.py`
- Modify: `src/embedding_store.py`
- Create: `tests/test_embeddings.py`
- Test: `tests/test_embedding_store.py`

**Interfaces:**
- Environment fallback uses `OPENROUTER_API_KEY`, URL `https://openrouter.ai/api/v1/embeddings`, model env/default `openai/text-embedding-3-small`, and dimension env/default `1536`.
- Tracking and profiles label the provider `openrouter`.

- [ ] **Step 1: Write failing configuration and request tests**

Cover provider priority, exact URL/model/dimensions, bearer authentication, response parsing, tracking service name, and `LegacyEmbeddingProvider.from_environment()` provider label.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_embeddings.py tests/test_embedding_store.py -q`

- [ ] **Step 3: Implement the provider fallback**

Keep existing explicit proxy/Jina/OpenAI precedence. Use OpenRouter only when its key is present and no higher-priority embedding configuration is selected. Validate vector dimensions before storage and surface provider failures to job status.

- [ ] **Step 4: Verify GREEN and commit**

Run: `.venv/bin/python -m pytest tests/test_embeddings.py tests/test_embedding_store.py -q`

Commit: `feat: support OpenRouter article embeddings`

---

### Task 4: Prepare jobs and project compatible vectors to legacy analysis

**Files:**
- Create: `scripts/prepare_embedding_jobs.py`
- Modify: `scripts/index_embeddings.py`
- Modify: `src/embedding_store.py`
- Test: `tests/test_embedding_store.py`

**Interfaces:**
- CLI supports `--days 30`, `--limit 500`, and `--dry-run`.
- Enqueues relevant, non-duplicate article content idempotently by content hash and active profile.
- Successful 1536-dimensional article vectors remain in `content_embeddings` and update matching `analysis.embedding` rows.

- [ ] **Step 1: Write failing job/prejection tests**

Cover dry-run non-mutation, repeat preparation without duplicate ready jobs, active-profile creation/reuse, exclusion of irrelevant/duplicate articles, and legacy projection only for article vectors with exactly 1536 values.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_embedding_store.py -q`

- [ ] **Step 3: Implement preparation and compatibility projection**

Use the existing `prepare_embedding_text`, profile, enqueue/claim, and record-success abstractions. Add the smallest profile helper needed. Project in the same successful-job transaction so `content_embeddings` is authoritative and retries remain idempotent.

- [ ] **Step 4: Verify GREEN and commit**

Run: `.venv/bin/python -m pytest tests/test_embedding_store.py -q`

Commit: `feat: queue and project article embeddings`

---

### Task 5: Add a bounded story recovery audit and rebuild

**Files:**
- Modify: `scripts/build_threads.py`
- Create: `scripts/audit_story_pipeline.py`
- Test: `tests/test_stories.py`

**Interfaces:**
- `build_threads.py --recent-days 30` invokes the existing bounded recent rebuild path.
- Audit is read-only and reports candidate totals, canonical-entity coverage, embedding coverage, pair rejection reasons, proposed clusters, same-thread defects, and country mismatches.

- [ ] **Step 1: Write failing CLI/audit tests**

Assert recent-days routing is bounded and audit mode performs no commit/write. Include stable JSON keys so production can be checked before and after rebuild.

- [ ] **Step 2: Verify RED**

Run: `.venv/bin/python -m pytest tests/test_stories.py -q`

- [ ] **Step 3: Implement the thin CLI and read-only report**

Reuse existing story candidate/scoring functions. Do not introduce a second clustering algorithm. The audit may explain rejection categories but must report the same proposed clusters as the production builder for the same inputs.

- [ ] **Step 4: Verify GREEN and commit**

Run: `.venv/bin/python -m pytest tests/test_stories.py -q`

Commit: `feat: audit bounded story recovery`

---

### Task 6: Canary, recover, and verify production

**Files:**
- No repository file changes.
- Backups: `/opt/geopulse/backups/pre-story-recovery-<timestamp>.dump`

- [ ] **Step 1: Verify the full release locally**

Run: `.venv/bin/python -m pytest -q`

Run: `cd web && npm test -- --run && npm run build`

Expected: zero failures and a successful production build.

- [ ] **Step 2: Back up and record protected invariants**

Record Git HEAD and counts/checksums for articles, analysis, temperature history, threads, signals, briefs, stories, canonical entities/mentions, embedding jobs/vectors. Create a custom-format database dump and validate it with `pg_restore --list`.

- [ ] **Step 3: Deploy the combined code once**

Push one reviewed release commit. Monitor `api`, `web`, `analyzer`, `threads`, and `briefs`; verify `/`, `/api/v2/health`, and core data endpoints before mutation.

- [ ] **Step 4: Repair canonical mentions only**

Dry-run `knowledge_mentions`, then run the selected checkpointed stage. Verify canonical counts increased and every protected pre-release count stayed equal or increased.

- [ ] **Step 5: Run a 500-article embedding canary**

Verify one OpenRouter embedding from a proxy-enabled worker, prepare at most 500 recent jobs, index them, and require at least 95% `ready` or an explicit explained terminal state. Confirm 1536-dimensional results exist in both provider-neutral storage and compatible analysis rows.

- [ ] **Step 6: Audit before rebuilding**

Run the read-only 30-day audit. Stop before mutation if it reports same-thread candidates, country mismatches, destructive count changes, or implausibly broad clusters.

- [ ] **Step 7: Recover the recent window**

Prepare/index the remaining relevant 30-day articles, then run the bounded 30-day thread/story rebuild. Do not rebuild or delete older history.

- [ ] **Step 8: Verify product and data invariants**

Require more than one current story, zero same-thread pairs, zero cross-country mismatches, working story list/detail APIs, and no decrease in protected counts. Confirm thematic briefs and all core services remain healthy after the single release.
