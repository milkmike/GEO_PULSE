# Task 7 performance report

## Status

DONE

Design/plan commit: `17b9027`

## RED evidence

The required regressions were added before production changes. The initial
focused run produced `8 failed, 10 passed`: the row-based v1 calculator and
exact window selector did not exist, recompute still called the canonical
article query once per key, and the report had no bounded-delta contract.

A separate allocation regression then produced `1 failed, 17 deselected`:
1,002 changed keys constructed 1,002 `TemperatureDelta` objects instead of the
maximum 14 candidates allowed for two countries with `delta_limit=7`.

## Implemented behavior

- `calculate_temperature_from_rows()` now contains the unchanged Thermometer
  v1 grouping, weighting, decay, component, rounding, trend, and anomaly
  formulas. It accepts explicit newest-first history and is database-free even
  with its default. The existing `calculate_temperature_at()` signature remains
  the public wrapper; it owns the bounded history read and live anomaly alerts.
- Both the live and preload queries select article/analysis IDs in the same
  total order. Equal-weight rows within an event cluster use publication time
  and those IDs as deterministic diminishing-factor tie-breakers.
- Recompute loads canonical relevant, analyzed, non-backfill article inputs
  once per country and uses binary search for the exact lower-exclusive,
  upper-inclusive 14-day slice at each existing temperature key.
- Countries are calculated chronologically with `deque(maxlen=30)`. Repaired
  values cascade into the next point; skipped points contribute their existing
  value. Dry-run and apply therefore calculate the same series.
- Changed-delta reporting exposes `delta_total` and `deltas_omitted` and retains
  at most `--delta-limit` deterministic samples in global `(time,
  country_code)` order.
- Apply calculations are written to per-country temporary spools after they are
  produced. Only after all calculations succeed are the spools merged into
  global key order and written in bounded batches. This preserves the
  calculate-before-write boundary without retaining every result dictionary.

## Query and memory benchmark

The deterministic fake-backend benchmark used 1,002 existing keys across two
countries, `batch_size=128`, and `delta_limit=7`:

```text
keys=1002 countries=2
dry_seconds=0.020327 apply_seconds=0.029081
dry_reads=4 apply_reads=4 apply_writes=8
delta_total=1002 retained_deltas=7 constructed_deltas=14
max_history=30
```

The previous per-key path required 1,004 reads for this fixture. The new path
uses `countries + 2 = 4` reads, a 251x reduction; apply adds exactly
`ceil(1002 / 128) = 8` upsert batches. Delta candidate allocation is bounded
by `countries * delta_limit`, and each country history is capped at 30 points.

## Verification

```text
tests/test_attribution_recompute.py
20 passed in 0.28s

tests/test_attribution_recompute.py tests/test_methodology.py tests/test_stories.py
120 passed in 0.90s

complete backend suite
484 passed, 8 skipped, 2 existing deprecation warnings in 6.26s
```

Python compilation and `git diff --check` passed. Verification used fakes and
temporary local files only; no production database was contacted or mutated.

A final repeated full-suite run after the clean result above observed a
concurrent, unstaged Task 6 edit and ended at `483 passed, 1 failed, 8 skipped`:
`tests/test_google_news_attribution_backfill.py:909` expected the companion bulk
SQL edit (`candidate_ids AS`) that was still in progress. The Task 7-focused
120-test run remained green, and neither concurrent Task 6 path is staged here.

An independent review found and blocked merge on equal-weight query-order drift
and an implicit session dependency in the first row-calculator version. Three
new RED regressions reproduced both issues; the deterministic tie-breaker and
explicit-history fixes above made them green before the final suite runs.

## Files in this performance change

- `src/engine/index.py`
- `scripts/recompute_attribution_window.py`
- `tests/test_attribution_recompute.py`
- `docs/release/investigation-search-stories.md`
- `docs/superpowers/specs/2026-07-16-attribution-recompute-performance-design.md`
- `docs/superpowers/plans/2026-07-16-attribution-recompute-performance.md`
- `.superpowers/sdd/task-7-performance-report.md`

The concurrent Task 6 bulk files were neither edited nor staged for this work.
