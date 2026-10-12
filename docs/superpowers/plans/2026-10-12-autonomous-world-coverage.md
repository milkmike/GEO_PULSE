# Autonomous world coverage implementation plan

> Execute with the requested GPT-6 Sol subagents; root integrates, reviews and verifies production.

**Goal:** Continuously find working local sources and surface source-grounded early observations for the entire 248-country/area catalog without analyst-supplied country prompts.

**Architecture:** Extend the existing hourly monitor, durable source research queue and exact-snapshot screenings. Automatically validate and admit discovered first-party feeds; expose preliminary observations separately from editorial hypothesis dossiers. Collection remains useful when paid analysis is paused.

**Baseline, 2026-10-12:** Production revision 14fb528b. 91 areas have a qualifying recent local sample; 149 have no configured source. Research has leads for 64 areas but cannot activate them. Countries with broken configured feeds are never claimed for research. Screenings stopped on October 3 at the existing campaign cap; the writer is disabled and only two manually released dossiers exist.

## Constraints and acceptance

- Preserve the 248-area monitoring catalog and separate 99-country RRI contract; publisher origin never becomes event geography.
- Discover and validate without paid models. Public requests never run collectors or models.
- Fetch only validated public destinations, with DNS pinning, TLS verification, same-host redirects, body/time limits and no credentials/proxy forwarding.
- Require current parseable RSS/Atom entries, publisher provenance and conflict-free attribution before collector admission. Use leases, cooldowns and idempotent writes.
- One failed publisher/provider must not remove coverage or starve other countries. Search and feed validation have separate bounded opportunities per cycle.
- Preliminary observations contain exact evidence, source/date and event country. Unsupported interpretations are not automatically published as editorial hypotheses.
- Preserve old budget ledgers. Unknown bills retain reserves; known bounded provider bills settle independently of content validity. No old campaign reset and no spend beyond the previously authorized $20 total.
- Keep the map and existing layout. Explain collection and processing stages inside the existing coverage disclosure; do not claim complete news coverage or no events from an empty sample.

## Parallel ownership

1. Source worker: source_research_store/worker, feed validation/admission modules, migration 043 and matching init schema, queue/HTTP/PostgreSQL tests. Interface: run_source_research(..., auto_activate=False), counters feeds_validated/sources_activated/admission_failures.
2. Observation worker: early_signal_observations, read-only early-signals route/store, configurable fixed campaign, exact-source and country fairness regressions. API adds observations while retaining editorial items.
3. UI worker: GlobalCoverageDisclosure, EarlySignalPanel, their types/tests. Render preliminary observations and observed country pipeline states without technical clutter.
4. Root: monitor/CLI/compose integration, budget settlement correction, CI PostgreSQL gates, integration verification, review and deployment.

## Verification sequence

- [x] Queue regression: a country with active failing/discovery-only feeds can be researched; countries rotate fairly and leases survive/recover correctly.
- [x] Network regression: private IPv4/IPv6, mixed DNS results, rebinding/redirect attempts, credentials and oversized/undated feeds fail closed; a valid first-party feed can be admitted exactly once.
- [x] Observation regression: a single fresh classified source can yield a provisional observation; stale/future/changed/uncited evidence and wrong geography are rejected; world results rotate countries.
- [x] Integration regression: zero model budget still discovers/activates sources and updates all country states; bounded sample analysis counts never imply worldwide completeness.
- [x] Budget regression: known valid charge for invalid prose releases the unused reserve exactly once; unknown or invalid bill remains reserved; overage halts; same payload cannot be paid twice.
- [ ] Full backend, disposable PostgreSQL, frontend tests, type-check and production build.
- [ ] Independent final review, live free feed probes, bounded paid screening using a fresh fixed campaign within the existing overall authorization.
- [ ] Merge and normal auto-update deployment, matching runtime revision and success marker, actual new sources/articles/observations and browser verification.

## Decisions

- Reuse the clean existing isolated worktree on a fresh branch from current main; preserve unrelated untracked research files.
- Source discovery alone is not coverage. Report activated feeds and subsequently collected publications separately.
- The user explicitly authorized autonomous implementation, agents and production release in this thread; routine implementation proceeds without another approval round.
- A one-time $20 ceiling cannot authorize indefinite paid operation. The unattended collector continues; model work pauses visibly at its durable cap.

## Pre-release evidence

- Backend: 1660 passed with disposable PostgreSQL; 19 unrelated search/story gates skipped locally and covered by CI. New integration rerun: 68 passed.
- Migration runner, source admission and budget: 63 passed on PostgreSQL 16. Source queue follow-up: 7 passed. Observation SQL was exercised with a non-UTC session and fractional timestamps.
- Frontend: 242 tests passed; type checking and production build passed.
- Independent review found and fixed source-search starvation, repeated admission criteria on polling, world-page starvation and stale screening rows hiding current evidence.
- Live free probes found working feeds for Diari d’Andorra, Le Matinal and CANA; CANA was intermittent. A successful probe does not prove collector admission or ongoing coverage.
- Model tariff/catalog check succeeds through the existing production gateway. No paid requests in preparation. New fixed account early-signals-2026-10-12-world is capped at $2 within the prior $20 total authorization. Historical ledgers remain unchanged.
- Observation headlines enter the existing background translation pipeline even without a Russia keyword. Machine translations are identified in the UI.
