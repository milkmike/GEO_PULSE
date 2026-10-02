# Autonomous global monitoring

The product must discover countries from its own inventory and incoming evidence, not from an analyst's prompt. Serbia and Ethiopia are reviewed examples, not a whitelist.

Authorized: autonomous implementation and GPT-6 Sol agents; model checks remain within the existing $20 total. This change does not reset any campaign or authorize credential transfer. Jev's production 403 is still an external blocker.

## Contracts

1. A separate UN M49 country/area inventory covers the world, preserving existing names without inventing Russia-relations baselines. Russia is excluded from foreign monitoring targets.
2. Each country gets a bounded recent-publication query. Country of publisher determines collection coverage only. The global newest-30k fence cannot erase quiet countries. Counts are explicitly sampled, not totals.
3. An hourly unpaid monitor persists coverage and its own run status even when Jev is unavailable. Countries with no sources or fresh articles remain visible as gaps.
4. Versioned Jev screenings create an idempotent work queue. Event geography comes from the screening, never the publisher. Unknown geography or weak context remains an unresolved lead.
5. Context assembly retrieves related source snapshots automatically and labels lexical relationships as retrieval suggestions. It never claims causality or independent corroboration. Old reports remain dated context, not fresh events.
6. Draft generation can consume the queue within the existing persistent cap, with per-country fairness and one attempt per snapshot. Drafts stay private pending semantic/editorial review. The public API performs no model calls and cannot publish.
7. A compact coverage disclosure shows the latest successful monitor time, monitored scope, collection gaps and pipeline blockage. The map remains the first visual anchor.
8. Curated source onboarding and collector admission accept the monitoring catalog. Unknown codes and unverified publisher domains still fail closed; no new political baseline is manufactured.

## Implementation ownership

- Registry and bounded coverage loader: live_signal_store, new monitoring_registry/global_monitoring modules and tests.
- Pure candidate/context planner: weak_signal_screening, new global_signal_planner module and tests.
- Persistent queue, migration 040, worker/CLI/API integration, integration tests: root.
- Compact frontend coverage disclosure: live_signal_frontend, once API contract is fixed.

## Verification gates

Quiet-country fairness despite a dominant country; same-country unrelated reports stay separate; wrong publisher geography never becomes event geography; changed snapshots create a new version; repeated cycles preserve work state; stale/future articles excluded; zero paid budget still updates coverage; provider failure records a clear stage without dropping coverage; no automatic public release; PostgreSQL migration idempotence, existing tests, actual desktop/mobile rendering and production snapshot after deployment.

Future stages remain explicit: vetted local source discovery in gaps, archival/official context retrieval beyond the recent screened pool, semantic model verification and automatic dossier/watch updates. A populated inventory or work queue is not proof of complete worldwide news coverage.

## Verified before release

The local backend suite passed (1452 tests, 97 skipped before the last onboarding-only patch); 227 frontend tests and the production frontend build passed. A read-only production query found a 10.6s publisher scan. Disjoint direct/attributed probes reduced it to 2.8s before the new ordered publisher index. Migration 041 builds that index concurrently and recovers interrupted builds; no model requests were made for these checks. Live PostgreSQL/CI and deployment evidence are recorded after they complete.
