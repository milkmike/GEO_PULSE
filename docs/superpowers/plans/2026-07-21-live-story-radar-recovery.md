# Live Story and Radar Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore continuous story-candidate embeddings, conservative hybrid cross-country story formation, and hourly persisted Radar cycles.

**Architecture:** A bounded embedding worker reuses the existing idempotent job store and active provider profile. Story clustering keeps the exact-event route and adds a corroborated semantic route that requires shared canonical entities and topics at both thread and article level. Radar gains a lock-protected loop while retaining shadow as the safe manual default.

**Tech Stack:** Python 3.12, SQLAlchemy 2, PostgreSQL/pgvector, OpenRouter embeddings, Docker Compose, pytest.

## Global Constraints

- Preserve every existing article, analysis, temperature, signal, brief, thread, story, and radar history row.
- Do not lower the concrete event-key threshold or remove the fourteen-day time gate.
- Semantic story admission requires similarity `>= 0.86`, a shared canonical entity, and a shared normalized topic.
- Embedding work is bounded, idempotent, and uses the one active 1,024-dimensional profile.
- Manual Radar invocation remains shadow unless `--apply` is explicit.
- Production Radar runs hourly with a PostgreSQL advisory transaction lock.
- Initial production story recovery uses the thirty-day non-destructive scoped builder.

---

### Task 1: Add the continuous embedding worker

**Files:**

- Create: `scripts/run_embedding_worker.py`
- Create: `tests/test_embedding_worker.py`
- Modify: `Dockerfile.analyzer`
- Modify: `docker-compose.yml`

**Interfaces:**

- Produces `run_embedding_cycle(*, days: int, prepare_limit: int, batch_size: int, index_limit: int, prepare, index) -> dict[str, object]`.
- Produces CLI flags `--loop`, `--interval 300`, `--days 30`, `--prepare-limit 500`, `--batch 50`, and `--index-limit 500`.
- Compose service `embedding-worker` uses `Dockerfile.analyzer` and the existing database, proxy, OpenRouter, and embedding-dimension environment.

- [ ] **Step 1: Write failing worker and Compose tests**

Add tests that inject real callable fakes and require the preparation stage to run before indexing, the returned report to contain both stage reports, invalid bounds to raise, loop mode to sleep only after a completed cycle, the analyzer image to contain the new entry point, and Compose to define a restarting five-minute worker.

```python
def test_embedding_cycle_prepares_before_indexing():
    calls = []
    report = run_embedding_cycle(
        days=30, prepare_limit=500, batch_size=50, index_limit=500,
        prepare=lambda **kwargs: calls.append(("prepare", kwargs)) or {"enqueued": 7},
        index=lambda **kwargs: calls.append(("index", kwargs)) or {"indexed": 7},
    )
    assert [name for name, _ in calls] == ["prepare", "index"]
    assert report == {"prepare": {"enqueued": 7}, "index": {"indexed": 7}}
```

- [ ] **Step 2: Verify RED**

Run: `/private/tmp/geopulse-story-radar-venv/bin/python -m pytest tests/test_embedding_worker.py -q`

Expected: collection failure because `scripts.run_embedding_worker` does not exist.

- [ ] **Step 3: Implement the smallest bounded worker**

Call `prepare_jobs(story_candidates=True, ...)`, then `index_pending(...)`. Validate every numeric bound as positive, log one JSON report per cycle, catch cycle failures only in loop mode, and sleep for the remaining interval measured from cycle start. Do not create another queue or embedding provider.

- [ ] **Step 4: Verify GREEN and commit**

Run: `/private/tmp/geopulse-story-radar-venv/bin/python -m pytest tests/test_embedding_worker.py tests/test_embedding_store.py -q`

Commit: `feat: run continuous story embeddings`

---

### Task 2: Add corroborated semantic story admission

**Files:**

- Modify: `src/stories.py`
- Modify: `tests/test_stories.py`

**Interfaces:**

- Produces `story_confirmation_routes(similarity: StorySimilarity) -> frozenset[str]` with values `event_key` and `semantic`.
- The semantic route requires `components["semantic"] >= 0.86`, non-empty `evidence["shared_entities"]`, non-empty `evidence["shared_topics"]`, cross-country evidence, and the existing time gate.
- `_filter_story_cluster_articles` accepts an article pair only through the concrete event route or through thread semantic confirmation plus article-level shared entity, shared topic, and time evidence.

- [ ] **Step 1: Write failing semantic-route tests**

Cover a cross-country pair with different concrete wording, semantic score `0.91`, one shared canonical entity, one shared topic, and a one-day gap. Assert it merges and reports `semantic`. Split separate tests that remove the entity, topic, semantic score, country diversity, or valid time and assert rejection. Preserve an existing exact-event regression proving `event_key` still works without embeddings.

```python
def test_semantic_route_requires_entity_and_topic_corroboration():
    similarity = score_story_match(
        _candidate("AM", 1, entities={"actor"}, topics={"sanctions"}, semantic=(2, .91)),
        _candidate("GE", 2, entities={"actor"}, topics={"sanctions"}, semantic=(1, .91)),
    )
    assert story_confirmation_routes(similarity) == frozenset({"semantic"})
    assert should_merge(similarity)
```

- [ ] **Step 2: Verify RED**

Run: `/private/tmp/geopulse-story-radar-venv/bin/python -m pytest tests/test_stories.py -q`

Expected: failure because semantic confirmation cannot currently pass the concrete-event gate.

- [ ] **Step 3: Implement pair and article evidence routes**

Keep current event-key scoring unchanged. Add the semantic route as an alternative inside `merge_rejection_reasons`, reuse the same route in `_filter_story_cluster_articles`, and store the selected confirmation route with shared entities, shared topics, components, and article IDs in membership evidence. Do not admit semantic-only article pairs without article-level corroboration.

- [ ] **Step 4: Verify GREEN and commit**

Run: `/private/tmp/geopulse-story-radar-venv/bin/python -m pytest tests/test_stories.py -q`

Commit: `feat: corroborate semantic cross-country stories`

---

### Task 3: Run persisted Radar cycles hourly

**Files:**

- Modify: `scripts/build_radar.py`
- Modify: `docker-compose.yml`
- Modify: `tests/test_build_radar.py`
- Modify: `tests/test_radar_wave1_audit.py`

**Interfaces:**

- CLI adds `--loop` and positive `--interval`, rejecting a fixed `--as-of` in loop mode.
- `run_once(args, *, now_factory, session_factory, cycle_runner) -> dict[str, object]` uses a fresh UTC time when `--as-of` is absent.
- Apply cycles acquire `pg_try_advisory_xact_lock(hashtext('geopulse-radar-worker'))`; a missed lock returns a skipped report without writes.
- Production Compose runs `python scripts/build_radar.py --apply --days 90 --loop --interval 3600` with `restart: unless-stopped` and no opt-in profile.

- [ ] **Step 1: Write failing loop, lock, and Compose tests**

Test parser validation, two loop iterations receiving different `as_of` values, shadow cycles never committing, apply cycles committing once, missed locks skipping the detector, and the exact production Compose command.

```python
def test_loop_uses_fresh_as_of_for_every_cycle():
    seen = []
    run_loop(_args(), run_once=lambda _args: seen.append(clock.pop(0)), sleep=lambda _: None, max_cycles=2)
    assert seen == [FIRST_HOUR, SECOND_HOUR]
```

- [ ] **Step 2: Verify RED**

Run: `/private/tmp/geopulse-story-radar-venv/bin/python -m pytest tests/test_build_radar.py tests/test_radar_wave1_audit.py -q`

Expected: parser and Compose assertions fail because Radar is one-shot shadow-only.

- [ ] **Step 3: Implement the lock-protected loop**

Refactor the existing body into one-cycle and loop functions without changing `run_radar_cycle`. Acquire the advisory transaction lock only for apply mode, commit only a completed apply cycle, roll back on errors through the session context, and retain JSON output for one-shot audits.

- [ ] **Step 4: Verify GREEN and commit**

Run: `/private/tmp/geopulse-story-radar-venv/bin/python -m pytest tests/test_build_radar.py tests/test_radar_wave1_audit.py -q`

Commit: `feat(radar): schedule persisted hourly cycles`

---

### Task 4: Verify and deploy the recovery

**Files:**

- Modify only if verification finds a defect: files from Tasks 1–3 and their tests.
- Production backup: `/opt/geopulse/backups/pre-live-story-radar-recovery-<UTC timestamp>.dump`.

**Interfaces:**

- Release must preserve protected row counts and serve the public product routes.

- [ ] **Step 1: Run local release verification**

Run: `/private/tmp/geopulse-story-radar-venv/bin/python -m pytest -q`

Run: `docker compose config`

Expected: zero test failures and valid Compose output.

- [ ] **Step 2: Review the branch diff and commit the release metadata**

Check `git diff main...HEAD`, confirm no secret or destructive SQL is present, and record the verified commit SHA.

- [ ] **Step 3: Capture production invariants and backup PostgreSQL**

Record counts and maxima for articles, analysis, temperature, signals, briefs, threads, stories, story memberships, embedding jobs/vectors, and Radar tables. Create a custom-format dump and verify it with `pg_restore --list` before changing services.

- [ ] **Step 4: Deploy once and start embeddings first**

Push the verified branch to `main`, update `/opt/geopulse`, build the changed analyzer/threads/Radar images, run migrations, and start `embedding-worker`. Do not restart PostgreSQL or Redis.

- [ ] **Step 5: Run the bounded story canary**

Require a matching active 1,024-dimensional profile, recent ready embeddings, and no uncontrolled job failure growth. Run `build_threads.py --stories-only-recent-days 30`, then inspect every newly created story for country diversity, evidence routes, and article relevance before allowing the hourly global builder to continue.

- [ ] **Step 6: Start Radar and verify freshness**

Start the hourly apply worker and require current timestamps in observations, trends, evidence, and state events. Verify only one cycle owns the advisory lock.

- [ ] **Step 7: Verify protected data and public routes**

Require every protected count to stay equal or increase and HTTP 200 from `/`, `/stories`, `/radar`, and `/signals`. Confirm worker restart counts remain stable and report the first current story and Radar cycle timestamps.
