# Autonomous source research

An all-world inventory must lead to work on missing local sources without an analyst naming countries. The coverage monitor already identifies three different gaps. This addition creates durable research work and finds bounded publisher leads using Wikidata's documented public API. Leads are unverified metadata, never active collector sources or evidence for a public hypothesis.

## Contracts

- Upsert one task per country/gap from every monitor. Distinguish no active sources, no successful direct fetch, and no qualifying recent sample. Resolve tasks when the gap disappears; reopen a returned gap without losing earlier leads. Fair ordering puts never attempted countries before retries; seven-day retry cooldown, interrupted leases recover.
- Only new-source tasks invoke discovery. Existing source repairs/freshness gaps remain separate tasks. Discovery is unpaid and bounded to four countries per hourly cycle, five publisher leads per country and a short request/time limit; rate limiting pauses the cycle.
- Search country entities by exact ISO alpha-2, verify P297, then search newspaper/news agency entities with that country's P17. Validate claims, retain Wikidata provenance/revision, and record safe public website URLs as unverified leads. Do not fetch those websites, fabricate RSS URLs, infer trust/ownership/independence or promote candidates into sources.yaml.
- API failure records a content-free blocked task and preserves coverage and model work. Model budgets, production key location and public dossier release gates stay unchanged.
- Existing vetted SourceCandidate/feed/domain checks remain the admission path. A global gap/research queue is not proof that all countries already have usable sources.
- Monitoring-only country selection filters early hypotheses separately from the legacy country overview and map. It must never submit unsupported codes to the RRI/decision-workspace routes or manufacture a score. Empty results offer a concise return to world signals.

## Ownership

- live_signal_store: persistent source research store, migration 042/init schema, PostgreSQL tests.
- weak_signal_screening: bounded Wikidata discovery adapter, pure/HTTP mocked tests.
- root: monitor orchestration/integration tests, live unpaid probe, CI/deployment and rendered coverage verification.
- live_signal_frontend: coverage row actions and separate signal-country selection, URL persistence and focused frontend verification.

## Verification

All-country gap creation without prompts; gap lifecycle and cooldown fairness; mismatched ISO/country/class claims rejected; unsafe URLs rejected; fixed endpoint/no arbitrary website fetch; rate limits stop further requests; duplicate leads remain idempotent; API outage cannot lose a coverage snapshot; no model calls and no source activation. Run PostgreSQL gates in CI, then verify production research tasks/leads and coverage timestamp.

Primary documentation: https://www.wikidata.org/wiki/Wikidata:Data_access and https://www.mediawiki.org/wiki/API:Search. An unpaid live ISO-code search returned Andorra's Q228 from the official API on 2026-10-02.

## Verified before release

The full local backend suite passed: 1483 tests, 97 optional tests skipped. The final bounded adapter found two Andorran publisher leads through the official API; this was unpaid and did not activate either source. Queue/orchestration review found no release-blocking issue; PostgreSQL and frontend gates still need the final CI result before merging.
