# Task 7 P1 review-fix report

## Status

DONE

Starting HEAD: `ba3ae4a`

## RED evidence

The initial review regressions produced `5 failed, 7 passed`:

- the second recalculated point queried stale persisted history;
- the recompute script still called global `build_threads()`;
- no scoped thread/story entry point existed;
- thread upsert could not guard old conflicts or suppress membership deletes;
- story building could not filter candidate IDs or suppress global lifecycle
  updates.

Self-review then added a narrower regression for recent threads that still
contained pre-window article memberships. It failed because the scoped story
builder had no article cutoff.

## Implemented behavior

- Trend and anomaly formulas still use Thermometer v1. During recomputation
  only, their history source is a context-local in-memory series seeded from
  the persisted points before the output window.
- Existing keys are recalculated chronologically. Each repaired temperature is
  appended before calculating the next key; a skipped key contributes its old
  persisted value. Dry-run and apply therefore calculate the same series while
  dry-run performs no writes.
- Post-apply maintenance calls `rebuild_recent_threads_and_stories(days=30)`
  instead of global `build_threads()`.
- The scoped thread path does not call global linking, dedup, cleanup, or story
  building. It adds memberships without replacement and refuses to update a
  conflicting thread whose `last_seen` predates the cutoff.
- The scoped story path filters candidate thread IDs and article evidence to
  the cutoff, refuses to update an older conflicting story, disables global
  lifecycle refresh and duplicate reconciliation, and upserts derived slices
  without executing deletes.

## Verification

```text
tests/test_attribution_recompute.py
tests/test_stories.py
tests/test_methodology.py
113 passed in 0.68s
```

Python compilation and `git diff --check` passed.

The full shared-checkout run reached `471 passed, 7 skipped` with two failures
in the concurrently edited Google News attribution-backfill checkpoint tests.
Those failures reproduce in isolation against the concurrent changes in
`scripts/backfill_google_news_attribution.py` and
`tests/test_google_news_attribution_backfill.py`; neither file is part of this
fix commit.

## Files in this fix

- `src/engine/index.py`
- `scripts/recompute_attribution_window.py`
- `scripts/build_threads.py`
- `src/stories.py`
- `tests/test_attribution_recompute.py`
- `tests/test_stories.py`
- `docs/release/investigation-search-stories.md`
- `.superpowers/sdd/task-7-review-fix-report.md`
