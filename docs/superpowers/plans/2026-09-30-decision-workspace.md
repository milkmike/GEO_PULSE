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
- [ ] Root integration: register route in main.py; invoke independent extractor in existing agenda worker; test isolation/failure behavior; whole-branch review; focused and full tests, build; production schema/API/UI verification after CI and deployment; bounded real extraction and original-quote checks.

## Execution ledger
- Base remote and prod both 5bbe2e2 verified. Main local contains unrelated ahead commits; isolated existing worktree reused on feat/decision-workspace.
- Three GPT-6 Sol agents completed independent schema/interface/semantics audits.
- Ruling: add precise article annotation extraction, because current sentiment/grouping fields cannot substantiate actor positions or practical effects. Cost: some cards initially report incomplete coverage while bounded extraction fills them.

- Implementation checks: 1278 backend tests passed (101 PG-dependent skipped in full unit run), 95 focused PostgreSQL/integration tests passed on isolated schemas, 189 frontend tests passed, production Next build passed. Agents cross-reviewed each other because available thread slots were exhausted.
- Review finding fixed: Jev discovery infrastructure exceptions no longer prevent independent translation and decision extraction phases.
- Review finding fixed: local publisher coverage uses indexed geography independently of article-involvement aggregation.

- Production read-only latency gate: broad extraction query exceeded15s; changed to indexed per-publisher-country pending ID batches (40/country), cache exclusion before bound. Probe234.234ms for3329 IDs; local RS coverage130.542ms. Root bootstrap selectors retain Russian publisher eligibility; extracted directions still exclude RU. Final affected PG/HTTP/worker suite44passed.
