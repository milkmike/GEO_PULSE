# GEO PULSE Production Pipeline Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore fast production reads, resilient collection, complete semantic story coverage, and an evidence-bearing two-contour radar without deleting historical data.

**Architecture:** Hot paths select bounded IDs before joining compatibility views, while additive indexes support country/time reads. Telegram receives an explicit Telethon proxy plus in-process reconnect control. Embedding preparation uses the same eligible article population as story formation. Radar derives dynamic meta velocity from current waves and materializes attributable structured actions before linking contours.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, PostgreSQL 16, Redis, Telethon, Docker Compose, Pytest, Next.js 15, React 19, Vitest.

## Global Constraints

- Existing articles, analysis, temperature, RRI, signals, briefs, threads, stories, memberships, radar observations, and evidence are never deleted.
- Production migrations are additive and idempotent.
- Every behavior change follows a verified red-green TDD cycle.
- Redis failure cannot trigger an unbounded historical database scan.
- Proxy credentials are never written to logs or committed.
- Story clustering keeps the existing semantic/entity/topic corroboration thresholds.
- Radar publication still requires at least two countries, measurable non-zero motion, a public lifecycle state, and public evidence.
- Deployment stops if any protected production row count decreases.

---

### Task 1: Bounded analyzer fallback

**Files:**
- Modify: `tests/test_analyze_attribution.py`
- Modify: `scripts/analyze.py`
- Create: `scripts/migrations/031_pipeline_recovery_indexes.sql`
- Modify: `data/init.sql`
- Test: `tests/test_postgres_migrations.py`
- Test: `tests/test_schema_contract.py`

**Interfaces:**
- Produces: `load_unanalyzed_candidate_ids(session, *, batch_size: int, scan_limit: int) -> list[int]`.
- Produces: `load_unanalyzed_rows(session, article_ids: list[int]) -> list[Any]`.
- Produces: an explicit queue outcome (`processed`, `empty`, `unavailable`) so
  Redis transport failure is not treated as an empty queue.
- `analyze_new_articles(batch_size=100)` never performs the current full-history `LEFT JOIN analysis ... WHERE an.id IS NULL` query.

- [ ] **Step 1: Add a failing unit test for bounded ID selection**

Capture executed SQL and assert that candidate selection:

```python
assert "ORDER BY ar.collected_at DESC" in candidate_sql
assert "LIMIT :scan_limit" in candidate_sql
assert "LEFT JOIN analysis" not in candidate_sql
assert candidate_params == {"scan_limit": 2000}
```

Use `scan_limit=max(batch_size * 20, 1000)`. Then assert the second query
receives only selected IDs and uses:

```sql
WHERE ar.id = ANY(CAST(:article_ids AS integer[]))
  AND NOT EXISTS (SELECT 1 FROM analysis an WHERE an.article_id = ar.id)
```

Add loop tests proving an empty queue triggers the bounded fallback no more than
once per hour and a Redis transport error never triggers it.

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_analyze_attribution.py -k bounded`

Expected: FAIL because `analyze_new_articles` still executes the full anti-join.

- [ ] **Step 3: Implement bounded two-stage selection**

Select at most `max(batch_size * 20, 1000)` newest non-duplicate,
country-attributed article IDs through an indexable `articles` CTE:

```sql
WITH candidate_ids AS MATERIALIZED (
  SELECT id
  FROM articles
  WHERE is_duplicate = FALSE
  ORDER BY collected_at DESC, id DESC
  LIMIT :scan_limit
)
```

Check analysis existence only for those IDs, load at most `batch_size` rows,
and retain newest-first ordering. Preserve `article_country_facts` as the final
canonical attribution guard after IDs are bounded.

If no recent candidate is missing, return immediately. Run this fallback at
most hourly after a genuinely empty Redis queue. Old recovery remains an
explicit administrative script; it is not run in the one-minute loop.

- [ ] **Step 4: Add the additive index migration**

Create:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_articles_pending_scan
  ON articles (collected_at DESC, id DESC)
  WHERE is_duplicate = FALSE;
```

Register migration `031_pipeline_recovery_indexes.sql` in the existing
migration runner and mirror the index in `data/init.sql`.

- [ ] **Step 5: Verify query contract and regression tests**

Run: `.venv/bin/python -m pytest -q tests/test_analyze_attribution.py tests/test_postgres_migrations.py tests/test_schema_contract.py`

Expected: PASS.

- [ ] **Step 6: Commit Task 1**

Commit: `git commit -am "fix: bound analyzer database fallback"`

---

### Task 2: Fast country and aggregate APIs

**Files:**
- Modify: `tests/test_world_dossier.py`
- Modify: `tests/test_signal_evidence.py`
- Modify: `src/api/routes/world.py`
- Modify: `src/api/signal_article_context.py`
- Modify: `src/engine/health.py`
- Modify: `scripts/migrations/031_pipeline_recovery_indexes.sql`
- Modify: `data/init.sql`

**Interfaces:**
- Produces: hot country queries filter `articles.geo_country_code` before
  expensive joins and retain `article_country_facts` as the final canonical
  publisher-attribution guard.
- Produces: `load_signal_article_previews` limits the context pool per requested country/time window before ranking.
- Produces: health aggregates use the existing cache abstraction with a maximum five-minute TTL.

- [ ] **Step 1: Add failing SQL-contract tests**

For topics and signal previews assert:

```python
assert "ar.geo_country_code = :cc" in topics_sql
assert "JOIN article_country_facts" in topics_sql
assert "context_article_pool AS MATERIALIZED" in preview_sql
assert "source.country_code = ANY" not in preview_sql
```

For health, call twice with a fake cache and assert the second call does not execute source-health SQL.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_world_dossier.py tests/test_signal_evidence.py tests/test_source_health.py`

Expected: FAIL on the current compatibility-view scans and uncached health calculation.

- [ ] **Step 3: Rewrite country hot paths**

Use `articles.geo_country_code` and verified geo statuses as the initial country
predicate for topics, headlines, dossier signal context, and other affected
country/time reads. Retain `article_country_facts` and its country equality as
the final canonical publisher-attribution guard after the selective predicate.

Add:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_articles_geo_published_live
  ON articles (geo_country_code, published_at DESC, id DESC)
  WHERE is_duplicate = FALSE
    AND geo_status IN (
      'source_verified','publisher_verified','publisher_reassigned'
    );
```

- [ ] **Step 4: Bound signal preview fallback**

For each requested signal window, select at most 200 relevant article IDs for
the exact country and window before joining/ranking article text and source
metadata. Exact persisted `signal_evidence.article_ids` remain authoritative and
are not capped before total-count calculation.

- [ ] **Step 5: Cache health summaries**

Cache `/api/v2/health` and source coverage for 300 seconds using the existing
Redis/cache helper. Cache failure falls back to direct calculation and does not
change the response schema.

- [ ] **Step 6: Verify Task 2**

Run: `.venv/bin/python -m pytest -q tests/test_world_dossier.py tests/test_signal_evidence.py tests/test_source_health.py tests/test_api.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 2**

Commit: `git commit -am "perf: bound country and health queries"`

---

### Task 3: Telegram proxy and reconnect control

**Files:**
- Create: `tests/test_telegram_collector.py`
- Modify: `src/collectors/telegram.py`
- Modify: `Dockerfile.tg-collector`
- Modify: `docker-compose.yml`
- Modify: `.env.example`

**Interfaces:**
- Produces: `parse_telegram_proxy(value: str | None) -> dict[str, object] | None`.
- Consumes: `TELEGRAM_PROXY_URL`, supporting `socks5://`, `socks4://`, and `http://` URLs with optional username/password.
- Produces: `run_with_reconnect(client_factory, *, initial_delay=5, max_delay=300)`; network connection failure does not terminate the container.

- [ ] **Step 1: Add failing proxy parser tests**

```python
def test_parse_authenticated_socks5_proxy():
    proxy = parse_telegram_proxy("socks5://alice:secret@proxy.example:1080")
    assert proxy == {
        "proxy_type": "socks5",
        "addr": "proxy.example",
        "port": 1080,
        "username": "alice",
        "password": "secret",
        "rdns": True,
    }
```

Also assert unsupported schemes and missing ports raise `ValueError`, and that
rendered log messages do not contain username or password.

- [ ] **Step 2: Add a failing reconnect test**

Use a fake client whose first two `start()` calls raise `ConnectionError` and
whose third call enters the collection coroutine. Assert delays are `5, 10`,
the client is disconnected after each failed attempt, and the function does not
raise.

- [ ] **Step 3: Run tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_telegram_collector.py`

Expected: FAIL because proxy parsing and reconnect control do not exist.

- [ ] **Step 4: Implement Telethon proxy wiring**

Pass the parsed dictionary as `TelegramClient(..., proxy=proxy)`. Install
`python-socks[asyncio]` in `Dockerfile.tg-collector`. Add only
`TELEGRAM_PROXY_URL: ${TELEGRAM_PROXY_URL:-}` to the Telegram collector
environment. Do not reuse `HTTPS_PROXY`.

- [ ] **Step 5: Keep the process alive on MTProto failure**

Wrap connect/live/poll execution in bounded exponential reconnect. Reset delay
after a successful connection. `asyncio.CancelledError`, SIGTERM, invalid API
credentials, and invalid proxy configuration remain terminal.

- [ ] **Step 6: Verify Task 3**

Run: `.venv/bin/python -m pytest -q tests/test_telegram_collector.py`

Run: `docker compose config`

Expected: PASS; the rendered config contains the variable name but tests and
logs contain no credentials.

- [ ] **Step 7: Commit Task 3**

Commit: `git commit -am "fix: proxy and stabilize telegram collection"`

---

### Task 4: Complete story embedding coverage

**Files:**
- Modify: `tests/test_embedding_store.py`
- Modify: `tests/test_embedding_worker.py`
- Modify: `scripts/prepare_embedding_jobs.py`
- Modify: `scripts/run_embedding_worker.py`
- Modify: `scripts/build_threads.py`
- Modify: `scripts/audit_story_pipeline.py`
- Modify: `src/stories.py`
- Modify: `tests/test_stories.py`

**Interfaces:**
- Produces: `load_story_eligible_articles(session, *, days: int, limit: int)`.
- Embedding cycle report uses one candidate definition for `eligible`,
  `ready_current`, `missing_current`, and story audit coverage.
- Country-thread formation reads the single active ready
  `content_embeddings` article profile, not legacy `analysis.embedding`.
- The active-profile uniqueness and content-hash idempotency contracts remain unchanged.

- [ ] **Step 1: Add failing eligibility tests**

Create a fixture containing:

- two relevant cross-country articles without an existing thread;
- a duplicate article;
- an irrelevant article;
- an article without verified country attribution;
- one already embedded article.

Assert the loader returns the two relevant verified non-duplicates, the prepare
report says `eligible=2`, `ready_current=1`, `missing_current=1`, and one job is
enqueued. Add more eligible rows than `prepare_limit` and prove the second cycle
selects the next missing rows rather than repeatedly selecting already-complete
newest rows.

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_embedding_store.py tests/test_embedding_worker.py`

Expected: FAIL because the current worker only loads articles already selected
as cross-country thread candidates.

- [ ] **Step 3: Implement the shared eligible population**

Use the normal relevant-article loader in the scheduled worker rather than
`story_candidates=True`. Select recent articles that are:

```sql
analysis.is_relevant = TRUE
AND articles.is_duplicate = FALSE
AND articles.geo_country_code IS NOT NULL
AND articles.published_at >= NOW() - make_interval(days => :days)
```

Order newest first, cap preparation by `prepare_limit`, and preserve active
profile/content-hash idempotency. Exclude current-hash ready rows before applying
`LIMIT`, so bounded cycles advance through the entire pool. Keep the old
thread-quota loader only for explicit diagnostics. Rename the scheduled report
mode from `story_candidates` to `story_eligible_articles`.

- [ ] **Step 4: Move country threads to authoritative embeddings**

In `scripts/build_threads.py`, derive `has_embedding` and embedding pairs from
the one active 1024-dimensional ready `content_embeddings` profile. Reject an
inactive or wrong-dimension profile and add `articles.is_duplicate = FALSE`.
Keep existing `0.72/0.82` thread clustering thresholds and trigram fallback.

- [ ] **Step 5: Align story coverage reporting**

Make the story audit and hourly builder report ready/missing counts for the
same eligible population. Do not relax cluster admission thresholds.

- [ ] **Step 6: Verify Task 4**

Run: `.venv/bin/python -m pytest -q tests/test_embedding_store.py tests/test_embedding_worker.py tests/test_stories.py`

Expected: PASS.

- [ ] **Step 7: Commit Task 4**

Commit: `git commit -am "fix: embed all eligible story articles"`

---

### Task 5: Persistent action replay and admissible radar trends

**Files:**
- Modify: `tests/test_radar_service.py`
- Modify: `tests/test_radar_observations.py`
- Modify: `tests/test_radar_api.py`
- Modify: `src/radar/service.py`
- Modify: `src/radar/actions.py`
- Modify: `src/radar/grouping.py`
- Modify: `src/radar/media.py`
- Modify: `src/radar/repository.py`
- Modify: `scripts/build_radar.py`
- Create: `scripts/backfill_radar_actions.py`
- Modify: `Dockerfile.temperature`

**Interfaces:**
- Produces: incremental cycles replay persisted action observations across the
  retained lookback even when no annual structured row was refreshed today.
- Produces: idempotent `upsert_action_events` keyed by existing `input_hash`.
- Produces: media radar subjects only from canonical story/analysis event keys.
- Existing public velocity and evidence gates remain unchanged; annual static
  snapshots with zero velocity stay hidden.

- [ ] **Step 1: Lock the public quality gate**

Add a regression test proving an annual/snapshot meta trend with zero velocity
does not enter the public list even with two countries and evidence.

- [ ] **Step 2: Add failing incremental action replay tests**

Persist an action observation outside the three-day generation window but inside
the ninety-day lookback. Run an incremental cycle with no newly generated
actions and assert the saved action participates in wave scoring and
media/action matching without being inserted again.

- [ ] **Step 3: Add failing action-time and evidence tests**

Assert an unchanged annual UN/trade loader refresh does not become a new
effective action merely because `updated_at` changed. A corrected annual value
may produce revision evidence while retaining its real period/effective date.
Assert each accepted action produces an idempotent `action_events` row and action
evidence includes both `observation_id` and `action_event_id`.

- [ ] **Step 4: Add failing media admission and contour-link tests**

Assert a media and action wave link only when normalized subject/direction and
the configured time window match. Unmatched waves remain persisted with
`insufficient` contour status and no fabricated link. A story without a
canonical event key produces no radar observation; one with an event key uses
`event:<key>`. A one-day candidate may be calculated but is not persisted.

- [ ] **Step 5: Run focused tests and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_radar_service.py tests/test_radar_observations.py tests/test_radar_api.py`

Expected: FAIL on absent persisted action replay/write path and current generic
media candidate persistence.

- [ ] **Step 6: Replay persisted action history**

During a short incremental generation, load persisted action observations from
the retained lookback and combine them with current media identity history and
previous waves. Do not regenerate unchanged annual observations hourly.

- [ ] **Step 7: Correct temporal semantics and materialize action events**

Separate dataset refresh time from annual period/effective time. Add repository
upsert by deterministic `input_hash` and persist `action_event_id` on action
trend evidence. `details` include dataset, source ID, temporal resolution,
period, previous/current value, and delta. Do not treat the generic sanctions
dataset as a Russia-specific action source in this release.

- [ ] **Step 8: Backfill existing action observations additively**

`scripts/backfill_radar_actions.py` derives events only from existing
`radar_observations WHERE contour='action'`, uses `ON CONFLICT DO NOTHING`, and
fills only missing `action_event_id` evidence roots. It never deletes or rewrites
historical observations, trends, or state events.

- [ ] **Step 9: Stop inadmissible candidate growth**

Media subject priority becomes canonical story event key, then analysis event
key, otherwise no radar observation. Do not persist `story:<id>`,
`media:coverage`, or candidate waves/metas. Collection health remains an
internal coverage input. Existing historical candidates remain untouched.

- [ ] **Step 10: Verify Task 5**

Run: `.venv/bin/python -m pytest -q tests/test_radar_service.py tests/test_radar_observations.py tests/test_radar_grouping.py tests/test_radar_lifecycle.py tests/test_radar_api.py tests/test_build_radar.py`

Expected: PASS.

- [ ] **Step 11: Commit Task 5**

Commit: `git commit -am "fix: produce evidence bearing radar trends"`

---

### Task 6: Integration, deployment, and production proof

**Files:**
- Modify if needed: `scripts/deploy_prod.sh`
- Create: `scripts/audit_production_recovery.py`
- Test: `tests/test_production_recovery_audit.py`

**Interfaces:**
- Audit emits JSON with protected counts, freshness, worker restarts, API
  latency, story embedding coverage, public radar count, action-event count,
  and contour-link count.
- Audit exits non-zero on protected-count decrease or failed acceptance gate.

- [ ] **Step 1: Add a failing audit contract test**

Given before/after fixtures, assert a decreased protected count fails, stale
timestamps fail, and credentials are redacted from emitted JSON.

- [ ] **Step 2: Run test and verify RED**

Run: `.venv/bin/python -m pytest -q tests/test_production_recovery_audit.py`

Expected: FAIL because the audit command does not exist.

- [ ] **Step 3: Implement the read-only audit**

Use public endpoints and read-only SQL. Never read or print `.env`. Store only
aggregate counts and timestamps.

- [ ] **Step 4: Run full local verification**

Run: `.venv/bin/python -m pytest -q`

Run: `cd web && npm test`

Run: `cd web && npm run build`

Run: `docker compose config`

Expected: all commands exit zero.

- [ ] **Step 5: Deploy additive migration and services in canary order**

Record protected counts, push the reviewed commit, apply migration, then recreate
only the services named in the design rollout. Existing PostgreSQL and Redis
volumes remain attached.

- [ ] **Step 6: Run bounded recovery**

Backfill the current thirty-day eligible article embedding window in bounded
batches, run one story cycle, one radar cycle, and verify their reports before
leaving loops enabled.

- [ ] **Step 7: Verify production acceptance criteria**

Measure all endpoints cold and warm. Verify analyzer fallback latency,
Telegram restart count stability and proxy connection, embedding coverage,
current story memberships, radar public output, action events, contour links,
and protected counts.

- [ ] **Step 8: Reclaim safe build cache**

After the release and rollback image are confirmed, prune only unused Docker
build cache. Do not prune images, containers, networks, or volumes. Recheck disk,
memory, swap, and load.

- [ ] **Step 9: Commit audit tooling**

Commit: `git commit -am "ops: verify production pipeline recovery"`
