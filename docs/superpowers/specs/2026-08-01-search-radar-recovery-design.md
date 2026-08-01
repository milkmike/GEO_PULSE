# Search and Radar Recovery Design

## Goal

Restore production article search on the current PostgreSQL corpus and allow
radar media candidates to accumulate enough durable history to advance through
the existing lifecycle, without deleting collected data or exposing unverified
candidates publicly.

## Scope

The change covers two independent failure modes:

1. Full-text article search times out because PostgreSQL satisfies the
   `published_at` ordering through the date index and filters the full corpus
   instead of using `idx_articles_search_vector`.
2. The radar discards media candidates and their generated observations during
   every apply cycle, preventing the 90-day lifecycle from accumulating
   persistence evidence.

The thermometer, API v1, source collection, article analysis, embeddings, and
story clustering remain unchanged.

## Search Design

The SQL pipeline will gain a materialized lexical-ID stage containing only the
article ID and full-text predicate. This stage has no date ordering and lets
PostgreSQL use the GIN search-vector index. The existing candidate stage will
join those IDs back to `articles`, verified publisher facts, analysis, entity,
date, language, and snapshot filters before applying the existing newest-first
bound and ranking logic.

This preserves current search semantics, filters, cursor snapshots, resolved
publisher URLs, and the 500-candidate ranking bound. The two-second statement
timeout stays in place so a planner regression remains visible instead of being
hidden by a larger timeout.

Regression tests must demonstrate that the materialized stage exists, that the
production-shaped PostgreSQL plan uses `idx_articles_search_vector`, that no
full `articles` sequential scan executes for lexical search, and that results
and snapshot pagination remain stable.

## Radar Design

The persistence gate will continue to exclude synthetic `story:*` subjects and
the generic `media:coverage` subject. It will stop excluding genuine media
waves solely because their state is `candidate`.

Candidate observations, country waves, evidence, and applicable meta membership
will therefore be written idempotently under the existing unique identities.
On later three-day generation cycles, the existing 90-day history lookup can
reload them, recompute persistence and velocity, and promote qualified waves
through `emerging` and `confirmed` using the current lifecycle rules.

Public API behavior does not change: candidate and rejected trends remain
hidden by default and cannot be opened through the public trend endpoint.
Existing confirmed trends, analyst T0 overrides, evidence, and contour links
are preserved.

Regression tests must prove that a one-day candidate is persisted internally,
that a subsequent incremental cycle reuses its stored history, and that the
public API still excludes candidate rows.

## Deployment and Backfill

Deployment uses the existing main-branch auto-deploy path. No schema migration
is required.

After deployment, run a 90-day shadow radar cycle first and inspect state
distribution, evidence validity, protected-table deltas, and contour
completeness. If the audit is consistent, run one 90-day apply cycle, then
return the hourly worker to its three-day incremental generation window.

The apply is additive and idempotent. It must not truncate or replace articles,
analysis, temperature, stories, existing radar evidence, or analyst overrides.

## Acceptance Criteria

- A representative public article query returns HTTP 200 within the existing
  two-second database statement timeout.
- The full-text PostgreSQL plan uses `idx_articles_search_vector` and does not
  scan the full articles table sequentially.
- Search filters, verified publisher attribution, resolved URLs, and cursor
  snapshots retain their existing behavior.
- A genuine media candidate and its observations are persisted during apply.
- Repeating the radar cycle is idempotent and allows stored candidate history
  to participate in the 90-day lifecycle.
- Candidate and rejected radar trends remain absent from default public API
  responses.
- The 90-day radar audit reports valid evidence and zero negative deltas for
  protected product tables.

## Rollback

The code deployment can be rolled back to the prior image. Candidate rows
written by the new version are safe to retain because the previous version
already ignores candidates in public output. No collected or calculated
historical data needs to be deleted during rollback.
