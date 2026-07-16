# Task 5 report — verified publishers across product surfaces

## Task 5A: search, article/world APIs, and signal evidence

### Status

DONE (split scope assigned by the coordinator)

Base commit: `3371c717f97876c5ffe436d6eae77acc46dbb437`

Task 5A covers search, article/world APIs, signal article context/detail, and
their focused backend/frontend regressions. Brief, thread, and story work is
intentionally excluded for Task 5B.

### Outcome

- Article-derived publisher metadata and country filtering now come from
  `article_country_facts`, so verified publishers such as EL PAÍS and Reuters
  replace Google discovery-source attribution.
- Unverified discovery articles, which have no canonical fact row, do not enter
  these product surfaces.
- Search retains global snapshot, country/tier, structured-filter, ordering,
  and candidate-limit behavior while selecting canonical facts by article ID.
- Search and public article APIs prefer `resolved_url`, fall back to the stored
  article URL, and pass API URLs through `safe_public_url` where serializers
  expose them.
- Catalog-only source queries remain unchanged.
- Global world-headline diversity partitions by canonical publisher ID rather
  than immutable discovery-source ID.

### Files

- `src/search.py`
- `src/api/routes/articles.py`
- `src/api/routes/world.py`
- `src/api/routes/signal_detail.py`
- `src/api/signal_article_context.py`
- `tests/test_search.py`
- `tests/test_search_postgres.py`
- `tests/test_world_dossier.py`
- `tests/test_signal_evidence.py`
- `web/components/SearchResults.test.tsx`
- `web/components/SignalFeed.test.tsx`
- `web/components/SignalEvidence.test.tsx`

### TDD evidence

#### RED

The six new focused backend regressions were run before production changes:

```text
6 failed, 2 warnings in 0.61s
```

They exposed direct joins through `articles.source_id`, raw article URL
selection, and Google discovery-source attribution in search, article/world
feeds, and signal evidence.

#### GREEN

The same six regressions after implementation:

```text
6 passed, 2 warnings in 0.54s
```

Final focused backend suite:

```text
79 passed, 1 skipped, 2 warnings in 0.66s
```

Command coverage:

```text
tests/test_search.py
tests/test_search_postgres.py
tests/test_world_dossier.py
tests/test_signal_evidence.py
```

The skipped test is the existing opt-in real-PostgreSQL search/EXPLAIN test;
`GEO_PULSE_SEARCH_PERF_TEST_DATABASE_URL` was not configured during this
initial focused run. Its later review follow-up is recorded below.

Final focused frontend suite:

```text
3 test files passed
34 tests passed in 0.72s
```

The fixtures assert API publisher names/countries render unchanged and no
`Google News (` discovery name appears.

### Self-review

- No Task 5B brief/thread/story production or test file was changed.
- No scoped product query joins an article directly to `sources`.
- The remaining `LEFT JOIN articles a ON a.source_id = s.id` in
  `articles.py` belongs to the catalog-only source listing endpoint and was
  intentionally preserved.
- All article links added to public serializers are validated; publisher
  homepage URLs are never substituted for article URLs.
- `git diff --check` passed.

### Concerns

- `web` has no configured non-interactive ESLint setup. `npm run lint` launches
  Next.js's initial configuration prompt and exits without checking files; it
  did not modify the worktree. The focused Vitest suite is green.

### Review follow-up: per-publisher caps and production-shaped EXPLAIN

Commit `0c0032010dc0f82beab6c9c20b9f15f825121c09` received a read-only review.
It found that the first canonical search rewrite accidentally changed a
per-source pre-filter cap into one global post-filter cap, and that the opt-in
fixture modeled `article_country_facts` as an indexed table instead of the
production view.

Both Important findings were corrected:

- country/tier candidates are capped inside a bounded lateral query per
  candidate publisher, with snapshot and duplicate predicates before the cap;
- `ROW_NUMBER() PARTITION BY s.id` uses the publisher ID validated by
  `article_country_facts`, never the discovery feed ID;
- topic, entity, date, and language filters remain after that publisher cap;
- `matching_sources` is `NOT MATERIALIZED`, so lexical/entity predicates may
  be pushed through the canonical view;
- the PostgreSQL fixture now defines the production-shaped base columns and
  canonical view, plus only real production article indexes. The invented
  `article_country_facts` table/index was removed.

Follow-up TDD RED evidence:

```text
missing per-publisher ranking contract: 1 failed, 2 warnings
materialized canonical source contract: 1 failed, 2 warnings
production-shaped PostgreSQL plan: failed on the old artificial index contract
```

Focused unit GREEN evidence during the fix:

```text
per-publisher pre-filter and service SQL contracts: 2 passed, 2 warnings
```

The opt-in test was then run against an ephemeral local PostgreSQL 16 database
with `pg_trgm`, not skipped:

```text
tests/test_search_postgres.py -q
1 passed in 1.74s
```

This real run covered the EL PAÍS / Reuters / unknown-discovery attribution
triplet, resolved URL selection, snapshot exclusion, post-cap structured-filter
cardinality, selective lexical/topic/language indexes, bounded candidate plan
nodes, and all six two-second execution thresholds.

Final fresh Task 5A verification after the review fixes:

```text
backend: 80 passed, 1 skipped, 2 warnings in 0.46s
frontend: 34 passed in 1.11s
```

The single skip is the same opt-in PostgreSQL test in the environment-free
focused command; its explicit ephemeral-PostgreSQL run passed as shown above.

PostgreSQL may still choose a sequential article scan for the country branch
instead of guaranteeing `idx_articles_source_candidates` or
`idx_articles_publisher_source_id`. The production-shaped 100k-row fixture met
the two-second SLA, but a future performance task should evaluate a composite
publisher candidate index on production-scale distributions rather than encode
one planner choice as a correctness requirement.
