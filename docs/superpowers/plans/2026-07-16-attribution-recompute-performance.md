# Attribution Recompute Performance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce Task 7 recompute reads from one canonical article query per temperature key to one per country while preserving exact Thermometer v1 behavior and bounded reporting.

**Architecture:** Extract the existing v1 arithmetic into a pure row-based calculator used by the unchanged database wrapper. Recompute country groups with one canonical preload, bisected time windows, 30-point deques, deterministic global output ordering, and bounded delta samples.

**Tech Stack:** Python 3.11, SQLAlchemy, PostgreSQL, pytest.

## Global Constraints

- No production database mutation during development or verification.
- Preserve existing temperature keys, Thermometer v1 formulas, and live calculator APIs.
- Dry-run and apply must produce the same recalculated series from the same snapshot.
- Stage only Task 7 performance files; never stage or revert concurrent Task 6 files.

---

### Task 1: Pure Thermometer v1 calculator

**Files:**
- Modify: `src/engine/index.py`
- Test: `tests/test_attribution_recompute.py`

**Interfaces:**
- Produces: `calculate_temperature_from_rows(country_code, as_of, rows, *, history_session=None) -> dict | None`
- Preserves: `calculate_temperature_at(country_code, as_of, *, exclude_backfill=True) -> dict | None`

- [ ] Write a parity test that runs the database wrapper and pure entry point on identical article/history fixtures and compares their complete dictionaries.
- [ ] Run the parity test and verify it fails because the pure entry point is absent.
- [ ] Move the existing formula without changing weights, decay, grouping, components, rounding, trend, or anomaly behavior; make the wrapper query and delegate.
- [ ] Run the parity and current-calculator tests and verify they pass.

### Task 2: Per-country canonical preload and bounded chronological history

**Files:**
- Modify: `scripts/recompute_attribution_window.py`
- Test: `tests/test_attribution_recompute.py`

**Interfaces:**
- Produces: `_load_article_rows(country_code, input_start, window_end) -> list[Any]`
- Produces: exact bisected row windows for `window_start < published_at <= as_of`

- [ ] Add regressions for exact time boundaries, more than 1,000 keys using `countries + 2` reads, 30-point history bounds, multi-country ordering, cascade equality, and apply batch counts.
- [ ] Run the new tests and verify failures identify the per-key calculator/query path.
- [ ] Group keys by country, preload canonical rows once, bisect each window, and calculate with a per-country deque seeded from existing history.
- [ ] Sort calculations and deltas globally before apply/report construction.
- [ ] Run all attribution recompute tests and verify they pass.

### Task 3: Bounded delta report

**Files:**
- Modify: `scripts/recompute_attribution_window.py`
- Test: `tests/test_attribution_recompute.py`

**Interfaces:**
- Adds: `delta_limit: int = 100` to `recompute_window()`
- Adds: `delta_total` and `deltas_omitted` to `RecomputeReport`
- Adds: CLI `--delta-limit`

- [ ] Add a regression with more changed keys than the limit and assert deterministic samples, total count, omitted count, and CLI forwarding.
- [ ] Run it and verify the unbounded report fails the expectation.
- [ ] Retain only the first `delta_limit` changed deltas after global sorting while preserving aggregate counts.
- [ ] Run serialization and CLI tests and verify they pass.

### Task 4: Verification, benchmark, report, and commit

**Files:**
- Create: `.superpowers/sdd/task-7-performance-report.md`
- Modify only if needed: `docs/release/investigation-search-stories.md`

- [ ] Run focused attribution, methodology, and stories tests.
- [ ] Run a local fake-backend benchmark with at least 1,000 keys and record read/write counts and elapsed time.
- [ ] Run Python compilation, `git diff --check`, and the complete backend suite.
- [ ] Confirm the staged set excludes Task 6 files.
- [ ] Commit the Task 7 performance files with a separate performance-fix message.
