# Search integrity implementation plan

> **For agentic workers:** Use superpowers:executing-plans task by task.

**Goal:** Restore trustworthy article retrieval and relevance admission before building the analyst research workspace.

**Architecture:** Keep the existing local PostgreSQL search and bounded candidate pool. Preserve explicit query operators, union entity and text matches, filter before candidate limits, and expose the limits honestly. Reuse the curated company registry for admission.

**Tech Stack:** Python, PostgreSQL, FastAPI, Next.js, Vitest.

**Spec:** The user-approved audit in this conversation: search must not silently change query meaning, lose unannotated entity mentions or historical matches, mislabel match confidence as truth, or hide candidate truncation.

## Global constraints

- No paid model calls or historical reprocessing in this release.
- Preserve v1 API and thermometer.
- Preserve publisher attribution, duplicate exclusion, snapshot pagination and bounded queries.
- Keep the current visual identity; do not enable unfinished feature flags.
- Existing unrelated worktree files stay untouched.

## Review focus

- Fuzzy/story expansion must not reintroduce terms excluded by an explicit query.
- Date, language, topic and entity filters must apply before publisher candidate caps.
- A text mention must remain searchable while entity analysis is pending.
- Candidate saturation must remain visible on later pages; it is not an archive total.
- A body/summary match must show the matching passage, without rendering source HTML.

### Task 1: Retrieval correctness and admission

**Files:** `src/search.py`, `src/pipeline/filter.py`, `tests/test_search_integrity_postgres.py`, `tests/test_search.py`, `tests/test_search_postgres.py`, `tests/test_pipeline_recovery.py`.

**Interfaces:** Existing `normalize_query`, `SearchQuery`, `search_articles`, `is_relevant`; additive `candidate_limit_reached` response field.

- [x] Add real PostgreSQL regressions for quoted phrases, exclusions (including fallback branches), lexical matches without entity annotations, old filtered rows, and matching body snippets. Run them against the old implementation and observe failures.
- [x] Preserve quotes, unary minus and OR; disable broad expansions for explicit query syntax. Keep plain entity names eligible for both lexical and entity retrieval.
- [x] Apply all structural predicates inside publisher candidate selection before LIMIT. Keep snapshot bounds before LIMIT.
- [x] Select snippets from the actually matched field; combine fields if a query spans fields.
- [x] Add company alias and Russian sanctions-form admission regressions; use bounded curated company aliases and a sanctions stem.
- [x] Run retrieval correctness plus existing 100k-row PostgreSQL plan tests and Python search/pipeline tests. Commit passing changes.

### Task 2: Honest search UI and candidate limits

**Files:** `src/api/routes/search.py`, `web/lib/types.ts`, `web/app/search/page.tsx`, `web/components/SearchResults.tsx`, search API/UI tests.

**Interfaces:** Additive `candidate_limit_reached: boolean`; existing `confidence` remains compatible but is labeled as match confidence.

- [x] Add a regression showing candidate saturation survives API serialization and pagination, and a UI test showing a bounded result set is not presented as exhaustive.
- [x] Expose candidate saturation conservatively, including branch limits, and show a request to narrow the filters.
- [x] Replace factual credibility wording with match confidence, explain country-of-source filtering, and show supported quote/minus/OR syntax.
- [x] Run frontend tests, typecheck and build; commit.

### Task 3: Verify and release

- [x] Run backend suite with isolated PostgreSQL gates and all frontend checks.
- [x] Get one independent whole-branch review; fix material findings with regression tests.
- [ ] Create and attach a PR, pass CI, then release under the user's standing implementation/deployment authorization.
- [ ] Verify the deployed revision and a bounded live search request; report measured limitations rather than extrapolating a single request.

## Subsequent product increments

The saved research case, competing hypotheses, exact evidence passages, exports and watchlist delivery remain separate product increments. Morphology and cross-language retrieval need a labeled quality benchmark and index design; this release does not claim to implement them.
