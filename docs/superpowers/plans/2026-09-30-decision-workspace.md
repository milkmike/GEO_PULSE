# Decision workspace implementation plan

**Goal:** Implement the six approved evidence-grounded Russian analyst widgets.
**Architecture:** Bounded background extraction with persistent budget and exact-source annotations; read-only country projection; responsive homepage workspace.
**Tech Stack:** PostgreSQL, Python/FastAPI, existing BudgetedChat, Next.js/React.
**Spec:** ../specs/2026-09-30-decision-workspace-design.md
**Execution:** User explicitly chose multi-agent GPT-6 Sol. Root integrates/reviews/releases; agents own disjoint modules. Continue approved implementation without a repeated approval gate.

## Global constraints
- Existing $3 campaign cap, within already authorized $5 aggregate; no new cap or paid calls by agents.
- Claims need exact source quotes, explicit Russia/country relation; geography of publisher is provenance only.
- No source/model/URL/date fabricated by frontend. Empty data is honest and visible.
- Preserve all unrelated changes and existing home map/search/dossier.

## Review focus
- Source changes after extraction invalidate cached annotations.
- Publication dates bound attention, collection dates do not revive old events.
- Quotes must exist in actual bounded source, not just plausible text.
- Counts of domains do not establish claim independence.
- UI country-switch races and failed API do not show wrong-country facts.

## Tasks
- [x] Extraction worker: own src/decision_extraction.py, tests/test_decision_extraction.py, scripts/migrations/037_article_decision_annotations.sql, matching data/init.sql DDL. Write validation/reservation/cache invalidation regressions before implementation; run relevant Python tests. Expose run_decision_cycle(budget_usd,campaign,max_calls=4). No migration/deploy/model execution by agent.
- [x] API: own src/decision_workspace.py, src/api/routes/decision_workspace.py, tests/test_decision_workspace.py. Implement exact contract in spec; tests use fixed now, invalid/stale/old/future evidence and publisher-vs-participant countries. Add real PG contract check. No main.py changes; root registers router.
- [x] UI: own web/components/DecisionWorkspace.tsx, its tests, web/lib/decisionTypes.ts, web/lib/api.ts, web/app/page.tsx. Native country selector and URL persistence, compact 6-widget hierarchy, exact backend fields, citation expansion, explicit unsupported claims and coverage caveats. Add to home before SortableGrid. Test request race, retry, empty, unsupported URLs, rendered language and selection.
- [x] Root integration: register route in main.py; invoke independent extractor in existing agenda worker; test isolation/failure behavior; whole-branch review; focused and full tests, build; production schema/API/UI verification after CI and deployment; bounded real extraction and original-quote checks.

## Execution ledger
- Base remote and prod both 5bbe2e2 verified. Main local contains unrelated ahead commits; isolated existing worktree reused on feat/decision-workspace.
- Three GPT-6 Sol agents completed independent schema/interface/semantics audits.
- Ruling: add precise article annotation extraction, because current sentiment/grouping fields cannot substantiate actor positions or practical effects. Cost: some cards initially report incomplete coverage while bounded extraction fills them.

- Implementation checks: 1278 backend tests passed (101 PG-dependent skipped in full unit run), 95 focused PostgreSQL/integration tests passed on isolated schemas, 189 frontend tests passed, production Next build passed. Agents cross-reviewed each other because available thread slots were exhausted.
- Review finding fixed: Jev discovery infrastructure exceptions no longer prevent independent translation and decision extraction phases.
- Review finding fixed: local publisher coverage uses indexed geography independently of article-involvement aggregation.

- Production read-only latency gate: broad extraction query exceeded15s; changed to indexed per-publisher-country pending ID batches (40/country), cache exclusion before bound. Probe234.234ms for3329 IDs; local RS coverage130.542ms. Root bootstrap selectors retain Russian publisher eligibility; extracted directions still exclude RU. Final affected PG/HTTP/worker suite44passed.

- PR31 merged and deployed at587956e. Six-widget API measured195ms forRS and78ms forZA with saved annotations; desktop/mobile browser country switching and citations verified. First4 bounded real extractions saved for$0.001268188 from existing campaign.
- Live source review found v1 omitted sanctions positions, used English actors and completed truncated clauses. Follow-up v2 strengthens explicit public-position relevance, Russian named actors, individual-claim uncertainty, affected Russian parties and country quote grounding; namespace also included in reservation hash.
- Added manual snapshot refresh and120s polling with retained evidence and honest failed-refresh state. Follow-up local verification:27 extraction/worker tests,191 frontend tests, TypeScript and Next production build passed.

- Final cross-review: API now requires current model/version for every annotation projection and coverage count; non-PG parity regression prevents extraction/API contract drift. Lowercase country URLs normalize correctly. All 46 affected API/extraction/worker tests passed on isolated PostgreSQL schemas; 7 workspace UI tests and TypeScript passed after the final URL correction.

- PR32 deployed at c65e613. Further live review showed quote presence alone does not establish country involvement: a Kaliningrad source was assigned LT/PL without naming them, and a South Africa annotation invented a practical consequence. Those two annotations were reversibly quarantined; background agendas temporarily stopped during review.
- Added one bounded Jev evidence review per positive extraction within the existing campaign. Admission requires supported headline, Russia relation and at least one country; optional summaries/explanations/positions/changes are independently removed on uncertainty. Thresholds are conservative admission rules, not calibrated truth probabilities. Original multi-country proposals suppress actor/consequence claims until per-claim country linkage exists.
- Generator output normalization accepts only one exact JSON fence and wraps original foreign participant names only when they occur verbatim in source. Country citations use existing country-name lines; Jev still checks participation. Incomplete excerpt tails cannot support optional positions/changes, even after an affirmative model decision.
- Read-only API admits only decision-annotation-v3-reviewed and permits empty optional summary/explanation without inventing fallback text. Root ran 74 affected tests with isolated PostgreSQL successfully.
- Live reviewer evidence: fabricated LT/PL country links received zero support; speculative South Africa change rejected. Reviewed Serbia gas headline retained; unsubstantiated same-contract-terms text suppressed. Other short excerpts were conservatively declined. This small sample establishes regression behavior, not general accuracy or world coverage. Full-text ingestion, a larger labeled evaluation and per-claim country provenance remain substantive limits.
- Paid work never changed the original $3 campaign cap within the user-authorized $5 aggregate. Cached generation/reviewer outputs were reused during normalization/projection fixes instead of rebilling generation. Campaign remaining after this review: $1.230553755181; conservative reservations include rejected/unknown attempts and differ from actual provider charges.
