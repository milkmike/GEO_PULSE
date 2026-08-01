# Search and Radar Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore indexed article search under the existing database timeout and persist private radar candidates so the 90-day lifecycle can mature them.

**Architecture:** Search uses a materialized GIN-only ID stage before verified-publisher joins, structured filters, date sorting, and ranking. Radar keeps its public quality gate unchanged while its apply path stores genuine candidate observations and trends idempotently for later incremental cycles.

**Tech Stack:** Python 3, SQLAlchemy text queries, PostgreSQL full-text search/GIN, FastAPI, pytest.

## Global Constraints

- Do not change `src/engine/index.py` or API v1 thermometer behavior.
- Do not delete or rewrite articles, analysis, temperature, stories, radar evidence, or analyst overrides.
- Keep the PostgreSQL search statement timeout at exactly two seconds.
- Keep radar candidates and rejected trends hidden from default public API responses.
- Continue excluding synthetic `story:*` and generic `media:coverage` waves from radar persistence.
- Do not add secrets, server addresses, or production credentials to the repository.
- No schema migration is required.

---

### Task 1: Force lexical search through the GIN candidate stage

**Files:**
- Modify: `src/search.py`
- Modify: `tests/test_search.py`
- Modify: `tests/test_search_postgres.py`

**Interfaces:**
- Consumes: `ARTICLE_SEARCH_SQL`, the existing named SQL parameters, and `idx_articles_search_vector`.
- Produces: a `lexical_article_ids AS MATERIALIZED` CTE whose rows feed `full_text_candidate_ids` without changing the public `search_articles()` return shape.

- [ ] **Step 1: Update the SQL-structure regression test before production code**

Change `test_full_text_search_bounds_ids_before_expensive_rank_calculation` so it requires a materialized `lexical_article_ids` CTE containing:

```python
lexical_ids_sql = ARTICLE_SEARCH_SQL[
    ARTICLE_SEARCH_SQL.index("lexical_article_ids AS"):
    ARTICLE_SEARCH_SQL.index("full_text_candidate_ids AS")
]
candidate_ids_sql = ARTICLE_SEARCH_SQL[
    ARTICLE_SEARCH_SQL.index("full_text_candidate_ids AS"):
    ARTICLE_SEARCH_SQL.index("full_text_candidates AS")
]

assert "lexical_article_ids AS MATERIALIZED" in lexical_ids_sql
assert "a.search_vector @@ sq.tsq" in lexical_ids_sql
assert "ORDER BY" not in lexical_ids_sql
assert "JOIN lexical_article_ids lexical ON lexical.id = a.id" in candidate_ids_sql
assert "a.search_vector @@ sq.tsq" not in candidate_ids_sql
assert "ORDER BY a.published_at DESC, a.id DESC" in candidate_ids_sql
assert "LIMIT :candidate_limit" in candidate_ids_sql
```

- [ ] **Step 2: Run the focused test and verify the expected failure**

Run:

```bash
pytest tests/test_search.py::test_full_text_search_bounds_ids_before_expensive_rank_calculation -q
```

Expected: fail because `lexical_article_ids` does not exist.

- [ ] **Step 3: Add the minimal materialized lexical-ID stage**

Insert this CTE immediately before `full_text_candidate_ids` in `ARTICLE_SEARCH_SQL`:

```sql
lexical_article_ids AS MATERIALIZED (
    SELECT a.id
    FROM articles a
    CROSS JOIN search_query sq
    WHERE :q <> ''
      AND a.search_vector @@ sq.tsq
      AND a.is_duplicate = FALSE
),
```

Then start `full_text_candidate_ids` with:

```sql
SELECT a.id, a.published_at
FROM lexical_article_ids lexical
JOIN articles a ON a.id = lexical.id
JOIN matching_sources s ON s.article_id = a.id
```

Remove the duplicate `a.search_vector @@ sq.tsq` predicate and unnecessary
`CROSS JOIN search_query sq` from that downstream CTE. Preserve all snapshot,
country, entity, topic, date, language, ordering, and candidate-limit clauses.

- [ ] **Step 4: Run unit and PostgreSQL plan tests**

Run:

```bash
pytest tests/test_search.py tests/test_search_postgres.py -q
```

Expected: all search tests pass; the PostgreSQL plan test observes a bitmap scan
of `idx_articles_search_vector`, no executed sequential scan of `articles`, and
execution under two seconds.

- [ ] **Step 5: Commit the search fix**

```bash
git add src/search.py tests/test_search.py tests/test_search_postgres.py
git commit -m "fix: route article search through gin index"
```

### Task 2: Persist private radar candidates for lifecycle accumulation

**Files:**
- Modify: `src/radar/service.py`
- Modify: `tests/test_radar_service.py`
- Verify: `tests/test_radar_api.py`

**Interfaces:**
- Consumes: `_admissible_persistence(generated, waves, metas)` and the existing idempotent radar repository writes.
- Produces: genuine media candidates and their generated observations in private radar tables; no public API contract change.

- [ ] **Step 1: Change the candidate persistence regression test before production code**

Rename `test_one_day_candidate_is_reported_but_not_persisted` to
`test_one_day_candidate_is_persisted_for_future_lifecycle_cycles` and replace
the negative write assertion with:

```python
assert report.country_waves[0].state is TrendState.CANDIDATE
assert any(
    "INSERT INTO radar_observations" in sql
    for sql, _ in session.calls
)
assert any(
    "INSERT INTO radar_trends" in sql
    for sql, _ in session.calls
)
```

- [ ] **Step 2: Run the focused test and verify the expected failure**

Run:

```bash
pytest tests/test_radar_service.py::test_one_day_candidate_is_persisted_for_future_lifecycle_cycles -q
```

Expected: fail because `_admissible_persistence` removes media candidates.

- [ ] **Step 3: Remove only the media-candidate exclusion**

Change `_admissible_persistence` so `persisted_waves` is equivalent to:

```python
persisted_waves = tuple(
    wave for wave in waves
    if not wave.subject_key.startswith("story:")
    and wave.subject_key != "media:coverage"
)
```

Update its docstring to state that genuine candidates are stored privately for
lifecycle history while synthetic subjects remain excluded. Do not change
lifecycle thresholds or public-state filters.

- [ ] **Step 4: Add an incremental-history regression test**

Add a test that supplies one stored candidate observation from the prior day
through `_history_for_identities`, one current observation through
`build_media_observations`, runs with `generation_days=3`, and asserts that the
resulting country wave contains both distinct observations. This proves the
next hourly cycle can reuse persisted candidate history rather than starting
from zero.

- [ ] **Step 5: Run radar service and public API tests**

Run:

```bash
pytest tests/test_radar_service.py tests/test_radar_api.py tests/test_build_radar.py -q
```

Expected: all tests pass, radar apply writes candidates, repeat writes remain
idempotent, and public list/detail queries still exclude candidate rows.

- [ ] **Step 6: Commit the radar fix**

```bash
git add src/radar/service.py tests/test_radar_service.py
git commit -m "fix: retain radar candidates for lifecycle history"
```

### Task 3: Verify, deploy, and rebuild radar history safely

**Files:**
- Verify: `deploy/deploy.sh`
- Verify: `scripts/build_radar.py`
- Verify: `scripts/audit_radar_wave1.py`

**Interfaces:**
- Consumes: the two committed fixes, the existing main-branch auto-deploy, and production radar audit commands.
- Produces: verified HTTP search responses and a populated private candidate history while preserving public quality gates.

- [ ] **Step 1: Run the complete automated test suite**

Run:

```bash
pytest -q
```

Expected: zero failures.

- [ ] **Step 2: Check repository integrity**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors and no uncommitted implementation changes.

- [ ] **Step 3: Push the reviewed commits to main**

Run:

```bash
git push origin main
```

Expected: remote main advances to the radar-fix commit and the existing
auto-deploy begins within five minutes.

- [ ] **Step 4: Verify deployed search before radar apply**

Call the public article-search endpoint with a representative lexical query and
check that it returns HTTP 200 within the API timeout, with real article URLs
and verified publisher/country attribution. Repeat with a country filter.

- [ ] **Step 5: Run a production 90-day shadow radar cycle**

Inside the deployed radar worker environment, run:

```bash
python scripts/build_radar.py --days 90 --generation-days 90
```

Expected: no writes, valid evidence ratio, no protected-table deletion, and a
reviewable candidate/emerging/confirmed distribution.

- [ ] **Step 6: Apply one full 90-day radar cycle**

Only after the shadow audit passes, run:

```bash
python scripts/build_radar.py --days 90 --generation-days 90 --apply
```

Expected: non-negative radar observation/trend deltas, zero deltas for articles,
stories, signals, and temperature, and no loss of analyst T0 overrides.

- [ ] **Step 7: Verify the incremental worker and public radar API**

Confirm the scheduled worker returns to `--generation-days 3`, completes a
cycle without restart, and reports inserted or reused observations. Check that
the public radar API remains HTTP 200 and contains only emerging/confirmed
quality-gated items; an empty response is acceptable immediately after backfill
if no wave qualifies.

- [ ] **Step 8: Record the deployment evidence**

Record the deployed commit, search response time/status, radar shadow/apply
counts, worker status, and public radar item count in the release handoff. Do
not include secrets or server addresses.
