# Investigation Search & Stories: release gate

This runbook releases hybrid article search, canonical knowledge, cross-country
stories, signal evidence, RRI shift explanations, and Thermometer methodology.
The database changes are additive. Keep every UI flag off until its data and
query-plan gate has passed.

## Feature-flag contract

This runbook defines the required integration contract. The four flags become
operational only when the frontend release from Tasks 8/9 includes a tested
Next.js server-side reader and Docker Compose runtime pass-through. This backend
worktree intentionally does not implement that frontend wiring; the final
release gate remains blocked until the integrated branch proves it. The flags
must not use a `NEXT_PUBLIC_` prefix and must default to `false` when missing or
malformed.

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

## Before rollout

1. Take a PostgreSQL backup and record current API/web image IDs.
2. Check database free space, active connections, API 5xx rate, and p95 latency.
3. Deploy code with all four flags set to `false`.
4. Apply migrations in numeric order with fail-fast psql behavior. Apply the
   complete migration chain on a clean disposable database and apply 019–021 a
   second time before touching production.
5. Confirm `pg_trgm` and `vector` extensions, the generated article
   `search_vector`, and all new tables exist.

Do not activate real vector retrieval in this release. Lexical and structured
search must remain useful when no embedding profile/provider is available.

## Backfill

The orchestration command is read-only by default. It uses four ordered stages:

1. canonical knowledge registry and legacy entity mentions;
2. cross-country story membership from existing country threads;
3. partial evidence for legacy signals where the saved payload is sufficient;
4. deterministic RRI explanation-cache warmup.

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

## Query-plan gate

Run plans with production-like parameters after `ANALYZE`. Save total time,
shared buffer hits/reads, returned rows, and the chosen index. Low-cardinality
development tables may legitimately use a sequential scan; production-sized
relations must not scan all articles per request.

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id FROM articles
WHERE search_vector @@ websearch_to_tsquery('simple', 'путин')
ORDER BY COALESCE(collected_at, published_at) DESC, id DESC
LIMIT 101;

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

Investigate any search plan that ignores the GIN search index or snapshot-order
index at production scale. Investigate story, signal, or explanation lookups
that read materially more rows than they return. The release owner sets the
latency budget from the pre-rollout baseline; do not enable navigation if p95 or
5xx materially regresses.

## Activation order and observation

Before step 1, verify on the integrated image that each flag independently
hides/shows its entry point, missing/malformed values behave as `false`, and the
four variables are present in the running web container. Do not proceed using
this backend-only worktree.

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

1. Set all four flags to `false` and restart the web server. This removes new
   navigation and panels without affecting existing pages or API v1.
2. Stop the manual backfill process. If a future scheduled embedding or story
   enrichment worker was enabled separately, stop it as well.
3. Roll back the API/web image only if read endpoints themselves are unhealthy.
4. Keep migrations 019–021 and all additive tables in place. Do not drop data or
   remove columns during an incident. Existing collectors and APIs do not depend
   on the flags and continue operating.
5. Preserve the checkpoint and logs for diagnosis. Resume the same apply command
   after the defect is fixed; committed batches do not need to be replayed.
