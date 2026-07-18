# Early Warning Radar Design

**Date:** 2026-07-18

**Product:** «Массаракш» (`massaraksh.tech`)

**Repository:** `milkmike/GEO_PULSE`

**Status:** approved product direction, implementation design

## 1. Summary

The Early Warning Radar turns the existing stream of articles, stories, signals,
country indices, and real-world indicators into one auditable trend system. It
must identify a narrative while it is still emerging, show the countries in
which it is spreading, distinguish media attention from real actions, and let an
analyst trace every conclusion to indexed evidence.

The radar has two independent contours:

1. **Media narrative contour** — what national media say about Russia, how much
   attention a thesis receives, how its stance changes, and how widely it spreads
   across independent publisher families.
2. **Real action contour** — sanctions, votes, trade and energy moves, official
   visits, agreements, diplomatic decisions, and other stored action evidence.

Each contour may progress from an early signal to a confirmed trend on its own.
When both contours independently confirm the same direction, the product marks
that the narrative is transitioning into action. Correlation is shown as a
measured alignment, not as proof of causation.

The implementation is PostgreSQL-first and additive. It preserves API v1,
Thermometer v1, RRI v1, all existing articles, analysis, historical temperature
rows, signals, briefs, threads, and stories.

## 2. Existing Foundation

The current product already supplies most of the raw building blocks:

- indexed articles, normalized publishers, country attribution, language, and
  analysis;
- lexical search, canonical entities, evidence-bearing mentions, and
  model-versioned embedding storage;
- cross-country stories and their article, country, entity, and event members;
- concrete signal evidence and country index-change explanations;
- direct-publisher coverage reporting and source freshness health;
- real-action inputs such as UN votes, sanctions, trade, energy imports, visits,
  agreements, and the composite RRI;
- a single `/world` product surface with country, signal, story, and methodology
  views.

The radar must extend these contracts instead of creating another article store,
another story system, or another country index.

The existing `analysis.action_level` is an LLM classification of a media
article. It remains useful media evidence but must not count as independent
confirmation in the real-action contour. Likewise, existing stories describe
bounded concrete events; they provide evidence for a trend but are not
cross-country meta-trends themselves.

## 3. Product Model

### 3.1 Observation

An observation is the smallest time-bound fact used by a detector. It references
one or more existing evidence records and includes:

- contour (`media` or `action`);
- country and observation time;
- normalized subject, thesis, event, and direction where known;
- measured value and expected baseline;
- source or dataset identity;
- article, story, entity, event, or action references;
- extractor and detector versions;
- confidence and data-quality metadata.

Observations do not duplicate full article text. They point to existing records
and retain only the normalized analytical facts required for replay.

### 3.2 Country wave

A country wave represents one trend in one national information or action space.
It owns its own 90-day baseline, T0, current state, direction, velocity,
confidence, source coverage, and evidence timeline.

One country may join a meta-trend later than another and may cool while the
cross-country trend remains active.

### 3.3 Cross-country meta-trend

A meta-trend groups country waves that express the same underlying subject and
direction. It is not a duplicate global cluster: the meta-trend owns a shared
analytical identity, while each country wave retains its local wording,
baseline, T0, stance, intensity, and evidence.

The meta-trend T0 is the earliest confirmed T0 among its country waves. The UI
also exposes first detection time and every later country entry point.

### 3.4 Evidence

Every state transition and every explanatory sentence must link to evidence.
Evidence may be:

- an indexed article or exact article chunk;
- an existing story;
- a canonical entity or event mention;
- a sanction, vote, trade, energy, visit, or agreement record;
- a time-series measurement;
- a source-health or collection-health measurement.

The product distinguishes direct triggering evidence, supporting evidence,
nearby context, and contradictory evidence.

## 4. Trend Lifecycle

Country waves and meta-trends use an explicit state machine:

1. `candidate` — similarity or anomaly candidate; internal only.
2. `emerging` — early signal; visible on the radar dashboard.
3. `confirmed` — independent evidence and persistence gates passed.
4. `cooling` — signal remains real but velocity and attention are declining.
5. `resolved` — no qualifying activity beyond the configured persistence window.
6. `rejected` — explained by duplication, collector failure, or insufficient
   independent evidence; retained for audit but not shown by default.

Normal emerging signals remain dashboard-only. Notifications are sent for
confirmed trends. An emerging signal may notify early only when at least one of
the following is true:

- velocity is critical;
- several countries cross their local thresholds in a short interval;
- the subject belongs to a critical policy or security class.

All state changes are append-only events. A recalculation can supersede a state
but cannot erase the previous decision.

## 5. Time Model and T0

### 5.1 Baseline

Each country wave uses a rolling 90-day baseline, calculated only from data that
would have been available at the observation time. The detector also uses a
7-day acceleration window and shorter 24-hour and 72-hour windows for velocity.

The baseline is country-local and subject-local. Weekday and source-mix effects
are normalized when enough history exists. Sparse countries fall back to a
documented hierarchical baseline using the country, language, media-system, and
global subject priors, with lower confidence.

### 5.2 Automatic T0

Automatic T0 is the earliest changepoint that remains supported by the later
confirmed regime. Online detectors identify the early warning; an offline PELT
pass refines T0 after confirmation.

Stored times are:

- `first_observed_at` — first candidate evidence;
- `detected_at` — first time the system emitted an emerging signal;
- `confirmed_at` — first time confirmation gates passed;
- `t0_auto` — algorithmic changepoint;
- `t0_effective` — currently displayed T0.

An analyst may correct `t0_effective`, but the automatic value, analyst identity,
reason, timestamp, and complete revision history remain visible and auditable.

## 6. The Two Independent Contours

### 6.1 Media narrative contour

The media contour measures more than sentiment. Candidate observations include:

- attention share relative to all successfully indexed national coverage;
- rate of new articles and new publisher families;
- semantic novelty relative to the 90-day subject history;
- stance and framing shift;
- entity, location, event, and thesis composition;
- cross-source repetition and contradiction;
- cross-country propagation velocity.

Confirmation requires persistence plus independent publisher-family evidence.
Raw article count alone can never confirm a trend.

### 6.2 Real action contour

The action contour consumes structured actions already stored by GEO PULSE and
new normalized action observations. Each action records actor, target, action
type, direction, magnitude where available, effective date, source dataset, and
evidence.

Action confirmation depends on source authority and action finality, not media
volume. One verified enacted sanction may be stronger evidence than dozens of
articles, while a reported proposal remains an early signal until independently
confirmed or formally adopted.

### 6.3 Contour alignment

Contours are aligned by canonical subject, entities, events, direction, country,
and time. Alignment produces a relationship record with its own confidence and
evidence. It never rewrites either contour's state.

Public wording:

- media only: `Нарратив подтверждён в медиаполе`;
- action only: `Изменение подтверждено действиями`;
- both: `Нарратив переходит в действия`;
- disagreement: `Медиаполе и действия расходятся`.

## 7. Detection and Grouping Stack

The detector is deliberately layered so expensive models are not applied to all
articles.

### 7.1 Identity and deduplication

1. Resolve discovery URL and verified publisher identity.
2. Normalize canonical URL and title.
3. Reuse the existing duplicate family where present.
4. Apply MinHash LSH to suppress near-duplicate syndication and minor rewrites.
5. Count publisher families rather than feed rows or republished URLs.

### 7.2 Candidate retrieval

Use pgvector HNSW to find semantically related recent articles, story centroids,
and active trend centroids. Lexical, entity, event, country, time, and stance
constraints narrow the candidate set before final scoring.

The current embedding store has no ANN index and public search does not yet mix
vector scores. Activation therefore requires a profile-specific partial HNSW
index and an explicit hybrid query adapter; it is not implied merely by stored
vectors.

### 7.3 Evidence graph

Construct a bounded weighted graph of candidate articles, stories, entities,
events, countries, and theses. Edge weights combine semantic similarity,
canonical entities/events, time proximity, stance compatibility, and publisher
independence.

Use connected components or conservative local community assignment first.
Leiden may be introduced after production evaluation shows that the simpler
assignment merges or fragments real stories.

### 7.4 Change detection

- River ADWIN and Page-Hinkley provide online early-warning candidates.
- PELT refines historical regimes and T0 after enough evidence accumulates.
- Detector thresholds are calibrated per country and metric against the rolling
  90-day baseline.

### 7.5 LLM boundary

An LLM is used only for ambiguous cluster merge/split decisions and final cached
explanations. It is not used for every article, for numeric changepoint detection,
or from a public GET request.

Every LLM run stores model, prompt version, input references, token usage, cost,
output, and review status. A deterministic fallback remains available.

## 8. Source Coverage and Collector Health

Media Cloud's strongest contribution to this design is the separation of a real
content anomaly from a collector anomaly.

The existing verified publisher registry and coverage API remain the source of
truth. They are extended with daily time series for:

- expected and observed active publisher families;
- successful fetch ratio and last fresh article;
- article volume and duplicate ratio;
- language and editorial-tier mix;
- discovery-versus-direct-publisher mix;
- country collection-health state;
- source additions, removals, and material configuration changes.

Fetch history is recorded first as append-only `source_fetch_events`, including
duration, outcome, HTTP status, fetched/new/quarantined item counts, final URL,
response size, validators, retry instruction, and classified error. Daily health
and collection-health rows are derived snapshots, not a replacement for the raw
fetch audit trail.

Publisher-family identity is persisted rather than repeatedly inferred from
mutable URLs. Existing `sources.id`, `articles.source_id`, and
`articles.publisher_source_id` remain compatibility identities. Catalog drift is
handled by retirement/deactivation, never by deleting historical sources or
rewriting article identity.

PELT detects source-volume regime changes independently from content trends.
Adaptive scheduling speeds up productive or breaking-wave feeds and slows
unchanged, duplicate-heavy, rate-limited, or repeatedly failing feeds within
safe minimum and maximum intervals.

Trend confidence is conceptually:

`semantic shift × source independence × coverage health × persistence`

Coverage health is normally a soft confidence factor. It becomes a hard
suppression gate only when the country is critically blind, for example when
more than half of expected core publisher families disappear or the observed
source mix changes sharply enough to explain the anomaly. Suppressed candidates
remain visible to operators with reason `collector_anomaly`.

## 9. Evidence Chunk Index

Article-level search remains available. For precise investigation, indexed body
text is additionally split into stable paragraph-sized chunks with:

- article ID, ordinal, source character offsets, and content hash;
- normalized text and full-text search vector;
- language;
- canonical entity, location, event, and thesis mentions;
- stance and extractor version;
- optional model-versioned embedding.

Chunks allow the product to answer queries such as “where did Spanish media
mention Putin?” with the exact matching passage and original article link. They
also provide compact evidence for trend explanations without re-sending full
articles to an LLM.

Chunk indexing is idempotent. Re-extraction creates a new version or supersedes
derived chunks without modifying the original article row.

## 10. Data Model

All schema changes are additive and mirrored in `data/init.sql`.

New logical records:

- `article_chunks` and `article_chunk_mentions`;
- `source_families`, `source_family_members`, `collections`, and
  `collection_family_memberships`;
- `source_fetch_events` plus bounded scheduling fields on existing sources;
- `radar_observations`;
- `action_events` for independently sourced real actions and their lifecycle;
- `radar_trends` for both country waves and meta-trends;
- `radar_trend_members` linking country waves to a meta-trend;
- `radar_trend_evidence` with evidence role and contribution;
- `radar_state_events` for the append-only lifecycle;
- `radar_t0_revisions` for automatic and analyst T0 history;
- `radar_contour_links` for media/action alignment;
- `source_health_daily` and `collection_health_daily`;
- `analysis_runs` for reproducible detector, embedding, and LLM runs;
- `notification_events` with idempotent delivery keys.

Existing `stories` remain the editorial/news grouping layer. A radar trend may
reference many stories over time; a story may support several analytical trends
only when the evidence roles differ. Existing `signals` remain readable and may
link to a radar trend during migration.

Stable IDs, detector versions, input windows, and baseline snapshots make every
decision replayable.

## 11. API

Additive v2 endpoints:

- `GET /api/v2/radar` — prioritized emerging and confirmed meta-trends;
- `GET /api/v2/radar/trends/{id}` — complete trend investigation;
- `GET /api/v2/countries/{code}/radar` — country waves;
- `GET /api/v2/radar/trends/{id}/timeline` — state, contour, country, and T0
  timeline;
- `GET /api/v2/radar/trends/{id}/evidence` — paginated evidence and exact chunks;
- `GET /api/v2/radar/coverage` — current collection confidence and blind spots;
- `GET /api/v2/methodology/radar` — immutable public detector, state, T0, and
  confidence contract;
- `GET /api/v2/search/articles` — extended with optional chunk evidence;
- protected analyst endpoint for T0 correction and review decisions.

Public GET endpoints only read persisted results. Detector, embedding, chunking,
and explanation work run in background jobs.

## 12. Product Experience

### 12.1 Radar page

The standalone radar page defaults to cross-country meta-trends and shows:

- concise thesis and direction;
- emerging/confirmed/cooling state;
- media and action contour states separately;
- first country, latest countries, and propagation speed;
- T0, first detection, and confirmation time;
- coverage confidence and contradiction marker;
- one concrete evidence sentence and a drill-down action.

Filters cover state, contour, country, region, topic, criticality, time, and
coverage confidence.

### 12.2 Trend investigation

The detail page answers, in order:

1. What is changing?
2. Where did it start and where is it spreading?
3. What happened in the media contour?
4. What happened in the action contour?
5. Why does the system believe this?
6. Which evidence contradicts it?
7. Is the data coverage healthy?
8. How were baseline, T0, confidence, and state calculated?

It includes a meta-trend timeline with separate country waves and direct links to
exact article chunks, source articles, stories, and action records.

### 12.3 Existing placements

- Home page: top confirmed trends plus exceptional high-velocity early signals.
- Country page: local waves and their parent meta-trends.
- Story detail: related analytical trends.
- Signal detail: related country wave or candidate explanation.
- Sources page: collection blind spots and recent health regimes.

The existing visual identity is preserved.

The current `RadarPanel` is the market/isolation widget and is not reused for
this feature. Early-warning components use unambiguous names such as
`EarlyWarningPanel`, `TrendCard`, and `TrendInvestigation`. Research subviews use
shareable query state on the same trend route (`propagation`, `evidence`,
`coverage`, and `method`) rather than creating a disconnected lab.

The navigation entry and all public placements are protected by the existing
server-side feature-flag system. The home panel precedes stories, while the
country panel sits between relationship dynamics and local stories. Existing
signal and story detail pages receive compact related-trend links rather than a
redesign.

## 13. Notifications

Notification delivery is policy-driven and idempotent. One state transition
creates at most one notification per channel and audience rule.

Default policy:

- no notification for ordinary `emerging`;
- notify on `confirmed`;
- notify early for critical velocity, rapid multi-country spread, or a critical
  subject;
- notify on media/action alignment when it materially changes the assessment;
- do not repeatedly notify while a trend remains unchanged;
- show coverage degradation as an operational alert, not as a geopolitical
  trend.

## 14. Cost and Token Policy

The radar's core detection does not require large token spending. Deduplication,
embeddings, graph assignment, baselines, ADWIN/Page-Hinkley, and PELT are
deterministic or fixed-cost operations.

Token spending is reserved for:

- ambiguous merge/split review;
- short cached trend summaries;
- optional analyst-requested synthesis.

Each run has a daily and per-trend cost cap. Repeated inputs use content hashes
and cached results. The UI exposes freshness and model version, while operations
expose aggregate token and currency cost.

## 15. Rollout and Data Safety

### Phase 1 — shadow media radar

- add schema and replayable analysis-run contracts;
- build article chunks and deterministic candidate generation;
- calculate source-health time series;
- run media country waves in shadow mode against recent history;
- expose operator-only diagnostics without notifications.

### Phase 2 — action contour and alignment

- normalize existing action datasets into observations;
- calculate independent action waves;
- link action and media contours;
- evaluate false positives and T0 quality.

### Phase 3 — public vertical slice

- expose `/radar`, trend detail, country blocks, and home block;
- enable confirmed-trend notifications;
- keep analyst T0 correction and audit history;
- monitor coverage and cost.

### Phase 4 — adaptive collection and richer research

- enable bounded adaptive polling after shadow comparison;
- add feed rediscovery for broken or moved feeds;
- add research comparisons, attention-over-time, top phrases, and corpus export.

Migrations never drop or rewrite existing product tables. Historical backfills
write only to new derived tables. Deployment updates application and worker
containers without recreating PostgreSQL volumes. Before any production
backfill, protected row counts and a database backup are recorded.

In particular, rollout never rewrites article discovery URLs, `source_id`,
`publisher_source_id`, `external_id`, or legacy uniqueness boundaries. Feed URL
changes continue to update the existing source identity. New source foreign keys
are validated against current catalog/database drift before enforcement.

## 16. Evaluation

Offline evaluation uses known historical events and a time-correct replay that
prevents future leakage. The review set includes positive events, slow narrative
builds, cross-country propagation, source outages, wire-service duplication, and
quiet periods.

Primary metrics:

- median early-warning lead time;
- precision of `confirmed` transitions;
- false confirmations caused by collector anomalies;
- country and publisher-family diversity at confirmation;
- cluster purity and fragmentation;
- automatic T0 error relative to analyst review;
- evidence-link validity;
- notification precision;
- processing latency and cost per indexed article/trend.

The first public release requires an analyst-reviewed precision threshold and a
documented failure mode for sparse countries. Recall is improved only after
confirmation quality is acceptable.

## 17. Acceptance Criteria

The first complete radar release is accepted when:

1. One cross-country meta-trend displays separate country waves with independent
   baselines and T0 values.
2. Media and action contours confirm independently and can visibly align or
   disagree.
3. A confirmed trend is supported by independent publisher families or
   authoritative action evidence, not raw article volume.
4. A source outage lowers confidence or suppresses a candidate without creating
   a false geopolitical alert.
5. Every displayed claim reaches an indexed article chunk, story, action record,
   or time-series measurement.
6. Automatic T0 can be corrected without losing the original or revision
   history.
7. Normal emerging signals stay on the dashboard, while notification policy
   handles confirmed and exceptional early signals.
8. Search can return an exact passage and original article for a person,
   location, event, or thesis query.
9. No public GET request triggers paid model work.
10. Existing articles, analysis, Thermometer history, RRI, signals, briefs,
    threads, stories, API v1, and PostgreSQL data remain intact.

## 18. Decision Record

Approved decisions incorporated here:

- two independently confirmed contours: media narratives and real actions;
- rolling 90-day baseline with a 7-day acceleration window;
- automatic, analyst-correctable T0 with audit history;
- one cross-country meta-trend with separate country waves;
- hybrid semantic, entity/event, time, stance, and graph grouping;
- MinHash LSH, pgvector HNSW, ADWIN/Page-Hinkley, PELT, and later Leiden if
  justified by evaluation;
- LLM only for ambiguity and cached explanation;
- dashboard-only ordinary early signals, confirmed-trend notifications, and
  exceptional early alerts;
- Media Cloud-inspired source registry, collection health, adaptive polling,
  evidence chunks, and reproducible analysis runs;
- PostgreSQL-first evolution with additive migrations and no replacement search
  or graph service.

## 19. External Research References

- Media Cloud Web Search: <https://github.com/mediacloud/web-search>
- Media Cloud RSS Fetcher: <https://github.com/mediacloud/rss-fetcher>
- Media Cloud Metadata Library: <https://github.com/mediacloud/metadata-lib>
- Media Cloud Feed Seeker: <https://github.com/mediacloud/feed_seeker>
- Media Cloud Sous Chef: <https://github.com/mediacloud/sous-chef>
- Media Cloud Story Indexer: <https://github.com/mediacloud/story-indexer>
- River change detection: <https://github.com/online-ml/river>
- ruptures changepoint detection: <https://github.com/deepcharles/ruptures>
- datasketch MinHash LSH: <https://github.com/ekzhu/datasketch>
- pgvector: <https://github.com/pgvector/pgvector>
- igraph Leiden implementation: <https://github.com/igraph/igraph>
