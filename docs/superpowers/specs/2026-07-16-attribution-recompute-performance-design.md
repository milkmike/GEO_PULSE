# Attribution Recompute Performance Design

## Goal

Make the 90-day attribution recompute safe to run against hourly global
temperature history without changing Thermometer v1 output, chronological
trend/anomaly behavior, or dry-run/apply equivalence.

## Approved architecture

`src/engine/index.py` will expose a pure `calculate_temperature_from_rows()`
entry point containing the existing v1 arithmetic and accepting explicit
newest-first temperature history. The current
`calculate_temperature_at()` API will keep its canonical database query and
delegate to the pure function; history reads and anomaly-alert persistence stay
in that wrapper, preserving live behavior while the row calculator remains
database-free.

`scripts/recompute_attribution_window.py` will group existing temperature keys
by country. It will load the canonical, relevant, non-backfill article inputs
once per country for the full 104-day input span, sort them by publication
time, and use binary search to select the exact
`as_of - 14 days < published_at <= as_of` slice for each key. This bounds
read queries to `countries + 2` instead of `keys + 2` and avoids retaining all
countries' article rows simultaneously.

Both canonical queries include article/analysis identifiers and the same total
publication/identifier order. Equal-weight rows within an event cluster use
that order as their deterministic tie-breaker, so live and preloaded paths
assign the same diminishing factors.

Each country will be processed chronologically with a `deque(maxlen=30)` seeded
from persisted pre-window history. A recalculated point is appended before the
next point; a skipped point appends its old value. Results are sorted back to
`(time, country_code)` before reporting and apply.

## Bounded reporting

The report will retain aggregate counts and at most a configurable number of
representative changed deltas. It will expose both the total changed count and
the number of omitted deltas, so the CLI remains useful without deep-copying
hundreds of thousands of before/after dictionaries. Apply rows remain buffered
until calculation is complete, preserving the existing dry-run/apply boundary.

The default sample limit is 100. `--delta-limit` accepts zero to disable delta
samples. Sampling is deterministic: the first changed keys in global
`(time, country_code)` order are retained.

## Error and compatibility behavior

- The pure calculator validates/normalizes `as_of` exactly like the wrapper.
- The canonical preload uses the same relevance, sentiment, attribution,
  backfill, and time predicates as the current per-key query.
- Current anomaly alert behavior remains enabled for live calculation;
  historical recompute continues suppressing alert writes.
- No database writes occur during dry-run.
- Apply upserts only existing keys after all calculations are complete.
- Existing `calculate_temperature()` and `calculate_temperature_at()` callers
  require no changes.

## Verification contract

- Exact pure/direct v1 parity on the same article and history rows.
- Exact lower-exclusive and upper-inclusive 14-day window boundaries.
- `countries + 2` dry-run reads for more than 1,000 keys.
- Chronological cascade and dry-run/apply equality.
- Bounded 30-point history and bounded delta samples.
- Global `(time, country_code)` report/apply ordering.
- Apply writes equal `ceil(recalculated / batch_size)` in addition to reads.
- Focused and complete backend suites pass.
