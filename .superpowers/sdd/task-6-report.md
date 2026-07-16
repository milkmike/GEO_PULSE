# Task 6 report: Google News attribution backfill

## Status

DONE

Task 6 starting HEAD: `e4d7d08`. The shared checkout advanced to `43b86b1`
when the disjoint Task 5B commit landed before this task was committed.

## RED evidence

The focused test was run before the backfill script existed:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider tests/test_google_news_attribution_backfill.py -q
```

Result: collection failed with `ImportError` because
`scripts.backfill_google_news_attribution` did not exist.

After the initial implementation, the same command exposed two test-harness
problems in sequence: the fresh-checkpoint idempotency fixture still requested
an interruption, and the protected-column assertion treated the allowed
`publisher_source_id` field as `source_id`. Those assertions were narrowed to
the intended behavior. A subsequent self-review added the missing
unclassified-domain count expectation; the focused test then failed because
`(unknown)` was not reported, and the implementation was updated to count it.

## GREEN evidence

Focused command:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider tests/test_google_news_attribution_backfill.py -q
```

Result: `4 passed in 0.13s`.

Required integration command:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider tests/test_google_news_attribution_backfill.py tests/test_stories.py tests/test_search.py -q
```

Result: `132 passed, 2 pre-existing deprecation warnings in 0.93s`.

`git diff --check` passed for both Task 6 implementation files.

## Files changed

- `scripts/backfill_google_news_attribution.py`
- `tests/test_google_news_attribution_backfill.py`
- `.superpowers/sdd/task-6-report.md`

## Implemented behavior

- `run_backfill` defaults to dry-run and scans 104 days in bounded keyset
  batches using the required discovery-feed predicate, cursor, ordering, and
  limit.
- Classification accepts only an exact normalized final title suffix and only
  when a verified registry source name maps uniquely to one publisher source.
  Ambiguous names fail closed as `legacy_unverified`.
- Apply mode mutates only the allowed publisher/geo fields during attribution;
  unclassified rows receive only the legacy-unverified status.
- Each apply batch commits before its cursor is persisted. Atomic JSON-file
  checkpoints and compatible load/save checkpoint stores support crash/resume.
- Re-running from either a completed checkpoint or a fresh checkpoint is
  idempotent, including the verification timestamp.
- Affected verified rows are reconciled by canonical publisher/external ID or
  exact normalized title within 48 hours. The lowest article ID remains the
  parent; only duplicate metadata and parent reprint counts change.
- Reports include discovery-country, publisher-country, status, and domain
  counts plus protected before/after invariants.
- The script never deletes or enqueues rows. It verifies unchanged article,
  analysis, and story-membership row counts and hashes every
  `(id, source_id, external_id, url)` tuple before returning success.
- The CLI exposes only `--apply`, `--since-days`, `--batch-size`,
  `--checkpoint`, and `--report` beyond standard help.

## Test coverage

- dry-run is the default and does not write either database state or a
  checkpoint;
- exact publisher classification, country reassignment, and ambiguous-name
  fail-closed behavior;
- interruption after the first committed batch, checkpoint resume, and both
  completed-checkpoint and fresh-checkpoint idempotency;
- duplicate parent selection, external-ID matching, title/window matching, and
  reprint-count recomputation;
- immutable provenance and row-count invariants;
- required keyset SQL, absence of deletes/protected-column writes, and the
  constrained CLI/report surface.

## Concerns

No blocking concerns. The requested `.venv311` environment is absent in this
checkout, so verification used the repository's `.venv`. The focused backfill
tests use a transactional in-memory backend that exercises commit/rollback and
checkpoint ordering; they do not execute the SQL against a live PostgreSQL
instance.
