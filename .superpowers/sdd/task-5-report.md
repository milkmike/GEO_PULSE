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
`GEO_PULSE_SEARCH_PERF_TEST_DATABASE_URL` is not configured. Its fixture and
plan contract were updated for `article_country_facts`, including an EL PAÍS /
Reuters / unknown-discovery end-to-end attribution case and the canonical
country index expectation.

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

- The opt-in PostgreSQL execution-plan test could not run without an explicitly
  disposable DSN. The non-PostgreSQL focused suite imports and collects it, but
  cannot prove the actual planner shape in this environment.
- `web` has no configured non-interactive ESLint setup. `npm run lint` launches
  Next.js's initial configuration prompt and exits without checking files; it
  did not modify the worktree. The focused Vitest suite is green.
