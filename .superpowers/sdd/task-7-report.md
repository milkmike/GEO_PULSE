# Task 7 report: attribution recomputation and monitoring

## Status

DONE

Starting HEAD: `cb3698b`

## RED evidence

The Task 7 test file was written before production changes and run with the
existing methodology suite:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider \
  tests/test_attribution_recompute.py tests/test_methodology.py -q
```

Result: `7 failed, 11 passed in 0.19s`. The failures were the missing
`calculate_temperature_at` API/delegation and the two absent Task 7 scripts;
all pre-existing methodology tests remained green.

Self-review found that the first audit implementation covered curated Russian
registry domains but not the design's explicit `.ru` case. A focused
regression was added and produced:

```text
1 failed in 0.37s
```

The audit SQL was then changed to gate both `.ru` publisher domains and
verified registry domains whose canonical country is `RU`.

## GREEN evidence

Required focused command:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider \
  tests/test_attribution_recompute.py tests/test_methodology.py \
  tests/test_signal_evidence.py tests/test_stories.py -q
```

Result: `130 passed, 2 pre-existing deprecation warnings in 0.84s`.

Full backend command:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider -q
```

Result: `461 passed, 7 skipped, 2 pre-existing deprecation warnings in 4.94s`.

Both new CLIs also render `--help` successfully with `PYTHONPATH=.`.
`git diff --check` passed.

## Files changed

- `src/engine/index.py`
- `scripts/recompute_attribution_window.py`
- `scripts/audit_google_news_attribution.py`
- `tests/test_attribution_recompute.py`
- `docs/release/investigation-search-stories.md`
- `.superpowers/sdd/task-7-report.md`

Concurrent Task 6 edits in `scripts/backfill_google_news_attribution.py` and
`tests/test_google_news_attribution_backfill.py` were neither modified for this
task nor included in its staging set.

## Implemented behavior

- `calculate_temperature()` delegates to the single Thermometer v1
  `calculate_temperature_at()` implementation.
- Article selection uses the canonical publisher view, a parameterized
  14-day lower bound, and `published_at <= :as_of`; trend/anomaly history is
  bounded to points before the requested instant.
- All existing v1 weights, decay, event clustering, components, normalization,
  and rounding remain shared. Current anomaly alert behavior remains enabled,
  while recomputation suppresses historical alert writes.
- Recompute defaults to dry-run, selects only existing keys in the 90-day
  output window, exposes the 104-day input span, reports per-key before/after
  deltas, and performs no dry-run writes.
- Apply mode upserts only those selected keys in bounded batches, never deletes
  points, and leaves pre-window points and `pattern_type` untouched.
- After temperature apply it invokes the existing current RRI, signal, and
  brief jobs followed by the normal 30-day thread/story builder. It does not
  import either legacy backfill path.
- The read-only audit reports metadata extraction coverage,
  verified/reassigned/unknown totals, discovery-to-publisher country matrix,
  Russian-domain and legacy-view leaks, current counts, and baseline deltas.
- Audit gates fail on `.ru` or curated Russian domains attributed to another
  country, legacy/unknown rows in `article_country_facts`, or article and
  temperature count loss.
- The release runbook records baseline capture, dry-run review, apply ordering,
  container commands, audit gates, and stop-without-delete behavior.

## Self-review

- Re-read the Task 7 brief and publisher-attribution observability design.
- Confirmed no `DELETE` exists in either new script.
- Confirmed recomputation never calls `backfill_temperature` or
  `backfill_investigation_data`.
- Confirmed the upsert updates only Thermometer-calculated fields and does not
  overwrite `pattern_type`.
- Confirmed JSON ordering and key traversal are deterministic.
- Confirmed only the six Task 7 paths listed above will be staged.

## Concerns

No blocking concerns. The requested `.venv311` environment is absent, so tests
used the repository's `.venv`. No production database mutation was run during
implementation; database behavior is covered by read/write recording fixtures
and the complete backend suite. Production rollout must still follow the
documented dry-run and audit gates before `--apply`.
