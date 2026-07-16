# Story pipeline recovery and vector-search foundation

## Goal

Turn the current single resolved cross-country story into a continuously updated,
high-precision story layer without lowering the merge threshold or risking the
existing article, temperature, signal, brief, and story history. Reuse the
existing Finland HTTPS proxy and OpenRouter account for multilingual embeddings,
and persist those embeddings through the provider-neutral storage already added
for future article/person/location/event vector search.

## Production evidence

The production diagnosis on 2026-07-16 found:

- 55,208 relevant analyses in the last 30 days and 1,141 persisted country threads;
- 637 active story candidates across 48 countries;
- 193,420 possible cross-country candidate pairs, but no active pair reaching the
  current `0.65` merge threshold (the best score was `0.621597`);
- zero legacy analysis embeddings, zero active embedding profiles, zero content
  embeddings, and zero embedding jobs;
- zero canonical entities and zero canonical article mentions even though 16,625
  analyses contain legacy entity arrays;
- broad fallback threads containing 673, 449, and 256 articles over several weeks;
- duplicate story candidates where one thread ID is projected into more than one
  country after publisher attribution changes;
- an hourly thread worker that performs sequential LLM deduplication before it
  reaches the global story build.

The shortage is therefore an input-quality and orchestration problem, not a lack
of news.

## Options considered

### 1. Lower the story merge threshold

This is the fastest code change but is rejected. Current near-matches include
same-thread country duplicates and broad threads with unrelated events. Lowering
the threshold would increase visible volume by admitting known false positives.

### 2. Backfill canonical entities only

This is safe and immediately useful. A read-only simulation estimated roughly ten
complete-link story clusters after legacy entity recovery. It remains incomplete:
the legacy registry is Russia-centric, and broad trigram threads still pollute the
candidate features.

### 3. Hybrid recovery (chosen)

Restore canonical entities first, repair country-thread boundaries, add
OpenRouter embeddings through the existing Finland proxy, rebuild only a bounded
recent window, then expand the historical backfill after quality checks. This
preserves the strict merge gate while improving both recall and precision.

## Architecture and data flow

### Canonical knowledge

Seed the deterministic canonical entity registry and backfill
`article_entity_mentions` from `analysis.entities` using the existing checkpointed
`KnowledgeBackfillService`. The operation is additive and must not modify
`analysis.entities`.

After the backfill, new analyzer results must also upsert their extracted legacy
keys into canonical mentions in the same bounded processing flow. A failed mention
write is observable and retryable; it must not discard the underlying analysis.

### Embeddings

Add OpenRouter as the third configured embedding backend when neither Jina nor a
direct OpenAI key is present. Use the existing `OPENROUTER_API_KEY`, standard
`HTTPS_PROXY`, endpoint `https://openrouter.ai/api/v1/embeddings`, and model
`openai/text-embedding-3-small` at 1,536 dimensions. This matches the existing
legacy `analysis.embedding` column and is supported by OpenRouter's current
Embeddings API.

Create one active provider-neutral embedding profile. New article vectors are
written to `content_embeddings`; a compatibility projection also fills
`analysis.embedding` until the thread clusterer is migrated completely to the
provider-neutral table. Object hashes and embedding jobs keep reprocessing
idempotent.

Start with a 500-article canary, then the relevant 30-day window. Do not start a
larger historical embedding backfill until the canary verifies vector dimensions,
provider cost/usage, cluster quality, and rollback behavior.

### Country-thread hygiene

A country thread has exactly one canonical country: `threads.country_code`.
Story candidates must be grouped by `(thread_id, threads.country_code)`, not by
whatever countries appear among current member facts. A candidate may contain
only member articles whose canonical `article_country_facts.country_code` matches
the thread country. The story layer also rejects any attempted cross-country pair
whose two candidates share a thread ID.

Recent country threads are rebuilt from canonical publisher facts with embeddings
preferred and trigram matching retained only for articles without vectors. Broad
or mixed legacy memberships are preserved in the database until their bounded
replacement succeeds; no global delete occurs before validation.

### Story building and scheduling

Story candidate comparison is prefiltered by country, a 14-day activity window,
and cached semantic features before full scoring. The public merge threshold
remains `0.65`, two independent semantic features remain mandatory, and
complete-link cohesion remains mandatory.

The global story build runs after a successful recent-thread refresh, but it no
longer waits for unrelated sequential LLM copy/dedup calls. Optional LLM-generated
copy may finish asynchronously; deterministic titles and summaries keep newly
persisted stories immediately visible.

## Production rollout

1. Capture article, analysis, temperature, thread, story, and membership counts;
   create a restorable database backup and report its checksum.
2. Run the canonical knowledge stage in dry-run mode, then apply it in bounded,
   checkpointed batches.
3. Deploy and verify the live canonical-mention write path.
4. Deploy the OpenRouter embedding adapter and run a one-request connectivity and
   dimension check through Finland.
5. Create the active embedding profile and run a 500-article canary.
6. Rebuild threads/stories in dry-run or shadow mode and compare proposed clusters
   with the current production set.
7. Apply the bounded 30-day rebuild only if the quality gates pass.
8. Backfill the rest of the 30-day vectors in resumable batches, then rebuild and
   publish stories.

## Safety, rollback, and observability

- Never restart or replace PostgreSQL or Redis for this rollout.
- Never delete articles, analyses, temperature rows, signals, briefs, or existing
  stories.
- Every backfill uses a durable cursor and idempotent upsert.
- Record pre/post counts and fail if protected counts decrease.
- Preserve existing stories until replacement memberships are committed.
- Disable the active embedding profile and stop its worker to roll back vector
  generation; existing vectors may remain inert.
- Restore thread/story memberships from the pre-rollout backup if bounded rebuild
  invariants fail.
- Expose counts for pending/ready/failed embedding jobs, canonical mentions,
  candidate pairs, rejection reasons, story clusters, and worker duration.

## Testing

Implementation follows red-green-refactor. Tests must cover:

- OpenRouter embedding configuration and 1,536-dimension validation;
- canonical mention backfill and live idempotent upsert;
- one thread producing exactly one country candidate;
- rejection of same-thread cross-country pairing;
- exclusion of member articles whose canonical country differs from the thread;
- story prefilter equivalence with the existing scoring gates;
- deterministic story persistence when optional LLM copy is unavailable;
- resumable embedding and knowledge backfills;
- protected production-count invariants.

## Acceptance criteria

- Canonical registry and article mentions are non-empty and continue growing with
  newly analyzed articles.
- The embedding canary produces valid 1,536-dimensional vectors through the
  Finland proxy with no secret exposure.
- At least 95% of eligible recent articles receive ready embeddings or an explicit
  retryable/terminal job status.
- No story contains two candidates with the same thread ID or a candidate whose
  articles belong to another canonical country.
- A manually reviewed sample of proposed stories has at least 90% precision before
  publication.
- More than one current cross-country story is published without lowering the
  `0.65` threshold.
- Article, analysis, temperature, signal, and brief counts do not decrease.
- Search, stories, signals, countries, and brief APIs return HTTP 200 after rollout.

## External API basis

OpenRouter documents `POST /api/v1/embeddings`, batch inputs, provider routing,
and `openai/text-embedding-3-small` with 1,536 dimensions:
<https://openrouter.ai/docs/api/reference/embeddings>.
