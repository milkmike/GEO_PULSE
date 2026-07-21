# Live Story and Radar Recovery Design

**Date:** 2026-07-21

**Status:** approved in conversation

## Goal

Restore continuous cross-country story formation and hourly Early Warning Radar
updates without deleting or rewriting the existing article, analysis, temperature,
signal, brief, thread, story, or radar history.

## Production diagnosis

The production audit showed that collection and article analysis are current, but
the active provider-neutral article embedding set stopped on 2026-07-16. The
embedding preparation and indexing commands are intentionally one-shot and are
not scheduled by Compose. Fresh country threads therefore have no active semantic
coverage for global story comparison.

The story scorer calculates semantic similarity, but its merge gate and final
article-membership filter require a concrete trigram match between generated
`event_key` values. Different but equivalent LLM wording therefore cannot form a
story even when the articles have strong semantic and canonical-entity evidence.
This strict gate was added to stop false-positive stories, so recovery must add a
second evidence route rather than merely lowering thresholds.

The radar worker is an opt-in, one-shot Compose profile that runs in `--shadow`
mode. The CLI only persists data with `--apply`, so no scheduled process has
updated radar observations or trends since the initial rollout.

## Chosen design

### Continuous embedding worker

Create one focused background entry point that performs an idempotent cycle:

1. enqueue missing embeddings for recent canonical-country story candidates;
2. process a bounded number of pending jobs for the active profile;
3. record a structured cycle result and sleep before the next cycle.

The worker uses the analyzer image, which already contains the embedding provider
dependencies and scripts. It runs every five minutes, uses bounded batches, and
fails closed if the configured provider metadata does not match the one active
1,024-dimensional profile. Provider failures remain retryable through the
existing embedding job state machine and never stop collection or analysis.

### Hybrid story evidence

Keep the current concrete-event route unchanged. Add a conservative semantic
route for cross-country pairs inside the existing fourteen-day time gate:

- thread semantic similarity is at least `0.86`;
- the pair shares at least one canonical entity;
- the pair shares at least one normalized topic;
- both candidates have the required active embedding coverage.

Production validation added a stricter fallback for fresh multilingual reports
whose canonical entities have not yet been extracted: semantic similarity must
be at least `0.90`, at least two normalized topics must match, and the title or
event wording must independently clear a `0.20` lexical corroboration gate.
Exact duplicate country-thread projections are collapsed before clustering and
their vector evidence is remapped to the canonical projection.

Any of the concrete-event, entity-semantic, or strict topic-semantic routes may
admit a pair. Same-
country pairs, same-thread pairs, generic event keys, and pairs outside the time
window remain rejected. The final article filter must use the same accepted pair
evidence instead of re-imposing event-key-only confirmation. Every persisted
membership retains the route, component scores, shared entities, shared topics,
and article references used for the decision.

The first production recovery is a bounded, non-destructive thirty-day story
rebuild. Existing stories remain intact; no global deletion or membership
replacement occurs during the canary.

### Scheduled radar

Extend the radar CLI with `--loop` and `--interval`. Each cycle uses a fresh UTC
`as_of`, opens a new database transaction, runs the existing replay-safe service
with `shadow=False` only when `--apply` is set, commits one complete cycle, and
sleeps. A PostgreSQL advisory lock prevents overlapping apply cycles.

The production Compose service is always enabled, runs hourly with `--apply`, and
uses `restart: unless-stopped`. Hourly cycles regenerate only the latest three
days, join those identities to the retained ninety-day observation baseline,
and never close unrelated meta-trend memberships. Shadow mode stays the default
for manual commands and audits.

## Safety and observability

- No destructive schema migration is required.
- No existing content or analytical history is deleted.
- Embedding jobs and vectors remain idempotent by profile, object, and content
  hash.
- Story recovery starts with the scoped non-destructive builder.
- Radar observations and state events keep their existing append-only/upsert
  contracts.
- Worker logs expose prepared, processed, indexed, failed, candidate, cluster,
  membership, observation, and trend counts.
- Deployment stops if protected row counts decrease, embedding profile metadata
  differs, or the canary produces implausibly broad cross-country clusters.

## Testing

Tests follow red-green-refactor and cover:

- bounded embedding cycles, loop timing, provider failure, and Compose wiring;
- event-key admission remaining unchanged;
- semantic admission requiring similarity, entity, topic, country, and time;
- rejection when any semantic corroboration input is missing;
- final article membership using the accepted semantic evidence route;
- radar loop argument validation, fresh timestamps, apply/shadow behavior,
  transaction boundaries, advisory locking, and Compose wiring;
- unchanged protected production counts and live endpoint health after rollout.

## Acceptance criteria

- Active story-candidate embeddings receive a new `updated_at` within fifteen
  minutes and continue growing with fresh relevant articles.
- The production story builder creates current cross-country stories through
  explainable concrete-event or corroborated-semantic evidence.
- No sampled story combines different concrete events, and every membership
  links to its supporting articles.
- Radar observations, trends, evidence, and state events receive current hourly
  timestamps.
- Existing articles, analyses, temperatures, signals, briefs, threads, stories,
  and prior radar history do not decrease.
- `/`, `/stories`, `/radar`, `/signals`, and their public data endpoints remain
  available after deployment.
