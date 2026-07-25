# GEO PULSE Production Pipeline Recovery

**Date:** 2026-07-25

**Status:** approved by the user after the production audit

## Goal

Restore GEO PULSE as a continuously updating analytical product, not merely a
large article archive. Collection, analysis, country pages, stories, semantic
search foundations, briefings, and the two-contour Early Warning Radar must all
advance from current production data without deleting historical rows.

## Production evidence

The audit at 2026-07-25 19:53 UTC established:

- 1,201,532 articles and 1,108,030 analyses exist; only 54 non-duplicate
  articles remain unanalyzed.
- The seven-day article flow is 52,938 rows from more than ninety countries.
- Analyzer latency is healthy once work is selected: p50 4.4 minutes and p90
  12.3 minutes.
- The analyzer's empty-queue fallback performs a full anti-join over the
  article and analysis histories. PostgreSQL estimates cost 304,505 and a live
  call remained active for more than ninety seconds with two parallel workers.
- `/api/v2/countries/ES` and `/api/v2/countries/ES/topics` return no bytes
  before a forty-second client timeout. The article-country compatibility view
  prevents selective country plans and signal preview fallbacks widen the scan.
- Source health is `UNHEALTHY`: 146 of 535 sources are `OK`; Telegram has no
  new data after 2026-07-23.
- `tg-collector` has restarted 4,764 times because direct MTProto connections
  to `149.154.167.51:443` time out.
- The current three-day story pool contains 926 relevant non-duplicate
  articles, but only 71 have a ready active-profile embedding.
- Story refresh repeats one three-article cluster although hundreds of
  candidates exist.
- Radar persistence updates hourly, but the public API returns zero trends.
  Four non-coverage confirmed meta trends have zero velocity and fail the
  publication gate. `action_events` and `radar_contour_links` are empty.
- The host is saturated: load 9.65 on four CPUs, 352 MiB available RAM,
  3.8/4.0 GiB swap used, disk 82% full, and 10.95 GiB reclaimable build cache.

## Design

### 1. Bounded work selection and fast read models

The analyzer must treat Redis as the primary queue. Queue connectivity failures
must not trigger an unbounded historical anti-join on every loop. The database
fallback selects a small recent candidate window through an indexable query,
then checks analysis existence for those IDs. A bounded, explicitly invoked
recovery path handles genuinely old missed rows.

Country-facing API queries must filter on a directly indexable canonical country
projection. The existing `article_country_facts` contract remains available,
but hot dossier, topics, headline, signal preview, story, and analyzer reads
must not force PostgreSQL to recompute the discovery/publisher `CASE` across the
whole corpus. New indexes are additive and production-safe.

Expensive health and list aggregates receive short server-side caching. A timed
out HTTP request must be cancelled server-side rather than continuing to consume
database workers.

### 2. Resilient collection

Telegram receives an explicit proxy configuration independent from the generic
HTTP proxy. The value is parsed into the connection format supported by
Telethon, credentials are never logged, and an invalid proxy fails with a
configuration error. Connection failure uses in-process exponential backoff so
Compose does not spin thousands of restarts.

RSS and web collection continue when individual publishers return 403, 404,
invalid TLS, or oversized content. Existing source health records remain the
source of truth; the recovery does not mark failing publishers healthy.

### 3. Complete semantic story candidate coverage

The embedding worker prepares every recent relevant, non-duplicate,
country-attributed article that is eligible for story formation, rather than
only articles already present in a cross-country thread. Preparation and
indexing remain bounded and idempotent by active profile, object, and content
hash.

The hourly story builder consumes only ready embeddings, exposes coverage in its
cycle report, and does not replace historical memberships destructively. A
bounded production backfill fills the current thirty-day story window before a
normal incremental cycle. Existing semantic/entity/topic corroboration gates
remain in force to prevent broad false clusters.

Article embeddings are the release blocker. Entity, event, and story embeddings
are prepared through the same generic job contract after article coverage is
healthy, but their absence must not block current story recovery.

### 4. Evidence-bearing two-contour radar

Radar publication must distinguish a static structural baseline from a changing
trend. The existing non-zero velocity gate remains: annual trade and voting
snapshots with zero motion must stay hidden even if that means the honest public
result is temporarily empty. Incremental cycles replay persisted action history
across the full lookback so a short generation window does not make the action
side disappear.

The action contour is populated only from persisted, attributable action facts.
Each action observation retains its source record and real period/effective
time; a dataset's loader `updated_at` is never presented as the event time.
Existing action observations are additively materialized into `action_events`
and evidence obtains both observation and action roots. The current generic
jurisdiction-wide sanctions dataset is not treated as Russia-specific action
evidence. Media/action links require a matching canonical subject and time
window; a missing action contour is reported as insufficient, never invented.

Media observations require a canonical story or analysis event key; generic
`story:<id>` and `media:coverage` subjects are internal coverage signals, not
product trends. Candidate lifecycle maintenance closes or rejects stale rows
and memberships without deleting them, and new one-day candidates are not
persisted before admission. Repeated hourly cycles must not create a new
candidate identity for unchanged input.

### 5. Safe rollout and operational headroom

Deployment uses the existing PostgreSQL volume and applies only additive,
idempotent migrations. Before and after counts are recorded for articles,
analysis, temperature, RRI, stories, story memberships, radar observations, and
radar evidence. Any decrease stops the rollout.

The release is canaried in this order:

1. query/index changes and analyzer;
2. API;
3. Telegram;
4. embedding worker and bounded backfill;
5. story worker;
6. radar worker and action loaders;
7. web.

Reclaimable Docker build cache may be pruned after the running release is
verified. Runtime images, volumes, and database data are never pruned.

## Error handling and observability

- Every loop writes one compact cycle summary with selected, processed, failed,
  skipped, and duration fields.
- Telegram logs proxy usage only as a boolean and endpoint country/host class;
  it never logs credentials.
- API endpoints expose finite timeouts and an actionable error response.
- Story logs report eligible and embedded counts for the same candidate set.
- Radar logs report created/reused/rejected candidates, member-wave velocity,
  action-event counts, and published meta count.
- Restart count, source health, API latency, and newest row timestamps are
  included in the post-deploy audit.

## Acceptance criteria

- No key worker other than an intentional deploy restart enters a restart loop.
- Telegram stores a new item or completes a healthy no-new-items cycle through
  the configured proxy.
- Analyzer fallback completes in under two seconds on production when there is
  no work and does not scan the full article/analysis histories.
- `/api/v2/countries`, a representative country dossier, country topics,
  stories, signals, health, and radar each return within five seconds warm and
  within fifteen seconds cold.
- At least 90% of eligible articles in the current three-day story pool have an
  active ready embedding after the bounded backfill.
- A fresh story cycle either creates/updates evidence-backed cross-country
  stories or reports exactly which corroboration gate rejected every candidate.
- Public radar returns evidence-backed changing trends when qualifying inputs
  exist; otherwise the empty state is supported by zero qualifying rows rather
  than a broken velocity calculation.
- Action events and contour links advance from attributable source data.
- Protected production row counts never decrease.
