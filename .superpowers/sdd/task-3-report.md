# Task 3 report: normalize independent media and action observations

## Scope delivered

- Added immutable, timezone-aware `ObservationWindow` and `Observation` domain
  types to `src/radar/types.py`. This was an ownership expansion requested by
  the coordinator because the Task 2 brief had omitted the interfaces.
- Added daily media normalization in `src/radar/media.py` using verified
  `article_country_facts`, duplicate/future exclusions, source-family inference
  from the existing health logic, article/story/signal/entity evidence, and a
  successfully indexed national denominator.
- Added structured action normalization in `src/radar/actions.py` for
  sanctions, UN votes, trade, and fossil imports. It has no media-analysis
  input, so `analysis.action_level` cannot create action evidence. Snapshot
  imports compare with the latest persisted snapshot before emitting a change.
- Added SHA-256 evidence-identity hashing and `ON CONFLICT (input_hash) DO
  NOTHING` persistence in `src/radar/repository.py`.
- Added focused red/green observation, type, family-independence, action
  separation, authoritative-action, and idempotent-upsert tests.

## TDD record

1. Created `tests/test_radar_observations.py` before adapter implementation.
2. Red command: `.venv/bin/python -m pytest tests/test_radar_observations.py -q`
   failed at collection because `src.radar.actions` did not exist.
3. Implemented the minimum adapters and repository, then refined duplicate
   membership/source counting and unchanged snapshot handling while keeping the
   focused suite green.

`python` is not installed in this workspace shell; the project virtualenv
interpreter was used for every executable test command.

## Verification

Commands run after the final source change:

```text
.venv/bin/python -m pytest tests/test_radar_observations.py tests/test_stories.py tests/test_signal_evidence.py -q
git diff --check
.venv/bin/python -m py_compile src/radar/types.py src/radar/media.py src/radar/actions.py src/radar/repository.py
```

Result: `136 passed, 2 warnings` in 4.48s. The warnings are pre-existing
FastAPI `regex` deprecations in `src/api/main.py`; none originate from this
task. Whitespace and bytecode compilation checks completed successfully.

## Review correction pass

- Annual UN/trade observations now use their stable December 31 dataset-period
  timestamp and a period/value fingerprint; `updated_at` is not part of their
  identity or effective time.
- Fossil imports use `numeric` comparisons with the latest compatible Radar
  observation or action event. Initial snapshots and canonical-equal values do
  not emit actions; changes preserve numeric previous/current/delta evidence.
- Media now uses transitive publisher-family merging, sorted multi-event
  subject associations, and all verified national articles as its denominator.
- Hashes now include top-level and evidence-contained article/story/entity/
  signal/action roots.
- Added regressions for all review findings.

Final correction-pass verification:

```text
.venv/bin/python -m pytest tests/test_radar_observations.py tests/test_stories.py tests/test_signal_evidence.py -q
git diff --check
.venv/bin/python -m py_compile src/radar/types.py src/radar/media.py src/radar/actions.py src/radar/repository.py
```

Result: `143 passed, 2 warnings` in 7.32s.

## Second review correction pass

- Annual UN/trade collection queries now select by the real `updated_at` inside
  the requested window. Observations retain that timestamp and record
  `updated_at`, `temporal_resolution: year`, and `period_year` in evidence.
  Their input identity instead uses the stable dataset/country/year/value
  fingerprint, so a refresh without data changes remains idempotent.
- Fossil prior-state selection now accepts only verified registry/formal Radar
  observations or verified action events explicitly identified as
  `ru_fossil_imports`; reported media cannot supply numeric state.
- Source fingerprints now include emitted deltas, including trade's emitted
  YoY value, so corrected structured magnitudes create new immutable evidence.
- Added an executable annual-window test-double that validates and applies the
  generated `updated_at` predicate, plus replay, authority, and correction
  regressions.

Final second-pass verification:

```text
.venv/bin/python -m pytest tests/test_radar_observations.py tests/test_stories.py tests/test_signal_evidence.py -q
git diff --check
.venv/bin/python -m py_compile src/radar/actions.py src/radar/media.py src/radar/repository.py src/radar/types.py
```

Result: `147 passed, 2 warnings` in 1.47s.

## Final observation-identity correction

- Removed the synthetic annual identity timestamp. Every observation hash and
  public ID now use the exact `observed_at` persisted to `radar_observations`.
- Added a separate annual source-fingerprint gate against persisted action
  Radar observations and action events. The fingerprint covers dataset,
  country, year, current/previous values, and emitted delta. An unchanged
  refresh is therefore suppressed before construction; a correction has a new
  fingerprint, real refresh timestamp, and hash.
- Added regressions for exact hash time, unchanged refresh suppression, and
  corrected annual magnitude emission.

Final verification:

```text
.venv/bin/python -m pytest tests/test_radar_observations.py tests/test_stories.py tests/test_signal_evidence.py -q
git diff --check
.venv/bin/python -m py_compile src/radar/actions.py src/radar/repository.py src/radar/media.py src/radar/types.py
```

Result: `149 passed, 2 warnings` in 6.01s.

## Commit

`feat(radar): normalize media and action evidence`

## Concerns

None. The action adapters intentionally require structured registry/formal
data; official-media reports remain outside confirmed action observation
creation. Action snapshot replay protection depends on the `radar_observations`
table introduced by migration 027, which is the specified persistence boundary.
