# Google News Publisher Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop Google News market editions from assigning foreign publishers to the wrong country, preserve every historical row, and repair the last 90 days of country analytics using verified publisher attribution.

**Architecture:** Keep `articles.source_id` immutable as ingest provenance, add a verified `publisher_source_id` and country metadata, and expose one `article_country_facts` view that looks like a canonical source to all downstream consumers. New unknown publishers go to a durable discovery quarantine, while accepted Google News items are mapped through a curated domain registry before deduplication, analysis, or country analytics.

**Tech Stack:** Python 3.11/3.12, FastAPI, SQLAlchemy text queries, PostgreSQL 16, feedparser, pytest, Next.js 15, React 19, TypeScript, Vitest, Docker Compose.

## Global Constraints

- Never delete an existing article, analysis row, temperature point, signal, brief, thread, or story as part of attribution repair.
- Never rewrite historical `articles.source_id`, `external_id`, or `url`; provenance must remain reversible.
- Google News `gl`, `hl`, and `ceid` are discovery-market metadata, never publisher-country evidence.
- Trust a country only through the curated publisher-domain registry; ccTLD, language, and fuzzy publisher-name similarity may create candidates but cannot verify them.
- New unknown or ambiguous Google News items must be written to `article_discoveries`, never `articles`, Redis analysis queues, temperature, signals, briefs, threads, or stories.
- Direct sources and verified `site:` wrappers retain their existing country/source behavior.
- `publisher_reassigned` means the article is attributed to the registry country, not the Google News discovery country.
- The 90-day repair must classify at least 104 days of input articles because temperature uses a 14-day lookback.
- All data migrations and backfills must be idempotent, resumable, dry-run first, keyset-paginated, and committed in bounded batches.
- Additive schema changes run before code rollout; concurrent indexes must recover safely after interruption.
- Stage 1 is deployable after Tasks 1–3 and blocks new contamination without waiting for historical recomputation.

---

### Task 1: Add publisher attribution and quarantine schema

**Files:**
- Create: `scripts/migrations/024_google_news_publisher_attribution.sql`
- Modify: `data/init.sql`
- Modify: `src/db.py`
- Modify: `tests/test_schema_contract.py`
- Modify: `tests/test_postgres_migrations.py`

**Interfaces:**
- Produces: `publisher_domains`, `article_discoveries`, article publisher/geo columns, and `article_country_facts`.
- Produces: canonical view columns `article_id`, `id`, `name`, `url`, `country_code`, `source_type`, `weight`, `language`, `tier`, `state_affiliated`, and `propaganda_risk`.
- Preserves: every existing `articles.source_id`, `external_id`, and `url`.

- [ ] **Step 1: Write failing schema-contract tests**

Add assertions for migration and init-schema parity:

```python
def test_google_news_publisher_attribution_schema_contract():
    migration_sql = migration("024_google_news_publisher_attribution.sql")
    init_sql = (ROOT / "data" / "init.sql").read_text()
    for fragment in (
        "CREATE TABLE IF NOT EXISTS publisher_domains",
        "CREATE TABLE IF NOT EXISTS article_discoveries",
        "ADD COLUMN IF NOT EXISTS publisher_source_id INTEGER",
        "ADD COLUMN IF NOT EXISTS publisher_domain TEXT",
        "ADD COLUMN IF NOT EXISTS geo_country_code CHAR(2)",
        "ADD COLUMN IF NOT EXISTS geo_status VARCHAR(24)",
        "CREATE OR REPLACE VIEW article_country_facts",
        "publisher_reassigned",
        "legacy_unverified",
    ):
        assert fragment in migration_sql
        assert fragment.replace("public.", "") in init_sql

    assert "UPDATE articles SET source_id" not in migration_sql
    assert "DELETE FROM articles" not in migration_sql
```

Extend the real PostgreSQL migration test so a direct source article appears in
`article_country_facts`, while a `publisher_discovery` article without
`publisher_source_id` does not.

- [ ] **Step 2: Run schema tests and verify RED**

Run:

```bash
.venv311/bin/python -m pytest tests/test_schema_contract.py tests/test_postgres_migrations.py -q
```

Expected: FAIL because migration `024` and the new models do not exist.

- [ ] **Step 3: Add the production-safe SQL schema**

Create `scripts/migrations/024_google_news_publisher_attribution.sql` with these
contracts:

```sql
ALTER TABLE public.articles
  ADD COLUMN IF NOT EXISTS publisher_source_id INTEGER,
  ADD COLUMN IF NOT EXISTS publisher_name TEXT,
  ADD COLUMN IF NOT EXISTS publisher_url TEXT,
  ADD COLUMN IF NOT EXISTS publisher_domain TEXT,
  ADD COLUMN IF NOT EXISTS geo_country_code CHAR(2),
  ADD COLUMN IF NOT EXISTS geo_status VARCHAR(24) NOT NULL DEFAULT 'source_verified',
  ADD COLUMN IF NOT EXISTS geo_method VARCHAR(40),
  ADD COLUMN IF NOT EXISTS geo_confidence NUMERIC(4,3),
  ADD COLUMN IF NOT EXISTS geo_verified_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS resolved_url TEXT;

ALTER TABLE public.articles DROP CONSTRAINT IF EXISTS articles_geo_status_check;
ALTER TABLE public.articles ADD CONSTRAINT articles_geo_status_check CHECK (
  geo_status IN (
    'source_verified', 'publisher_verified', 'publisher_reassigned',
    'unverified', 'legacy_unverified'
  )
) NOT VALID;
ALTER TABLE public.articles VALIDATE CONSTRAINT articles_geo_status_check;

CREATE TABLE IF NOT EXISTS public.publisher_domains (
  domain TEXT PRIMARY KEY,
  publisher_source_id INTEGER NOT NULL REFERENCES public.sources(id),
  country_code CHAR(2) NOT NULL,
  status VARCHAR(16) NOT NULL CHECK (status IN ('verified', 'blocked')),
  method VARCHAR(32) NOT NULL,
  confidence NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.article_discoveries (
  id BIGSERIAL PRIMARY KEY,
  discovery_source_id INTEGER NOT NULL REFERENCES public.sources(id),
  external_id TEXT NOT NULL,
  title TEXT,
  body TEXT,
  google_url TEXT,
  published_at TIMESTAMPTZ NOT NULL,
  feed_country_code CHAR(2) NOT NULL,
  publisher_name TEXT,
  publisher_url TEXT,
  publisher_domain TEXT,
  geo_status VARCHAR(24) NOT NULL DEFAULT 'unverified',
  reason TEXT,
  raw_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  promoted_article_id INTEGER,
  UNIQUE (discovery_source_id, external_id)
);
```

Mark only broad native feeds as discovery sources without touching articles:

```sql
UPDATE public.sources
SET config = COALESCE(config, '{}'::jsonb) ||
  jsonb_build_object('feed_mode', 'publisher_discovery')
WHERE url ILIKE 'https://news.google.com/rss/search%'
  AND POSITION('site:' IN LOWER(url)) = 0
  AND name LIKE 'Google News (%) — Россия';
```

Create retry-safe concurrent indexes for `publisher_source_id`, discovery
quarantine keyset reads, and canonical publisher/external-id uniqueness. Follow
the invalid-shadow cleanup and `indisvalid/indisready` checks from migration
`022_postgres_hardening.sql`.

Create a view that emits only analytics-eligible rows:

```sql
CREATE OR REPLACE VIEW public.article_country_facts AS
SELECT
  article.id AS article_id,
  publisher.id,
  publisher.name,
  publisher.url,
  publisher.country_code,
  publisher.source_type,
  publisher.weight,
  publisher.language,
  publisher.tier,
  publisher.state_affiliated,
  publisher.propaganda_risk
FROM public.articles article
JOIN public.sources discovery ON discovery.id = article.source_id
JOIN public.sources publisher ON publisher.id = CASE
  WHEN COALESCE(discovery.config->>'feed_mode', 'publisher') = 'publisher_discovery'
    THEN article.publisher_source_id
  ELSE COALESCE(article.publisher_source_id, article.source_id)
END
WHERE article.geo_status IN (
  'source_verified', 'publisher_verified', 'publisher_reassigned'
)
AND (
  COALESCE(discovery.config->>'feed_mode', 'publisher') <> 'publisher_discovery'
  OR article.publisher_source_id IS NOT NULL
);
```

- [ ] **Step 4: Mirror schema in init and ORM models**

Add equivalent columns/tables/view to `data/init.sql`. Add `PublisherDomain`
and `ArticleDiscovery` models plus the new `Article` columns in `src/db.py`.
Keep `source_id` unchanged and do not add an ORM relationship that implicitly
rewrites it.

- [ ] **Step 5: Run migration tests and commit**

Run:

```bash
.venv311/bin/python -m pytest tests/test_schema_contract.py tests/test_postgres_migrations.py tests/test_migration_runner.py -q
```

Expected: all pass, including two consecutive migration runs.

Commit:

```bash
git add scripts/migrations/024_google_news_publisher_attribution.sql data/init.sql src/db.py tests/test_schema_contract.py tests/test_postgres_migrations.py
git commit -m "feat: add publisher attribution schema"
```

---

### Task 2: Classify feeds and build the curated publisher registry

**Files:**
- Create: `src/collectors/publisher_attribution.py`
- Modify: `src/collectors/gnews.py`
- Modify: `scripts/add_native_feeds.py`
- Modify: `scripts/collect.py`
- Modify: `src/collectors/sources_native.yaml`
- Create: `tests/test_publisher_attribution.py`
- Create: `tests/test_source_sync.py`

**Interfaces:**
- Produces: `normalize_publisher_domain(url) -> str | None`.
- Produces: `feed_mode(url, config) -> Literal['publisher','site_wrapper','publisher_discovery']`.
- Produces: `expected_site_domain(url) -> str | None`.
- Produces: `sync_publisher_domains(session) -> RegistrySyncReport`.
- Consumes: existing source catalog and the schema from Task 1.

- [ ] **Step 1: Write failing domain and feed-mode tests**

Cover IDNA, common feed prefixes, Google `site:` wrappers, native discovery,
missing source metadata, and dangerous ccTLD assumptions:

```python
def test_native_google_market_is_discovery_not_country_evidence():
    url = native_feed_url("ES", "es")
    assert feed_mode(url, {}) == "publisher_discovery"
    assert expected_site_domain(url) is None

def test_site_wrapper_has_one_expected_publisher_domain():
    url = site_wrapper_url("https://www.elpais.com/rss", "es")
    assert feed_mode(url, {}) == "site_wrapper"
    assert expected_site_domain(url) == "elpais.com"

def test_country_code_tld_does_not_create_verified_registry_entry():
    assert candidate_country_from_domain("example.me") is None
```

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv311/bin/python -m pytest tests/test_publisher_attribution.py tests/test_source_sync.py -q
```

Expected: FAIL because the attribution module does not exist.

- [ ] **Step 3: Implement deterministic domain and feed classification**

In `src/collectors/publisher_attribution.py`, define frozen dataclasses:

```python
@dataclass(frozen=True)
class PublisherMeta:
    name: str | None
    home_url: str | None
    domain: str | None

@dataclass(frozen=True)
class PublisherMatch:
    status: Literal["verified", "reassigned", "unknown", "blocked"]
    publisher_source_id: int | None
    country_code: str | None
    method: str
    confidence: float
    reason: str
```

Normalize by parsing the hostname, lowercasing, IDNA-encoding, trimming a
trailing dot, and removing only configured presentation prefixes such as
`www.`, `rss.`, `feeds.`, `feed.`, `amp.`, and `m.`. Do not infer a registrable
domain or country from a suffix. Match a host to the registry only by exact
domain or explicit alias.

- [ ] **Step 4: Emit explicit feed metadata from the native-feed generator**

Change `render_yaml()` so every generated native source contains:

```yaml
        config:
          feed_mode: publisher_discovery
          discovery_country: ES
          provider: google_news
```

Regenerate `src/collectors/sources_native.yaml` mechanically and assert all 85
broad feeds have this mode.

- [ ] **Step 5: Make source sync update config and seed verified domains**

In `ensure_sources_in_db()`, include `config`, language, tier, and trust flags in
updates for existing `(url,country)` and `(country,name)` matches. After all
sources are synchronized, call `sync_publisher_domains(session)`.

Registry seeding rules:

```python
if mode == "publisher_discovery":
    continue
domain = explicit_config_domain or expected_site_domain(url) or direct_source_domain(url)
if not domain or is_aggregator_domain(domain):
    continue
```

An unambiguous domain receives `status='verified'`, its existing source ID and
country, `method='catalog'` or `method='site_wrapper'`, and confidence `1.0`.
If one normalized domain maps to more than one country/source, upsert it as
`blocked` with conflict evidence; never choose the last row silently.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
.venv311/bin/python -m pytest tests/test_publisher_attribution.py tests/test_source_sync.py -q
```

Expected: all pass.

Commit:

```bash
git add src/collectors/publisher_attribution.py src/collectors/gnews.py scripts/add_native_feeds.py scripts/collect.py src/collectors/sources_native.yaml tests/test_publisher_attribution.py tests/test_source_sync.py
git commit -m "feat: add curated publisher registry"
```

---

### Task 3: Gate Google News intake before deduplication and analysis

**Files:**
- Modify: `src/collectors/rss.py`
- Modify: `scripts/collect.py`
- Modify: `src/pipeline/dedup.py`
- Modify: `scripts/analyze.py`
- Create: `tests/test_rss.py`
- Create: `tests/test_collect.py`
- Create: `tests/test_analyze_attribution.py`

**Interfaces:**
- Consumes: `PublisherMeta`, `publisher_domains`, `article_discoveries`, and `article_country_facts`.
- Produces: RSS article dictionaries with `publisher_name`, `publisher_url`, and `publisher_domain`.
- Produces: `classify_article(session, discovery_source, article) -> PublisherMatch`.
- Guarantees: only verified/reassigned rows enter `articles` or Redis.

- [ ] **Step 1: Write a real Google News RSS parser fixture**

Use an inline XML item containing:

```xml
<item>
  <title>El Gobierno comenta las relaciones con Rusia - EL PAÍS</title>
  <link>https://news.google.com/rss/articles/example</link>
  <pubDate>Wed, 15 Jul 2026 10:00:00 GMT</pubDate>
  <source url="https://elpais.com">EL PAÍS</source>
</item>
```

Assert `_parse_entries()` preserves the Google item URL separately from the
publisher homepage and returns normalized `publisher_domain == 'elpais.com'`.

- [ ] **Step 2: Write failing intake tests for verified, reassigned, and unknown rows**

Use one ES discovery source and three articles:

```python
assert save(el_pais).geo_status == "publisher_verified"
assert save(el_pais).geo_country_code == "ES"
assert save(reuters).geo_status == "publisher_reassigned"
assert save(reuters).geo_country_code == "GB"
assert save(unknown) is None
assert discovery_for(unknown).geo_status == "unverified"
assert queued_article_ids == [el_pais_id, reuters_id]
```

Also assert `articles.source_id` remains the ES discovery-source ID for both
accepted items and `publisher_source_id` points to EL PAÍS/Reuters.

- [ ] **Step 3: Run focused tests and verify RED**

Run:

```bash
.venv311/bin/python -m pytest tests/test_rss.py tests/test_collect.py tests/test_analyze_attribution.py -q
```

Expected: FAIL because RSS publisher metadata and gated intake do not exist.

- [ ] **Step 4: Preserve `<source>` metadata in RSS parsing**

Extend each article dictionary with:

```python
source_meta = getattr(entry, "source", None) or {}
publisher_url = source_meta.get("href") or source_meta.get("url")
publisher_name = source_meta.get("title")

{
    "publisher_name": publisher_name or None,
    "publisher_url": publisher_url or None,
    "publisher_domain": normalize_publisher_domain(publisher_url),
    "raw_source": dict(source_meta) if source_meta else {},
}
```

Do not treat `publisher_url` as the article URL.

- [ ] **Step 5: Route articles before exact/fuzzy dedup**

Select `sources.config` in `collect_all()`. In `_save_source`, classify the item
before any duplicate query:

```python
match = classify_article(session, source, art)
if match.status in {"unknown", "blocked"}:
    upsert_article_discovery(session, source, art, match)
    continue

effective_country = match.country_code or source.country_code
publisher_source_id = match.publisher_source_id
geo_status = (
    "publisher_reassigned"
    if effective_country != source.country_code
    else "publisher_verified"
)
```

For direct sources, use `source_verified`, `geo_country_code=source.country_code`
and `publisher_source_id=None`. For `site_wrapper`, require the RSS publisher
domain to equal the expected `site:` domain or quarantine it.

Check exact duplicates by `(publisher_source_id, external_id)` for discovery
items and `(source_id, external_id)` for direct items. Call
`find_duplicate(session, title_norm, effective_country, published_at)` only
after verification.
Write all publisher/geo columns with the article. Enqueue only accepted,
non-duplicate articles and pass `effective_country` to Redis.

- [ ] **Step 6: Gate both analyzer entry paths through the canonical view**

Replace the source join in `_analyze_article_by_id` and `analyze_new_articles`:

```sql
JOIN article_country_facts source ON source.article_id = ar.id
```

Select `source.name AS source_name`, `source.country_code`, and `source.weight`.
This causes queued legacy/unknown broad-feed IDs to become safe no-ops.

- [ ] **Step 7: Run Stage 1 regression tests and commit**

Run:

```bash
.venv311/bin/python -m pytest tests/test_rss.py tests/test_collect.py tests/test_analyze_attribution.py tests/test_publisher_attribution.py tests/test_schema_contract.py -q
```

Expected: all pass; the ES/Reuters/unknown triplet proves country routing.

Commit:

```bash
git add src/collectors/rss.py scripts/collect.py src/pipeline/dedup.py scripts/analyze.py tests/test_rss.py tests/test_collect.py tests/test_analyze_attribution.py
git commit -m "fix: verify Google News publishers before intake"
```

At this point Stage 1 can be deployed: new contamination is blocked even before
historical repair.

---

### Task 4: Move core calculations to canonical publisher facts

**Files:**
- Modify: `src/engine/index.py`
- Modify: `src/engine/ru_index.py`
- Modify: `src/engine/signals.py`
- Modify: `src/engine/explanations.py`
- Modify: `scripts/backfill_temperature.py`
- Modify: `scripts/retro_temperature.py`
- Modify: `tests/test_methodology.py`
- Modify: `tests/test_explanations.py`
- Modify: `tests/test_signal_evidence.py`

**Interfaces:**
- Consumes: `article_country_facts` from Task 1.
- Guarantees: source weights, tier diversity, country filters, and evidence IDs use the verified publisher.

- [ ] **Step 1: Add failing canonical-country calculation tests**

Add the ES discovery triplet to methodology/signal fixtures. Assert only EL PAÍS
contributes to ES, Reuters contributes to GB, and unknown contributes nowhere.
Assert publisher IDs—not the Google discovery source—drive source diversity and
tier convergence.

- [ ] **Step 2: Run focused calculation tests and verify RED**

Run:

```bash
.venv311/bin/python -m pytest tests/test_methodology.py tests/test_explanations.py tests/test_signal_evidence.py -q
```

Expected: FAIL while queries still join `sources` through `articles.source_id`.

- [ ] **Step 3: Replace every article-source join in the temperature path**

In current and historical temperature queries, replace:

```sql
JOIN sources s ON ar.source_id = s.id
```

with:

```sql
JOIN article_country_facts s ON s.article_id = ar.id
```

Keep the existing `s.country_code`, `s.weight`, and `s.id` expressions; the view
now gives them canonical meaning. Apply the same replacement in explanation
queries that rank evidence articles.

- [ ] **Step 4: Replace canonical-source joins in RRI and signal detectors**

Update all article-derived queries in `ru_index.py` and these detector families
in `signals.py`: tier convergence, official silence context, velocity, notable
events, and article evidence. Do not change GDELT-only queries. Where a query
starts from `sources` to count configured active official outlets, leave it on
the source catalog; where it starts from articles, join the view.

- [ ] **Step 5: Run calculation tests and commit**

Run:

```bash
.venv311/bin/python -m pytest tests/test_methodology.py tests/test_explanations.py tests/test_signal_evidence.py -q
```

Expected: all pass.

Commit:

```bash
git add src/engine/index.py src/engine/ru_index.py src/engine/signals.py src/engine/explanations.py scripts/backfill_temperature.py scripts/retro_temperature.py tests/test_methodology.py tests/test_explanations.py tests/test_signal_evidence.py
git commit -m "fix: use verified publishers in country calculations"
```

---

### Task 5: Correct search, APIs, briefs, threads, and stories

**Files:**
- Modify: `src/search.py`
- Modify: `src/api/routes/articles.py`
- Modify: `src/api/routes/world.py`
- Modify: `src/api/routes/signal_detail.py`
- Modify: `src/api/signal_article_context.py`
- Modify: `src/pipeline/briefs.py`
- Modify: `scripts/build_threads.py`
- Modify: `src/stories.py`
- Modify: `src/api/routes/stories.py`
- Modify: `tests/test_search.py`
- Modify: `tests/test_search_postgres.py`
- Modify: `tests/test_world_dossier.py`
- Modify: `tests/test_stories.py`
- Modify: `web/components/SearchResults.test.tsx`
- Modify: `web/components/SignalFeed.test.tsx`
- Modify: `web/components/SignalEvidence.test.tsx`

**Interfaces:**
- Consumes: canonical view columns using the same aliases existing serializers expect.
- Produces: country-filtered results and citations showing the verified publisher, never `Google News (…)`.

- [ ] **Step 1: Add end-to-end country and display regressions**

In backend fixtures, assert:

```python
assert [row["source_name"] for row in search(country="ES")] == ["EL PAÍS"]
assert "Reuters" not in [row["source_name"] for row in search(country="ES")]
assert all("Google News (" not in row["source_name"] for row in results)
```

For stories, signal context, headlines, and briefs, use the same triplet and
assert country counts/citations contain EL PAÍS only for ES. Frontend tests
assert the API-provided publisher name and country render unchanged and no
Google discovery-source name appears.

- [ ] **Step 2: Run focused consumer tests and verify RED**

Run:

```bash
.venv311/bin/python -m pytest tests/test_search.py tests/test_search_postgres.py tests/test_world_dossier.py tests/test_stories.py -q
cd web && npm test -- --run components/SearchResults.test.tsx components/SignalFeed.test.tsx components/SignalEvidence.test.tsx
```

Expected: backend tests fail while queries still use discovery sources.

- [ ] **Step 3: Update search and article/world APIs**

For each query that currently joins an article alias (`a`, `ar`, or
`candidate`) to `sources`, join `article_country_facts` on the article ID and
retain the existing source alias. Apply this to all country predicates and
serialized source fields in `src/search.py`, `articles.py`, and `world.py`.

Use the public article URL in this order:

```sql
COALESCE(NULLIF(ar.resolved_url, ''), ar.url) AS url
```

Continue passing the result through `safe_public_url`. Do not expose publisher
homepages as article URLs.

- [ ] **Step 4: Update signal context, briefs, and citations**

Replace article-source joins in signal detail/context and all country/article
brief queries with the canonical view. Preserve source trust/tier logic by
reading it from the view. Brief source hashes must use canonical publisher IDs
so changing a Google edition alone cannot invalidate or duplicate a brief.

- [ ] **Step 5: Update thread/story construction and APIs**

Replace article-derived source joins in `build_threads.py`, `src/stories.py`,
and story routes. Country membership, `country_count`, source diversity,
cross-country story edges, timeline labels, and story evidence must all use the
verified publisher country. Leave catalog-only queries unchanged.

- [ ] **Step 6: Run backend/frontend consumers and commit**

Run:

```bash
.venv311/bin/python -m pytest tests/test_search.py tests/test_search_postgres.py tests/test_world_dossier.py tests/test_stories.py tests/test_signal_evidence.py -q
cd web && npm test -- --run components/SearchResults.test.tsx components/SignalFeed.test.tsx components/SignalEvidence.test.tsx
```

Expected: all pass and no user-facing fixture renders a Google discovery name.

Commit:

```bash
git add src/search.py src/api/routes/articles.py src/api/routes/world.py src/api/routes/signal_detail.py src/api/signal_article_context.py src/pipeline/briefs.py scripts/build_threads.py src/stories.py src/api/routes/stories.py tests/test_search.py tests/test_search_postgres.py tests/test_world_dossier.py tests/test_stories.py web/components/SearchResults.test.tsx web/components/SignalFeed.test.tsx web/components/SignalEvidence.test.tsx
git commit -m "fix: use verified publishers across product surfaces"
```

---

### Task 6: Backfill 104 days of Google News attribution without rewriting provenance

**Files:**
- Create: `scripts/backfill_google_news_attribution.py`
- Create: `tests/test_google_news_attribution_backfill.py`

**Interfaces:**
- Produces: `run_backfill(*, since_days=104, batch_size=500, apply=False, checkpoint=None) -> BackfillReport`.
- Consumes: broad discovery rows, publisher registry, unique publisher source names, and optional already-resolved URLs.
- Preserves: `source_id`, `external_id`, `url`, and every row count.

- [ ] **Step 1: Write failing dry-run, resume, and invariant tests**

Cover:

```python
report = run_backfill(apply=False, since_days=104, batch_size=2)
assert report.updated == 0
assert report.classifiable == 2
assert article_rows_after == article_rows_before
assert source_triplets_after == source_triplets_before
```

Then apply, interrupt after one batch, resume from the DB/file checkpoint, and
assert a second full run changes zero rows. Include a duplicate publisher name
mapped to two sources and assert it remains `legacy_unverified`.

- [ ] **Step 2: Run tests and verify RED**

Run:

```bash
.venv311/bin/python -m pytest tests/test_google_news_attribution_backfill.py -q
```

Expected: FAIL because the backfill does not exist.

- [ ] **Step 3: Implement conservative exact-evidence classification**

The script defaults to dry-run and accepts only `--apply`, `--since-days`,
`--batch-size`, `--checkpoint`, and `--report`. Select rows by keyset:

```sql
WHERE source.config->>'feed_mode' = 'publisher_discovery'
  AND article.published_at >= NOW() - make_interval(days => :since_days)
  AND article.id > :last_id
ORDER BY article.id
LIMIT :batch_size
```

Build a normalized exact-name map only from verified registry sources whose
normalized source name is unique. Extract a publisher suffix only after a final
` - `, ` – `, or ` — ` separator. Accept it only on exact normalized equality;
never use fuzzy matching, language, or ccTLD.

On apply, update only:

```sql
publisher_source_id = :publisher_source_id,
publisher_name = :publisher_name,
publisher_domain = :publisher_domain,
geo_country_code = :country_code,
geo_status = :status,
geo_method = 'legacy_title_suffix',
geo_confidence = 1.000,
geo_verified_at = NOW()
```

Set unclassified broad rows to `legacy_unverified` in the same bounded batch.
Never enqueue or delete during this script. Write JSON counts by discovery
country, publisher country, status, and domain plus before/after invariants.

- [ ] **Step 4: Reconcile affected duplicates without deleting rows**

Within the same script, after classification, group affected verified rows by
canonical publisher/external ID and by normalized title within the existing
48-hour window. Keep the lowest existing article ID as parent, set later rows'
`is_duplicate/duplicate_of`, and recompute parent `reprint_count`. Do not change
article IDs, URLs, sources, analyses, or story memberships in this step.

- [ ] **Step 5: Run backfill tests and commit**

Run:

```bash
.venv311/bin/python -m pytest tests/test_google_news_attribution_backfill.py tests/test_stories.py tests/test_search.py -q
```

Expected: all pass, including crash/resume and unchanged provenance.

Commit:

```bash
git add scripts/backfill_google_news_attribution.py tests/test_google_news_attribution_backfill.py
git commit -m "feat: backfill verified Google News publishers"
```

---

### Task 7: Recompute the 90-day window and add attribution monitoring

**Files:**
- Modify: `src/engine/index.py`
- Create: `scripts/recompute_attribution_window.py`
- Create: `scripts/audit_google_news_attribution.py`
- Create: `tests/test_attribution_recompute.py`
- Modify: `docs/release/investigation-search-stories.md`

**Interfaces:**
- Produces: `calculate_temperature_at(country_code, as_of) -> dict | None` using the same methodology as current temperature.
- Produces: `recompute_window(days=90, apply=False) -> RecomputeReport`.
- Produces: JSON monitoring report with extraction, mismatch, unknown, and old/new analytics deltas.

- [ ] **Step 1: Write failing as-of and dry-run recomputation tests**

Assert the first 90-day output includes a 14-day input lookback, preserves the
same temperature primary keys, and dry-run writes nothing:

```python
report = recompute_window(days=90, apply=False)
assert report.input_days == 104
assert temperature_keys_after == temperature_keys_before
assert temperature_values_after == temperature_values_before
```

On apply, assert only values inside the 90-day window are upserted and older
points remain byte-for-byte unchanged.

- [ ] **Step 2: Run recompute tests and verify RED**

Run:

```bash
.venv311/bin/python -m pytest tests/test_attribution_recompute.py tests/test_methodology.py -q
```

Expected: FAIL because current temperature calculation is fixed to `NOW()`.

- [ ] **Step 3: Share one current/as-of temperature implementation**

Refactor `src/engine/index.py` so `calculate_temperature()` delegates to a new
`calculate_temperature_at(country_code: str, as_of: datetime, *,
exclude_backfill: bool = True) -> dict | None` function.

Parameterize `published_at <= :as_of` and the 14-day lower bound. Preserve every
weight, component, decay, clustering, and rounding rule from the current
methodology; do not copy the older divergent algorithms from
`backfill_temperature.py` or `retro_temperature.py`.

- [ ] **Step 4: Implement dry-run-first 90-day upsert**

`recompute_attribution_window.py` reads the existing distinct temperature keys
inside the window, recalculates each key, reports deltas, and on `--apply`
upserts the same `(time,country_code)` keys in batches. It never deletes points
and never creates older keys. After temperature apply, invoke existing current
RRI/signal/brief jobs and a scoped 30-day thread/story rebuild; do not call the
destructive legacy thread backfill path.

- [ ] **Step 5: Add observability and release gates**

`audit_google_news_attribution.py` outputs:

```json
{
  "publisher_metadata_coverage": 0.0,
  "verified": 0,
  "reassigned": 0,
  "unknown": 0,
  "matrix": {"ES": {"ES": 0, "GB": 0}},
  "foreign_ru_domains_in_country_analytics": 0,
  "legacy_rows_in_country_analytics": 0,
  "article_count": 0,
  "temperature_count": 0
}
```

Exit non-zero if Russian domains are attributed to another country, any
legacy/unknown row appears in `article_country_facts`, or article/temperature
counts fall below captured pre-deploy baselines.

- [ ] **Step 6: Run tests and commit**

Run:

```bash
.venv311/bin/python -m pytest tests/test_attribution_recompute.py tests/test_methodology.py tests/test_signal_evidence.py tests/test_stories.py -q
```

Expected: all pass.

Commit:

```bash
git add src/engine/index.py scripts/recompute_attribution_window.py scripts/audit_google_news_attribution.py tests/test_attribution_recompute.py docs/release/investigation-search-stories.md
git commit -m "feat: recompute verified attribution window"
```

---

### Task 8: Full verification and two-stage production deployment

**Files:**
- Modify only if needed: `docs/release/investigation-search-stories.md`

**Interfaces:**
- Consumes: reviewed commits from Tasks 1–7 and existing migration-first Docker deployment.
- Produces: Stage 1 containment followed by a separately audited historical repair.

- [ ] **Step 1: Run complete local verification**

Run:

```bash
.venv311/bin/python -m pytest -q
cd web && npm test -- --run
cd web && npm run build
```

Expected: all backend/frontend tests and production build pass.

- [ ] **Step 2: Capture production baselines and backup before Stage 1**

Record article, analysis, temperature, active-signal, brief, thread, and story
counts plus max timestamps. Run the existing database backup and verify the
archive is non-empty. Record a sample of AL/ES/KZ Google News rows and their
current source names for post-deploy comparison.

- [ ] **Step 3: Deploy Stage 1 containment**

Deploy Tasks 1–3 with migration first. Confirm:

- migration `024` recorded successfully;
- all 85 broad sources have `feed_mode=publisher_discovery`;
- the registry contains no conflicting domain marked verified;
- new verified/reassigned items enter `articles` with publisher metadata;
- new unknown items enter `article_discoveries` only;
- analyzer does not process legacy/unknown rows;
- article, analysis, and temperature counts have not decreased.

- [ ] **Step 4: Deploy canonical consumers**

Deploy Tasks 4–5 and run the attribution audit. Smoke-test search, ES/AL/KZ
headlines, one signal detail, one story detail, and a brief citation. Every
visible source must be the verified publisher; no foreign Russian publisher may
appear under another country.

- [ ] **Step 5: Run historical repair in dry-run and review deltas**

Run the 104-day attribution backfill and 90-day recomputation without `--apply`.
Save both JSON reports. Review coverage, country transition matrix, article
count invariants, temperature deltas, signal churn, brief citation changes, and
story membership changes before any writes.

- [ ] **Step 6: Apply bounded backfill and recomputation**

Apply attribution in batches of 500 with checkpoints. Run the audit after every
batch group. Then apply the 90-day temperature upsert, current RRI/signals/briefs,
and scoped thread/story rebuild. Stop immediately on an invariant failure; do
not roll back by deleting data.

- [ ] **Step 7: Final production acceptance**

Verify:

- article and temperature counts are at or above pre-deploy baselines;
- zero `legacy_unverified`/`unverified` rows appear in `article_country_facts`;
- zero known Russian publisher domains appear in foreign country analytics;
- unknown publishers are durable in `article_discoveries`;
- search and all evidence links open the selected article, not a different
  publisher;
- latest briefing timestamp advances and background jobs have no restart loop;
- proxy/OpenRouter health remains unchanged by this rollout.

Commit any release-note-only changes:

```bash
git add docs/release/investigation-search-stories.md
git commit -m "docs: record publisher attribution rollout"
```
