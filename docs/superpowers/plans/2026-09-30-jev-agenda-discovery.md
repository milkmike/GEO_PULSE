# Jev agenda discovery implementation plan

**Goal:** Discover news agendas from collected articles before the RRI relevance filter, use Jev decisions to attach evidence, and expose them inside /stories.

**Approved intent:** User requested implementation after the article-first/event/agenda design and explicitly requested real Jev use plus research. Existing $5 task ceiling remains; prior conservative reservations $1.82. Allocate at most $3 to the new persistent campaign. No repeated approval gate is needed for this authorized work.

**Architecture:** A separate small article-first projection preserves the established meanings of RRI and confirmed Radar risks. Reuse article_country_facts attribution, safe public URL handling and the existing killable Jev transport. Candidate retrieval is deterministic and bounded; Jev accepts or rejects each attachment against a stable anchor. Related developments are labelled separately from reports of the same event. No transitive union of accepted edges. No LLM-written assertion of truth: card title is explicitly an example source headline; all evidence is inspectable.

**Tech stack:** Existing Python/SQLAlchemy/PostgreSQL, OpenRouter Decisions API, Next/React. No new dependencies.

## Contracts

`AgendaArticle` dict: id:int,title:str,excerpt:str,published_at:datetime,collected_at:datetime,source_id:int,source_name:str,country_code:str,url:str,content_hash:str.

Core module `src/agenda_discovery.py`: `candidate_groups(articles, existing_groups=(), max_groups=30, max_members=20)` returns list of dicts `{anchor: AgendaArticle, candidates: list[AgendaArticle], existing_id: int|None}`; no RRI/analysis eligibility. Use bounded lexical rare-token/transliteration retrieval, hard time separation, no event-specific keyword hardcoding. `prepare_pair_payload(anchor,candidates)` creates <=24KB, <=8 questions with pair keys `pair_<anchor.id>_<article.id>` and choices same_event/development/unrelated/uncertain. `parse_pair_response(data, expected_pairs)` validates exact IDs, numeric finite probability/confidence and allowed values. `accepted_decision` requires selected probability >=.80 AND confidence >=.70 and same_event/development. Numeric thresholds are provisional and replay-tested, not statistical guarantees.

API `/api/v2/agendas?limit=20&q=` returns `{items:[{id,title,article_count,source_count,countries:string[],first_seen,last_seen,updated_at,same_event_count,development_count,model,articles:[{id,title,url,source_name,country_code,published_at,collected_at,relation,confidence}]}],coverage:{last_run_at,status,articles_scanned,candidate_groups,decisions,accepted,remaining_budget_usd},has_more:boolean}`. Each item includes up to50 latest evidence articles; list max50 items. `title` is a representative source headline, not a verified summary. Country codes are publisher geography, not event participants. Read-only API, no model calls.

Storage: news_agendas stable anchor article IDs; news_agenda_articles one current group per article; news_agenda_decisions content/version-keyed cache; agenda_runs persisted coverage; agenda_budget/calls atomic campaign reservations before network requests. Migrations must also be reflected in data/init.sql. No writes to analysis, legacy threads/stories or Radar.

Worker loads last30000 IDs then limits to recent collection72h, verified publisher attribution, valid title, non-duplicates; suspicious publication dates remain visible as collection-time evidence and are not silently rewritten. Cached decisions avoid repeated paid work. PostgreSQL advisory lock prevents simultaneous workers. Commit budget reservations before external calls; killable 5s requests, no retry, <=24KB payload, one fixed Jev model, verified catalog tariff <=$0.10/M input, zero output/request fee. Unknown outcomes retain reservation. Total campaign cap $3; deployment default disabled with budget0. Persist run errors/budget exhaustion. One cycle <=20 calls, worker interval600s.

## Work units and verification

- [x] Core candidate retrieval + bounded Jev payload and parsing. Write failing synthetic tests: unrelated same country, same event across countries, changed date/event, development versus event identity, missing/invalid probabilities, anchor nontransitivity, RRI-rejected article still eligible. Owner core worker.
- [x] Persistence/budget/worker/API. Atomic budget integration tests with PostgreSQL; idempotent rerun, failures retain reservation, malformed provider response never creates membership, stale article hash invalidates cache, API exposes provisional evidence and safe URLs. Owner root.
- [x] Stories agenda panel. Show current agendas above legacy cross-country stories with clear source-headline attribution, evidence expansion and relation labels; proper empty/error/loading/budget-paused states. No huge claim of full internet coverage. Owner frontend worker.
- [x] Test all backend/frontend, independent whole-branch review, bounded live replay on actual Flydubai/Kaliningrad articles, verify unrelated cases remain separated. Record actual latency, cost and decisions; retain limited-sample caveat.
- [ ] CI/merge/standard deploy; enable budgeted agenda worker only after bounded proof. Verify live endpoint and browser with saved evidence, actual Jev model metadata and fresh run status.

## Review focus

- Reprints/publisher country must not become independent corroboration or real-world event geography.
- Conflicting claims may coexist in an agenda; model confidence is relation confidence, not truth.
- No lexical heuristic may silently substitute for a successful Jev acceptance.
- Cross-language recall is limited by retrieval; show bounded coverage, don't infer global absence.
- Concurrent/restarted workers must not exceed the persisted campaign cap or pay repeatedly for unchanged pairs.
