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

## Commit

`feat(radar): normalize media and action evidence`

## Concerns

None. The action adapters intentionally require structured registry/formal
data; official-media reports remain outside confirmed action observation
creation. Action snapshot replay protection depends on the `radar_observations`
table introduced by migration 027, which is the specified persistence boundary.
