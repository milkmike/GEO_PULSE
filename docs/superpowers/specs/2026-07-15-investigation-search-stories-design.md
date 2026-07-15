# Investigation Search and Stories Design

**Date:** 2026-07-15  
**Product:** «Массаракш» (`massaraksh.tech`)  
**Repository:** `milkmike/GEO_PULSE`  
**Related system:** `milkmike/GEO-LAB`

## 1. Summary

This design turns the existing dashboard from a collection of indicators into a connected investigation workflow:

1. Find an indexed article by text, entity, topic, country, or time range.
2. Open the country, signal, or story connected to that article.
3. Select a shift on the RRI chart and see what changed the calculation.
4. Separate measured contribution from nearby news context.
5. Drill into a cross-country story and reach every available primary article URL.

The work preserves the current dark editorial design, API v1 compatibility, Thermometer v1 behavior, and RRI v1 behavior. It adds new v2 APIs and additive database migrations.

## 2. Current Baseline

The repository and production server were audited before this design was written.

- Production runs the same `main` commit as the repository.
- Indexed articles already contain title, body, source, country, publication time, sentiment, action level, `event_key`, topics, and extracted entity keys where analysis succeeded.
- Country-scoped narrative threads already exist in the database and API.
- The country page requests those threads inside the «Динамика отношений» component, but the feature is visually secondary and disappears when the response is empty.
- Signals exist as a list, but their current payload and UI do not consistently expose triggering evidence or a durable detail view.
- Article embedding helpers exist, but the schema and migrations do not provide a complete, model-versioned semantic index.
- `GEO-LAB` defines useful analyst scopes and explainability contracts, but its graph repository is currently mock/in-memory and its current vector score is based on hashed lexical tokens rather than production embeddings.

Source health is a separate operational concern. Search and story screens must state that results cover the articles actually collected and indexed; they must not imply complete coverage of the global media field.

## 3. Goals

### 3.1 Hybrid article search

Provide a durable page where a user can search indexed news and reach the original article. A query such as «Путин» filtered to source country «Испания» must return matching indexed articles with direct URLs, highlighted evidence, and relevant metadata.

### 3.2 Explain RRI shifts

Let a user select a point or marked shift on a country chart and open one investigation panel containing:

- the exact change in stored RRI components;
- estimated event contributions where counterfactual calculation is possible;
- nearby news context clearly separated from measured contribution;
- related signal, story, and primary article links.

### 3.3 Explain the Thermometer

Add a two-level methodology section to «О проекте»: a plain-language explanation first, then an expandable technical description with the current criteria, formula, time window, coefficients, example, and limitations.

### 3.4 Make signals concrete

Every signal card must say what changed, by how much, where, over which period, and with what confidence. Every signal must have a detail route with its trigger, evidence, chart context, related story, and articles.

### 3.5 Restore stories as a first-class product

Create a global stories page, a drill-down page for every story, a stories block on the home page, and a country-specific stories block on every country page. A story may span multiple countries.

### 3.6 Prepare real semantic retrieval

Introduce canonical people, organizations, locations, and events; evidence-bearing mentions and graph edges; model-versioned embedding storage; and a provider-neutral ranking contract. Lexical search must remain fully functional when no embedding provider is configured.

## 4. Non-goals

- Replacing PostgreSQL with OpenSearch, Meilisearch, or a graph database.
- Enabling live semantic retrieval in the first release.
- Importing `GEO-LAB` mock graph data or its hashed-vector implementation into production.
- Changing the current Thermometer v1 or RRI v1 formula.
- Claiming that temporal proximity proves real-world causation.
- Reprocessing GDELT samples as locally indexed full-text articles when their full article record is unavailable.
- Redesigning the visual identity of the site.
- Fixing the separate production source-health backlog as part of this feature package.

## 5. Product Principles

1. **Primary evidence first.** Every article result and evidence item links to its stored source URL when a valid HTTP(S) URL exists.
2. **Measured contribution is not contextual correlation.** The UI never mixes the two labels.
3. **No hidden paid work.** Public GET requests never trigger an LLM call. Story summaries are generated in background jobs and cached.
4. **Explain every rank.** Search and timeline results expose why they were included, their relevance, confidence, and evidence.
5. **Progressive disclosure.** A reader gets a concise answer first and can expand the calculation, sources, and limitations.
6. **Graceful degradation.** Search works without embeddings, stories work without an LLM summary, and investigation works when only component-level attribution is available.

## 6. Terminology and Scopes

The public Russian UI uses these terms:

- **Новость / article:** one indexed article with a source URL.
- **Сюжет / story:** a cross-country evolving collection of related articles and events. This maps to `narrative` in `GEO-LAB` compatibility responses.
- **Событие / event:** a real-world occurrence inside a story, normally supported by one or more articles.
- **Сущность / entity:** a canonical person, organization, location, or event reference.
- **Сигнал / signal:** a detector output identifying a measurable anomaly or change that requires review.
- **Расследование / investigation:** the joined explanation view for a chart shift, signal, or story.

Supported retrieval scopes are:

- `article`
- `country`
- `story` with compatibility alias `narrative`
- `entity`
- `event`

## 7. Information Architecture

### 7.1 Global navigation

- Add a persistent search action labelled «Поиск новостей» to the header.
- Add «Сюжеты» as a first-class navigation item.
- Keep «Сигналы» as its own destination.
- Preserve the existing remaining navigation items.

On every screen size, the header shows a search icon and the label «Поиск новостей». Activating it opens `/search` and focuses the query field. Submitting produces a shareable `/search?...` URL.

### 7.2 Routes

- `/search`
- `/stories`
- `/stories/[id]`
- `/signals/[id]`
- existing `/country/[code]`, enhanced with stories and investigation
- existing `/about`, enhanced with methodology

All selected filters and the search query are encoded in the URL so a result view can be copied and reopened.

## 8. Search Experience

### 8.1 Inputs

The first release supports:

- free text query;
- source country;
- topic;
- canonical entity;
- date range;
- source tier;
- language;
- sort by relevance or newest.

The default date range is the latest 90 days. The user can expand it to all indexed history.

### 8.2 Result card

Each result displays:

- article title;
- highlighted matching fragment from title, summary, or body;
- source name and source country;
- publication time and language;
- topics and matched canonical entities;
- sentiment and action level when available;
- related story when available;
- direct primary article URL;
- `whyIncluded`, `relevanceScore`, `confidence`, and evidence details in an expandable area.

Invalid or missing URLs render as non-clickable evidence records rather than unsafe links.

### 8.3 Ranking v1

The search service exposes component scores instead of one opaque score. Initial ranking uses:

- lexical match: 35%;
- canonical entity match: 25%;
- topic match: 15%;
- freshness: 10%;
- source trust: 10%;
- story relevance: 5%.

An exact canonical-entity match outranks a body-only lexical match. Title matches receive a lexical field boost over summary and body matches. The rank weights are configuration constants with regression tests.

PostgreSQL full-text search uses the `simple` configuration to avoid assuming one language. `pg_trgm` provides typo-tolerant fallback when the full-text query returns too few candidates. Empty queries are allowed only when at least one structured filter is present.

### 8.4 Pagination and limits

- default page size: 25;
- maximum page size: 100;
- query length: 2–200 characters after normalization;
- server-side timeout budget: 2 seconds for the indexed query;
- stable pagination order uses rank, publication time, and article ID.

## 9. Global Stories

### 9.1 Story model

A story is cross-country by default and contains:

- stable ID and public slug;
- Russian title and optional English title;
- concise summary;
- lifecycle state;
- first and last observation time;
- participating countries;
- topics and canonical entities;
- member events and articles;
- related signals and RRI shifts;
- article count, source count, and country count;
- clustering confidence;
- generation metadata and source hash for any LLM-written copy.

Lifecycle states are:

- `emerging` — зарождается;
- `developing` — развивается;
- `escalating` — обостряется;
- `cooling` — затухает;
- `resolved` — завершён.

A resolved story can reactivate when new matching evidence crosses the clustering threshold. Reactivation is recorded in the timeline rather than silently changing history.

### 9.2 Clustering

Existing country threads and analyzed articles provide candidates. Membership is determined before any LLM summarization.

Candidate similarity uses:

- normalized `event_key` similarity;
- canonical entity overlap;
- topic overlap;
- publication-time proximity;
- country and source diversity;
- title similarity as a fallback.

A cross-country merge requires a score of at least `0.65` and at least two independent supporting features. Articles separated by more than 14 days are not merged automatically unless the existing story is explicitly reactivated by a matching event and entity set.

LLM use is limited to title, summary, dynamics, and forecast copy after membership has been fixed. The prompt receives only story member evidence. The stored source hash prevents unnecessary regeneration.

### 9.3 Stories page

`/stories` lists active stories first and supports filters by:

- country;
- topic;
- lifecycle state;
- entity;
- period.

Each card shows title, lifecycle, countries, latest update, article and source counts, highest action level, linked signals, and the latest relevant RRI shift.

### 9.4 Story detail

`/stories/[id]` contains:

1. concise evidence-based summary;
2. chronological event and article timeline;
3. participating countries and their different media tone;
4. canonical people, organizations, and locations;
5. linked signals;
6. linked RRI shifts;
7. full paginated article list with primary URLs;
8. clustering explanation and confidence;
9. visible limitations when coverage is incomplete.

### 9.5 Story placements

- Home page: 5–7 most active stories ranked by recency, velocity, action level, source diversity, and country breadth.
- Country page: stories connected to that country, with the country-specific tone and article count.
- Signal page: the story explaining the broader context of the signal.
- Search result: related story link when membership exists.

## 10. Unified Investigation for RRI Shifts

### 10.1 Interaction

The country RRI graph displays markers for meaningful changes. Selecting a marker or a time interval opens the «Единое расследование» panel next to the graph on desktop and as a bottom sheet on mobile.

The selected state is reflected in the URL with country code and timestamp so it can be shared.

### 10.2 Exact calculation section

«Что изменило расчёт» shows:

- previous and selected RRI value;
- exact total delta;
- exact structural component delta;
- exact media component delta;
- exact event boost delta;
- formula version and calculation timestamps;
- article and GDELT counts used by that calculation.

### 10.3 Estimated event contribution

When article-level inputs are available, the service recomputes the media/event result without one event cluster. The difference is labelled «оценочный вклад события». It is never shown as an exact causal fact.

The API includes the counterfactual method, input IDs, confidence, and any reason an estimate could not be produced.

### 10.4 Context section

«Что происходило рядом» contains stories, signals, and articles from the selected period. Default context window is 48 hours around a point; a selected interval uses its exact boundaries.

Every contextual item states why it is nearby or relevant. Context is never added to the exact contribution subtotal.

## 11. Signal Detail and Concrete Home Cards

### 11.1 Signal evidence contract

New signals persist their evidence at creation time:

- detector name and version;
- threshold and observed value;
- baseline value and comparison window;
- country or countries;
- supporting article IDs, story IDs, and RRI timestamps;
- confidence and evidence completeness;
- human-readable explanation fields.

### 11.2 Home card

A signal card answers in one glance:

- what changed;
- by how much;
- where;
- during which period;
- why it matters;
- confidence;
- related story when present.

Generic detector labels remain secondary metadata rather than the headline.

### 11.3 Signal detail page

`/signals/[id]` shows:

- concrete summary;
- detector rule and observed values;
- chart with the compared baseline and event window;
- supporting evidence;
- related story and countries;
- primary article links;
- known limitations;
- creation, expiration, and current active state.

Old signals without persisted evidence use available current context and display «частичные доказательства». They do not fabricate missing trigger inputs.

## 12. Thermometer Methodology on About Page

### 12.1 Plain-language layer

The visible section explains:

- which country-source articles enter the Thermometer;
- how relevance to Russia is determined;
- what positive and negative sentiment mean;
- why fresh material weighs more than old material;
- how source weight, event type, action level, and repeated coverage affect the result;
- that repeated articles and event clusters are damped to prevent one newswire event from dominating;
- the 14-day analysis window;
- how the Thermometer differs from the broader RRI;
- that the output is an analytical estimate, not an objective fact.

### 12.2 Technical layer

An expandable section presents the exact current v1 constants and formula from the engine, not a separately maintained approximation. It includes:

- input eligibility rules;
- time-decay function;
- source, event, and action-level coefficients;
- event-cluster diminishing rule;
- aggregation and normalization;
- anomaly and trend calculation;
- one worked example;
- model and prompt versions involved upstream;
- known biases and coverage limitations.

The implementation must source formula values from a shared backend methodology definition or a generated API response so documentation cannot silently diverge from production code.

## 13. Canonical Entity and Graph Foundation

### 13.1 Entity kinds

Canonical entity kinds are:

- `person`;
- `organization`;
- `location`;
- `event`.

The `GEO-LAB` compatibility layer maps `organization` to `org`, `location` to `place`, and global story to `narrative`.

### 13.2 Stable identity

Each entity has a UUID, kind, canonical name, normalized name, localized labels, aliases, optional country codes, provenance, and lifecycle timestamps. Public graph IDs are stable strings such as `person:<uuid>` or `location:<uuid>`.

Aliases are unique within an entity kind after normalization unless an explicit ambiguity record exists. Ambiguous aliases do not auto-resolve without contextual evidence.

### 13.3 Evidence-bearing mentions

An article-to-entity mention stores:

- article ID and entity ID;
- exact text span when available;
- character offsets when available;
- extraction method and version;
- confidence;
- evidence payload;
- creation time.

Graph edges store source, target, relation, confidence, evidence references, and optional validity dates. An edge without evidence cannot be presented as a verified relationship.

### 13.4 Relationship to the existing registry

The current curated Russian-orbit entity registry remains an extraction input. A migration maps its keys to canonical entity rows. Existing `analysis.entities` JSON remains readable for backward compatibility while new processing writes normalized mentions as well.

## 14. Vector-ready Embedding Foundation

### 14.1 Storage

Create:

- `embedding_profiles` for provider, model, dimensions, task, version, and active status;
- `content_embeddings` for profile, object type, object ID, content hash, vector, generation status, and timestamps;
- `embedding_jobs` for idempotent background indexing and retry state.

Supported embeddable object types are `article`, `entity`, `event`, and `story`.

The embedding column uses pgvector without a global fixed dimension. When a real profile becomes active, deployment creates a profile-specific partial ANN index with an explicit cast to that profile's dimensions. This allows model migration without overwriting or mixing incompatible vectors.

### 14.2 Provider-neutral interface

The embedding provider implements:

- `profile()` — model metadata and dimensions;
- `embed_documents(texts)`;
- `embed_query(text)`;
- health and usage reporting.

No search route imports a specific provider. The ranker receives an optional semantic score from an adapter.

### 14.3 Ranking contract

Every candidate may contain:

- `lexicalScore`;
- `entityScore`;
- `topicScore`;
- `vectorScore`;
- `graphScore`;
- `temporalScore`;
- `trustScore`;
- `finalScore`.

In the first release `vectorScore` is absent and its configured weight is redistributed among lexical, entity, and topic components. When semantic retrieval is enabled, the same response contract and UI remain valid.

### 14.4 GEO-LAB interoperability

GEO_PULSE is the source of truth for indexed content, canonical entities, stories, events, evidence, and embeddings. GEO-LAB consumes these through stable APIs and may apply analyst-specific reranking.

Compatible responses expose:

- `scope`;
- `whyIncluded`;
- `relevanceScore`;
- `confidence`;
- `evidence`;
- stable node IDs;
- source and freshness metadata.

GEO-LAB mock graph records and hashed vectors are not synchronized back into GEO_PULSE.

## 15. Database Changes

All changes are additive and mirrored in `data/init.sql`.

### 15.1 Search

- stored generated article search vector over title, summary, and body using the PostgreSQL `simple` text-search configuration;
- GIN full-text index;
- trigram indexes for normalized title and selected entity aliases;
- indexes for publication date, country through source, language, topics, and story membership.

### 15.2 Stories and evidence

- `stories`;
- `story_articles`;
- `story_countries`;
- `story_entities`;
- `story_events`;
- `signal_evidence`;
- `index_change_explanations` as an auditable cache keyed by country, comparison timestamps, and RRI version.

### 15.3 Entities and embeddings

- `canonical_entities`;
- `entity_aliases`;
- `article_entity_mentions`;
- `knowledge_edges`;
- `embedding_profiles`;
- `content_embeddings`;
- `embedding_jobs`.

Every join table uses foreign keys and uniqueness constraints. Article deletion cascades through mentions and memberships; canonical entities, stories, and evidence records retain independent provenance.

## 16. API Design

New endpoints:

- `GET /api/v2/search/articles`
- `GET /api/v2/stories`
- `GET /api/v2/stories/{story_id}`
- `GET /api/v2/countries/{code}/stories`
- `GET /api/v2/signals/{signal_id}`
- `GET /api/v2/countries/{code}/index-explanation`
- `GET /api/v2/entities/suggest`
- `GET /api/v2/entities/{entity_id}`
- `GET /api/v2/methodology/temperature`

Background/admin operations use protected write endpoints or workers. No public GET endpoint creates a story summary, calls an embedding provider, or starts an LLM generation.

### 16.1 Search parameters

`/api/v2/search/articles` accepts:

- `q`;
- `country`;
- `topic`;
- `entity_id`;
- `from` and `to`;
- `tier`;
- `language`;
- `sort` (`relevance` or `newest`);
- `cursor`;
- `limit`.

### 16.2 Common explainability response

Search, story timeline, entity timeline, and signal evidence items share:

```json
{
  "scope": "article",
  "whyIncluded": "Точное упоминание сущности «Владимир Путин» в заголовке",
  "relevanceScore": 0.94,
  "confidence": 0.91,
  "evidence": [
    {"type": "text_span", "article_id": 123, "text": "...Путин..."}
  ]
}
```

Field names use lower camel case in GEO-LAB compatibility responses and existing snake case in native GEO_PULSE responses. Serializers derive both from one domain object.

## 17. Error Handling and Empty States

- Invalid filters return `422` with the invalid field and accepted values.
- Unknown story, signal, or entity IDs return `404`.
- Search timeouts return a retryable `503` with no partial fabricated results.
- A missing embedding provider is not an error; the response reports `semantic_search: unavailable` and uses deterministic ranking.
- A story without an LLM summary uses a deterministic headline and evidence list.
- A chart point without enough inputs shows component deltas that are available and explains which attribution could not be computed.
- Empty result pages suggest removing individual filters and show the actual indexed coverage period.
- External links are emitted only after HTTP(S) validation.

## 18. Performance and Operations

- Search and list endpoints use cursor pagination.
- Expensive counterfactual RRI explanations are cached by input hash and RRI version.
- Story clustering runs in the existing background worker family, not in API requests.
- Story summarization and embedding jobs are idempotent and retryable.
- Worker queues expose pending, succeeded, failed, and dead-letter counts.
- Metrics include search latency, empty-result rate, candidate count, clustering confidence, unclustered article count, story freshness, explanation cache hit rate, and embedding job status.
- Logs include stable request, story, signal, and entity IDs without storing raw user identifiers.

## 19. Accessibility and Responsive Behavior

- Search, story filters, chart markers, investigation drawers, and signal cards are keyboard accessible.
- Every chart-driven action has a textual list alternative.
- Color is never the only carrier of direction, severity, or confidence.
- Motion respects `prefers-reduced-motion`.
- The desktop investigation side panel becomes a bottom sheet on narrow screens and retains focus management.
- Tooltips are also available by focus and tap.

## 20. Testing Strategy

### 20.1 Unit tests

- query normalization and validation;
- lexical, entity, topic, freshness, trust, and combined ranking;
- trigram fallback behavior;
- story similarity, merge threshold, lifecycle, and reactivation;
- exact RRI component delta;
- counterfactual event contribution labelling;
- evidence completeness and old-signal fallback;
- canonical alias ambiguity handling;
- embedding job idempotency and provider absence fallback.

### 20.2 Integration tests

- each migration on an empty and populated PostgreSQL database;
- search filters and cursor pagination;
- story list, detail, country slice, and article URLs;
- signal detail evidence;
- index explanation cache and version key;
- methodology output matching engine constants;
- v1 API compatibility.

### 20.3 UI and flow tests

- «Путин» plus Spain returns an indexed article and primary URL;
- a country chart shift opens the unified investigation panel;
- exact contribution and context are visually separated;
- a home signal opens a durable signal detail route;
- a home story opens its story detail and full source list;
- the same cross-country story appears in each participating country slice;
- methodology is readable in simple form and expandable to the technical form;
- keyboard and mobile flows work.

### 20.4 Production verification

- backfill against a database copy;
- query latency and query-plan review;
- limited production backfill with metrics;
- API smoke tests without LLM or embedding side effects;
- frontend build and HTTP smoke tests;
- rollback by disabling new routes and workers while leaving additive tables intact.

## 21. Rollout

1. Add schema, domain contracts, deterministic search, and tests.
2. Backfill article search vectors and canonical mentions.
3. Add story aggregation and story APIs while preserving existing threads.
4. Add signal evidence and RRI explanation APIs.
5. Add search, stories, signal detail, investigation, and methodology UI.
6. Run production backfill and enable routes behind server-side feature flags.
7. Observe metrics, then expose navigation and home-page blocks.
8. After this package passes its acceptance criteria, schedule a separate semantic-search release that activates the first real embedding profile and its profile-specific ANN index.

Feature flags are server-side and default off until their data backfill and smoke checks pass. Existing pages continue to function when flags are off.

## 22. Acceptance Criteria

The package is complete when all of the following are true:

1. A user can search «Путин», filter Spain, and open matching indexed source articles.
2. Search results explain why each item matched and do not claim unindexed GDELT samples are searchable full articles.
3. A meaningful RRI shift can be selected and opens one investigation panel.
4. The panel separately shows exact component changes, estimated event contributions, and contextual news.
5. The About page documents the current Thermometer criteria at both plain and technical levels.
6. Home-page signals contain concrete measured facts and open durable detail pages.
7. A global stories page exists and every story has a drill-down timeline and primary article list.
8. A cross-country story appears as one story globally and as a country-specific slice on every participating country page.
9. The home page and every country page expose a visible stories block.
10. Canonical entity, mention, graph-edge, and embedding schemas support article, person, location, event, and story retrieval without requiring embeddings at runtime.
11. GEO-LAB can consume stable scopes, node IDs, and explainability fields without depending on mock graph data.
12. Public GET endpoints never trigger paid LLM or embedding work.
13. Existing API v1, Thermometer v1, and RRI v1 behavior remain compatible.

## 23. Likely Implementation Areas

Backend and data:

- `src/db.py`
- `src/entities.py`
- `src/embeddings.py`
- `src/engine/index.py`
- `src/engine/ru_index.py`
- `src/engine/signals.py`
- `src/api/routes/world.py`
- new focused search, story, entity, and investigation domain modules and routers
- additive migrations after `018`
- `data/init.sql`
- existing analyzer and thread workers

Frontend:

- `web/components/SiteHeader.tsx`
- `web/app/page.tsx`
- `web/app/country/[code]/page.tsx`
- `web/app/signals/page.tsx`
- `web/app/about/page.tsx`
- new search, stories, story-detail, and signal-detail routes
- focused components for search results, story cards, story timeline, and investigation panel
- `web/lib/api.ts` and `web/lib/types.ts`

Tests and operations:

- expanded Python unit and integration tests
- frontend build/type checks and flow tests
- migration and backfill scripts
- compose worker commands only where existing workers cannot safely host the new jobs

## 24. Decision Record

Approved product decisions:

- hybrid lexical plus structured search for the first release;
- one unified investigation panel for chart shifts;
- exact calculation and contextual correlation shown separately;
- a standalone global stories page and story detail pages;
- story blocks on the home page and every country page;
- one cross-country story rather than country duplicates;
- plain and technical Thermometer methodology layers;
- PostgreSQL-first implementation with no new search service;
- vector-ready schemas and contracts aligned with GEO-LAB, without importing its mock graph or hashed-vector behavior.
