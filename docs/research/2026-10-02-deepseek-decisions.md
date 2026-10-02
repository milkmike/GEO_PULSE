# DeepSeek decisions: implementation and activation

## Result and boundary

The shared decision layer replaces Jev in early-change screening, news triage,
article-pair relations, and evidence review. It supports DeepSeek through
OpenRouter or the native DeepSeek API. Native Gemini 3.1 Flash-Lite is an
explicitly selected reserve; Jev remains selectable for comparison.

This change does not migrate the separate translation, annotation-writing,
embedding, sentiment, or early-signal writing clients. Those clients still need
their own working provider access. Screening results are review leads, and
draft hypotheses still require editorial publication.

On 2026-10-02 production was running commit `c57d3c9`. Authenticated OpenRouter
key inspection returned HTTP 403. Neither `DEEPSEEK_API_KEY` nor `GEMINI_API_KEY`
was configured in the server environment or the early-signals container. An
unauthenticated request to the native DeepSeek model catalog returned 401,
establishing reachability, not successful authentication. No paid replacement
calls were made during this implementation.

## Provider selection

Set the same `DECISION_PROVIDER` for `api`, `agendas`, and `early-signals`.
Compose propagates the selection. Direct keys go only to background workers.
Public reads never call a model.

| Selection | Requested model | Credential |
| --- | --- | --- |
| `openrouter` (default) | `deepseek/deepseek-v4-flash` | `OPENROUTER_API_KEY` |
| `deepseek` | `deepseek-flash` | `DEEPSEEK_API_KEY` |
| `gemini` | `gemini-3.1-flash-lite` | `GEMINI_API_KEY` |
| `jev` | `typesafe/jev-1.13` | `OPENROUTER_API_KEY` |

Native `deepseek-flash` currently serves V4.1 Flash. The earlier 11-pair V4
comparison is a small exploratory sample and does not establish the accuracy
of the native V4.1 route or the new prompts. Official aliases and current
prices must be checked again before native activation.

Changing an OpenRouter model name does not resolve a gateway 403. Use a valid
native key or restore authorized gateway access. Keep keys on the server;
never copy them into this document, logs, command arguments, or chat.

## Grounding and abstention

- Chat scores are stored as `confidence_kind=self_reported` with empty
  `probabilities`. Native Jev distributions retain their original contract.
- Each supported chat answer must quote its supplied article exactly. Pair
  relations must quote both articles. A shared topic, actor, or country alone
  cannot establish the same event or a development of it.
- Positive pair relations require a score of at least .90 and grounded
  evidence. The store checks the quotes again before attaching memberships.
- Country choices see the complete allowed catalog rather than a shortlist
  based on a literal English/Russian keyword. Publisher origin does not
  establish event geography. A quote identifying a different country cannot
  establish the chosen country; Russia-only evidence cannot establish the US.
- A high-scoring country nomination using an unfamiliar local spelling or
  city remains provisional: `uncertain=true` and
  `country_evidence_unverified=[code]`. Public leads already have
  `needs_review` status; that private marker is not a separate public badge.
- Missing, malformed, truncated, invented, or cross-article evidence yields
  abstention or rejection. An unresolved primary country cannot be rescued
  by a secondary-country prediction.

Model and prompt versions enter source keys, pair caches, current membership
checks, and private planning queues. Old Jev results are retained in storage
but are not relabeled or reused as DeepSeek results. **Changing the selection
invalidates current old-model lists. Do not deploy the cutover before provider
access and a bounded first pass are ready.** Published reviewed dossiers are
not rewritten by this change.

## Requests and accounting

Requests have fixed destinations, no redirects, no retry or automatic paid
provider fallback, a 24KB input envelope, a 35KB chat envelope, a 4000-token
output limit, a 160KB response limit, and a total child-process deadline up to
45 seconds. Credentials travel to the private HTTP child over stdin only.
Thinking is disabled for DeepSeek; Gemini usage includes returned thinking
tokens. No model tool calls or web-search tools are enabled.

Each call commits a $.10 reservation before network I/O. OpenRouter routing
enforces per-million ceilings of $1 input/$2 output and no request fee. A known
overage, including one from a rejected model answer, reaches the ledger and
halts the account. Unknown bills, failures, and timeouts retain the full
reservation. Native APIs do not report exact dollar costs here; their token
usage is recorded, but no fictional exact bill or refund is created.

Reviewed native standard prices on 2026-10-02:

- DeepSeek Flash: uncached input $0.15 off-peak/$0.30 peak; output $0.60/$1.20
  per million tokens. JSON requests use `thinking.type=disabled`.
- Gemini 3.1 Flash-Lite: text input $0.25; output including thinking $1.50
  per million tokens. No grounding tools are requested.

Sources: [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/),
[DeepSeek release and aliases](https://api-docs.deepseek.com/updates/),
[DeepSeek thinking](https://api-docs.deepseek.com/guides/thinking_mode/),
[Google pricing](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.1-flash-lite).

Existing campaign names and caps are preserved. `DECISION_CAMPAIGN` permits
an explicitly named new agenda account and is propagated to the public API
so its processing status reads the same ledger. The default remains
`jev-agenda-2026-09-30`; its remaining balance observed on 2026-10-02 was
approximately $.00336, insufficient for another $.10 reservation. Increasing
an existing cap is rejected. Early screening keeps the separate persistent
`early-signals-2026-10-02` account.

The user's approved total is $20 including previous costs. The earlier
conservative exposure bound was $7.12. A new agenda account capped at $2 would
bring that bound to $9.12. Do not reset historical accounts or create repeated
campaigns to bypass that overall limit. Native $.10 holds are conservative
accounting charges, not a claim that each request actually costs ten cents.

## Activation sequence

1. Add the native key in `/opt/geopulse/.env`, without sending it in chat.
   Set `DECISION_PROVIDER=deepseek` (or explicitly choose `gemini`). Perform
   read-only authentication/catalog inspection on the server first.
2. If using a new agenda account, set
   `DECISION_CAMPAIGN=deepseek-decisions-2026-10-02` and
   `JEV_AGENDA_BUDGET_USD=2`, within the approved overall budget. Keep the
   original early-signal account and its cap unchanged.
3. Require CI and review before integration. Deploy through
   `deploy/auto-update.sh`, verifying its success marker and running service
   configuration. A merge or a container start alone does not prove activation.
4. Run a single bounded pass on the server. Inspect fresh records for the
   actual selected model, exact source quotes, country assignment, pair
   acceptance, and correct committed budget charges. Do not export secrets.
5. Verify `/api/agendas` and the country/global workspace using those fresh
   records. Check that existing reviewed dossiers remain available. Separate
   writer/translation/embedding failures must remain explicit; a native
   screening key alone does not restore every other OpenRouter client.

## Verification

Local regression coverage includes transport limits and price caps, malformed
JSON with known cost, missing native keys, cross-article/invented quotes,
country misassignment and local spellings, pair-cache invalidation, current
membership snapshots, and private queue ownership across model changes.
An isolated PostgreSQL 16/pgvector container exercised real migrations for
screening persistence and private queue storage. The frontend suite and
production build passed. The final PR records the exact suite results;
live provider quality and production activation remain unverified until a
working credential is available.
