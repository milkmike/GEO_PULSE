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

## Review-fix evidence

Review found four production-safety gaps in the initial Task 6 commit. New
regressions produced `6 failed, 1 passed, 1 skipped` before the fixes:

- Stage 1 `publisher_verified`/`publisher_reassigned` discovery rows were
  scanned and could be overwritten or downgraded;
- a second unresolved row resolving to the same
  `(publisher_source_id, external_id)` raised the production unique-index
  violation before duplicate reconciliation could run;
- equal normalized titles from different canonical countries were joined into
  one duplicate component;
- a resumed report contained only the post-checkpoint tail because audit
  counters and the discovery/publisher matrix were not checkpointed.

A follow-up edge-case regression then failed because an exact-collision loser
remained attached to an intermediate winner after title reconciliation moved
that winner under an older parent. Exact-conflict families are now flattened
after title reconciliation, preserving the lowest parent and full reprint
count.

After the fixes, the focused non-PostgreSQL command reports
`7 passed, 1 skipped`. The required integration command reports
`139 passed, 1 skipped, 2 pre-existing deprecation warnings in 2.83s`.

The opt-in PostgreSQL regression was also executed against a disposable
`postgres:16-alpine` container with the real partial unique index:

```bash
GEO_PULSE_TEST_DATABASE_URL=postgresql://... \
GEO_PULSE_TEST_DATABASE_RESET=1 \
.venv/bin/python -m pytest \
  tests/test_google_news_attribution_backfill.py::test_postgres_unique_publisher_external_collision_is_reconciled_safely -q
```

Final result after the reconciliation-order fix: `1 passed in 0.55s`. The
disposable container was removed after the
test.

## Files changed

- `scripts/backfill_google_news_attribution.py`
- `tests/test_google_news_attribution_backfill.py`
- `.superpowers/sdd/task-6-report.md`

## Implemented behavior

- `run_backfill` defaults to dry-run and scans 104 days in bounded keyset
  batches using the required discovery-feed predicate, cursor, ordering, and
  limit.
- Selection and both attribution updates accept only unresolved/legacy rows:
  historical discovery rows in `source_verified` with null attribution
  markers, plus `unverified`/`legacy_unverified`. Stage 1 verified/reassigned
  and any already-attributed rows are excluded in both SELECT and UPDATE.
- Classification accepts only an exact normalized final title suffix and only
  when a verified registry source name maps uniquely to one publisher source.
  Ambiguous names fail closed as `legacy_unverified`.
- Apply mode mutates only the allowed publisher/geo fields during attribution;
  unclassified rows receive only the legacy-unverified status.
- Each apply batch commits before its cursor is persisted. Atomic JSON-file
  checkpoints and compatible load/save checkpoint stores support crash/resume.
  Checkpoint version 2 also persists cumulative scan/mutation counters,
  category counts, and the discovery-country/publisher-country matrix, so a
  resumed final report covers the full run.
- Re-running from either a completed checkpoint or a fresh checkpoint is
  idempotent, including the verification timestamp.
- Affected verified rows are reconciled by canonical publisher/external ID or
  exact normalized title within 48 hours. The lowest article ID remains the
  parent; title matching is canonical-country-scoped in both SQL and connected
  component logic. Only duplicate metadata and parent reprint counts change.
- Exact publisher/external-ID claims are checked before attribution UPDATE.
  A colliding loser remains fail-closed with null `publisher_source_id`, is
  marked `legacy_unverified`, and is reconciled into the duplicate family,
  avoiding the non-deferrable partial unique-index violation.
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
  completed-checkpoint and fresh-checkpoint idempotency, including cumulative
  counters and matrix restoration;
- immutable Stage 1 verified/reassigned rows alongside eligible historical
  `source_verified/null/null` rows;
- real PostgreSQL partial-unique-index collision behavior;
- duplicate parent selection, external-ID matching, title/window matching, and
  canonical-country-scoped reprint-count recomputation;
- immutable provenance and row-count invariants;
- required keyset SQL, absence of deletes/protected-column writes, and the
  constrained CLI/report surface.

## Concerns

No blocking concerns. The requested `.venv311` environment is absent in this
checkout, so verification used the repository's `.venv`. Most focused tests
use a transactional in-memory backend for commit/rollback and checkpoint
ordering; the unique-index safety path additionally runs as an opt-in test
against explicitly disposable PostgreSQL. Checkpoint version 1 files are
rejected intentionally because they do not contain the cumulative audit state
required for a trustworthy resumed report.
