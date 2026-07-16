# Investigation Search & Stories: release gate

This runbook releases hybrid article search, canonical knowledge, cross-country
stories, signal evidence, RRI shift explanations, and Thermometer methodology.
The database changes are additive. Keep every UI flag off until its data and
query-plan gate has passed.

## Feature-flag contract

The integrated frontend has one tested Next.js server-only reader for all four
flags and passes an immutable boolean snapshot to client components. Search,
stories, investigation, and signal-detail entry points are wired. The flags do
not use a `NEXT_PUBLIC_` prefix and default to `false` when missing or malformed.

Flags are Docker build arguments because Next.js may prerender the root layout.
Changing a value therefore requires an explicit web image rebuild; restarting
an existing container is not sufficient:

```bash
docker compose build web
docker compose up -d web
```

| Variable | Enables | Initial value |
|---|---|---|
| `FEATURE_SEARCH_NAVIGATION` | Search entry points and `/search` navigation | `false` |
| `FEATURE_STORIES_NAVIGATION` | Stories navigation and story panels | `false` |
| `FEATURE_INVESTIGATION` | Chart markers and the RRI investigation panel | `false` |
| `FEATURE_SIGNAL_DETAIL` | Links to evidence-rich signal detail | `false` |

Flags hide entry points, not data. API v1, Thermometer v1, RRI v1, and existing
country threads remain available. The new read APIs are safe to smoke-test while
navigation is disabled. A public GET must only read persisted state; it must not
invoke an LLM, embedding provider, or cache-warming write.

## Story pagination consistency

`GET /api/v2/stories` returns an additive `consistency` object. Its mode is
`membership_generation_live_filters`: the first page reads the committed
`membership_generation`, and every cursor carries that same cutoff. Membership,
first/last activity, counts, countries, primary article URL, six-level action
score, relevance, and lifecycle priority are derived only from memberships at
or below the cutoff. The builder increments the singleton membership clock in
the same transaction that writes a complete batch, so a batch is never partly
visible. Lifecycle, confidence, topic, entity, and merge-state filters still
read current rows. This is deliberately **not** a full point-in-time snapshot;
the filtered result set can change while the membership-derived rank stays
fixed. The stories page surfaces that limitation.

Story-detail article cursors carry the same membership-generation cutoff.
Memberships committed after the first page are excluded, and ordering uses the
immutable `membership_confidence_snapshot` saved in membership evidence.
Entity and event evidence is rebuilt from only those visible memberships, not
from the live materialized story aggregate tables. List and article cursors use
a six-decimal PostgreSQL `numeric` rank key serialized as a decimal string;
decoders accept only canonical unpadded base64url, bounded positive IDs,
timezone-aware dates, and finite relevance values in the documented range.
Migration 023 backfills missing or corrupt action/confidence snapshots in
bounded, retry-safe batches, preserves a valid action level 6, and enforces the
story action scale of 1–6 before navigation is enabled.

## Before rollout

1. Take a PostgreSQL backup and record current API/web image IDs.
2. Check database free space, active connections, API 5xx rate, and p95 latency.
3. Deploy code with all four flags set to `false`.
4. Apply migrations in numeric order with fail-fast psql behavior. Apply the
   complete migration chain on a clean disposable database and apply 019–023 a
   second time before touching production.
5. Confirm `pg_trgm` and `vector` extensions, the generated article
   `search_vector`, and all new tables exist.

Do not activate real vector retrieval in this release. Lexical and structured
search must remain useful when no embedding profile/provider is available.

## Backfill

The orchestration command is read-only by default. It uses five ordered stages:

1. canonical knowledge registry and legacy entity mentions;
2. cross-country story membership from existing country threads;
3. partial evidence for legacy signals where the saved payload is sufficient;
4. durable signal-evidence metadata derived from persisted signal rows;
5. deterministic RRI explanation-cache warmup.

Every write stage is idempotent, commits bounded batches, and saves its cursor
only after the transaction commits. Before the first story write, the command
persists a high-water mark, hash of every clustering input, ordered arrays of
thread IDs for the frozen cluster plan, and the next plan index. Resume follows
that saved plan, ignores newer thread IDs, and fails closed if a frozen input
changed. Legacy signal reconstruction is labelled `partial`: saved payload
fields may be recovered, but historical thresholds and windows remain empty;
the API stores them as `{}` and `null`. The current detector rule appears only
under `explanation.current_rule_reference`; it is never historical evidence.

Run the dry-run first:

```bash
docker compose run --rm analyzer \
  python scripts/backfill_investigation_data.py --batch-size 100
```

Review the JSON summary, then run apply mode with a host-mounted checkpoint so a
replacement container can resume safely:

```bash
mkdir -p backups
docker compose run --rm \
  -v "$PWD/backups:/app/backups" \
  analyzer python scripts/backfill_investigation_data.py \
  --apply --batch-size 100 \
  --checkpoint /app/backups/investigation-backfill-checkpoint.json
```

Re-run the same command after interruption. Do not delete or edit the checkpoint
during a rollout, and do not run the country-thread builder or re-analysis before
resume: changing any frozen clustering input intentionally aborts the story
stage instead of silently changing its membership plan. Investigate the change,
then either restore the frozen inputs or start an explicitly new rollout with a
new checkpoint path. All database upserts remain idempotent.

## Google News attribution repair and monitoring

Capture immutable pre-deploy counts before the attribution backfill. Use the
same values for every audit in one rollout:

```sql
SELECT COUNT(*) AS article_baseline FROM articles;
SELECT COUNT(*) AS temperature_baseline FROM temperature;
```

Run the read-only audit before changing data and save its JSON output:

```bash
docker compose run --rm -v "$PWD:/app" analyzer \
  python scripts/audit_google_news_attribution.py \
  --article-baseline "$ARTICLE_BASELINE" \
  --temperature-baseline "$TEMPERATURE_BASELINE" \
  --report backups/google-news-attribution-before.json
```

The audit exits non-zero if a verified Russian publisher domain appears under a
foreign country, an unverified or legacy row leaks into
`article_country_facts`, or article/temperature counts fall below the captured
baselines. The report also records publisher-metadata extraction coverage,
verified/reassigned/unknown counts, the discovery-country to publisher-country
matrix, and old/new analytics count deltas. Unknown quarantined discoveries may
be non-zero; they must remain outside `article_country_facts`.

Run both historical repair commands without `--apply` first. The attribution
scan needs 104 days because the first output point in the 90-day Thermometer
window consumes a 14-day input lookback:

```bash
docker compose run --rm -v "$PWD:/app" analyzer \
  python scripts/backfill_google_news_attribution.py \
  --since-days 104 --batch-size 500 \
  --checkpoint backups/google-news-attribution-checkpoint.json \
  --report backups/google-news-attribution-dry-run.json

docker compose run --rm -v "$PWD:/app" analyzer \
  python scripts/recompute_attribution_window.py \
  --days 90 --batch-size 100 \
  --report backups/google-news-recompute-dry-run.json
```

Review the attribution matrix and every temperature delta before applying. The
recompute command reads only existing `(time, country_code)` keys in the 90-day
output window. Dry-run performs no writes. Apply mode never deletes points,
never creates an older key, and leaves `pattern_type` and every point before the
window byte-for-byte unchanged.

Apply the attribution backfill with its durable checkpoint, audit after each
batch group, and only then apply temperature recomputation:

```bash
docker compose run --rm -v "$PWD:/app" analyzer \
  python scripts/backfill_google_news_attribution.py \
  --apply --since-days 104 --batch-size 500 \
  --checkpoint backups/google-news-attribution-checkpoint.json \
  --report backups/google-news-attribution-applied.json

docker compose run --rm -v "$PWD:/app" analyzer \
  python scripts/audit_google_news_attribution.py \
  --article-baseline "$ARTICLE_BASELINE" \
  --temperature-baseline "$TEMPERATURE_BASELINE" \
  --report backups/google-news-attribution-after-backfill.json

docker compose run --rm -v "$PWD:/app" analyzer \
  python scripts/recompute_attribution_window.py \
  --apply --days 90 --batch-size 100 \
  --report backups/google-news-recompute-applied.json
```

After the bounded temperature upserts commit, the recompute command invokes the
existing current RRI, signal, and brief jobs, then a scoped additive 30-day
thread/story refresh. That refresh does not run global thread linking, dedup,
cleanup, membership replacement, story lifecycle refresh, or any delete. Old
thread/story conflicts and pre-window story evidence are excluded by the same
30-day cutoff. The command does not invoke either legacy temperature backfill
or the destructive investigation backfill path. Run the audit once more after
these jobs and stop on any non-zero exit; do not roll back by deleting rows.

## Data and product gates

Capture each result before enabling a flag.

```sql
-- Knowledge coverage and unresolved legacy rows
SELECT COUNT(*) AS mentions,
       COUNT(DISTINCT article_id) AS articles_with_mentions
FROM article_entity_mentions;

-- Only real cross-country stories should be public
SELECT lifecycle, COUNT(*) AS stories,
       MIN(country_count) AS minimum_countries
FROM stories
WHERE NOT (COALESCE(meta, '{}'::jsonb) ? 'merged_into_story_id')
GROUP BY lifecycle ORDER BY lifecycle;

-- Evidence coverage: preserve legacy partial status visibly
SELECT COALESCE(se.completeness, 'missing') AS completeness, COUNT(*)
FROM signals s LEFT JOIN signal_evidence se ON se.signal_id = s.id
GROUP BY 1 ORDER BY 1;

-- Warmed intervals and cache freshness
SELECT rri_version, evidence_completeness, COUNT(*), MAX(updated_at)
FROM index_change_explanations
GROUP BY rri_version, evidence_completeness;
```

Smoke-test the exact product flows:

- search `Путин` with `country=ES` and open the returned HTTP(S) source;
- open one story with at least two countries, then its Spain country slice;
- open a complete signal and an old partial signal;
- open an RRI shift and verify that exact changes, estimated contributions, and
  contextual correlation are three separate response fields;
- load `/api/v2/methodology/temperature` without provider credentials;
- repeat the above with embedding configuration removed: lexical results must
  remain available and `semantic_search` must be `unavailable`.

### Search candidate-window contract

Search returns and paginates a deterministic snapshot of at most 500 final
candidates. Source-driven branches first bound their upstream windows to the
newest 500 eligible non-duplicate articles per matching source, then merge and
rank those candidates into the final global window.
Full-text candidates enter through the article GIN index; the typo-tolerant
title branch runs only when fewer than ten eligible full-text candidates exist.
Topic, entity, and story branches apply the request's snapshot, country, topic,
entity, date, tier, and language predicates before their IDs reach the shared
deduplication and ranking stage.

For a country or tier filter, entity/topic/story and structured branches read
the newest 500 non-duplicate articles per matching source through
`idx_articles_source_candidates`, then calculate the unchanged hybrid score
inside that source-scoped snapshot window. The cursor's ingestion timestamp and
maximum article ID are applied inside the index scan before its per-source
`LIMIT`, so more than 500 later arrivals cannot displace the older page-two
window. A text-empty, language-only request
reads the newest 500 non-duplicate articles for that language through
`idx_articles_language_published_id`, then applies the same exact score inside
that window. Thus `sort=relevance` means relevance within the documented recent
candidate window; `sort=newest` uses the same window directly. Cursors preserve
the original ranking timestamp and ingestion high-water marks, so later pages
cannot admit articles collected after page one.

## Query-plan gate

Run plans with production-like parameters after `ANALYZE`. Save total time,
shared buffer hits/reads, returned rows, and the chosen index. Low-cardinality
development tables may legitimately use a sequential scan; production-sized
relations must not scan all articles per request.

Do not use a direct `SELECT ... FROM articles WHERE search_vector @@ ...` as the
search gate: that only proves the standalone GIN index works and cannot expose
materialization or full scans introduced by the real multi-branch ranking
query. Run the opt-in regression against a disposable PostgreSQL 16 database:

```bash
GEO_PULSE_SEARCH_PERF_TEST_DATABASE_URL="$DISPOSABLE_DATABASE_URL" \
  pytest -q tests/test_search_postgres.py -vv
```

The test creates 100,001 synthetic articles and runs `EXPLAIN (ANALYZE,
BUFFERS, FORMAT JSON)` over the exact `ARTICLE_SEARCH_SQL` for lexical, sparse
topic, language-only, canonical-entity-plus-country, and structured
entity-ID-plus-country requests. It then inserts 600 post-snapshot articles and
verifies that the older 500-row country window remains available through the
original cursor high-water marks. The gate requires the expected
GIN/language/source indexes, forbids every executed `Seq Scan` of `articles`,
caps language and source-driven aggregate/sort work at 500 rows, and enforces
the hard two-second statement budget per query. Preserve the JSON plans with
the release evidence; nodes under an unexecuted fallback branch have
`Actual Loops = 0` and do not fail the gate.

Run the remaining lookup plans separately:

```sql

EXPLAIN (ANALYZE, BUFFERS)
SELECT st.id FROM stories st
JOIN story_countries sc ON sc.story_id = st.id
WHERE sc.country_code = 'ES' AND st.country_count >= 2
ORDER BY st.last_seen DESC LIMIT 21;

EXPLAIN (ANALYZE, BUFFERS)
SELECT se.signal_id FROM signal_evidence se
WHERE se.signal_id = 1;

EXPLAIN (ANALYZE, BUFFERS)
SELECT id FROM index_change_explanations
WHERE country_code = 'ES' AND rri_version = 'v1'
ORDER BY to_time DESC LIMIT 1;
```

Investigate any real search plan that ignores the GIN search index, executes a
full article scan, or misses the snapshot-order index at production scale.
Investigate story, signal, or explanation lookups that read materially more
rows than they return. The release owner sets the latency budget from the
pre-rollout baseline; do not enable navigation if p95 or 5xx materially
regresses.

## Activation order and observation

Before step 1, verify on the integrated image that each wired flag independently
hides/shows its entry point, missing/malformed values behave as `false`, and the
four variables are present in the running web container.

1. Enable `FEATURE_SIGNAL_DETAIL`; observe 404/5xx and evidence completeness.
2. Enable `FEATURE_INVESTIGATION`; observe explanation latency, cache hit ratio,
   and missing-RRI-point responses.
3. Enable `FEATURE_STORIES_NAVIGATION`; observe empty country slices, story
   detail errors, cluster/member counts, and external-link rejection counts.
4. Enable `FEATURE_SEARCH_NAVIGATION`; observe p50/p95/p99 latency, timeouts,
   candidate counts, zero-result rate, cursor validation failures, and slow
   query logs.

Hold each step for one normal collection cycle before widening exposure. During
the hold, compare API request volume/latency, database CPU and buffers, worker
errors, and product-level empty/error rates against the captured baseline.

## Rollback

1. Set all four flags to `false`, rebuild the web image, and recreate the web
   container. This removes new navigation and panels without affecting existing
   pages or API v1.
2. Stop the manual backfill process. If a future scheduled embedding or story
   enrichment worker was enabled separately, stop it as well.
3. Roll back the API/web image only if read endpoints themselves are unhealthy.
4. Keep migrations 019–023 and all additive tables in place. Do not drop data or
   remove columns during an incident. Existing collectors and APIs do not depend
   on the flags and continue operating.
5. Preserve the checkpoint and logs for diagnosis. Resume the same apply command
   after the defect is fixed; committed batches do not need to be replayed.
