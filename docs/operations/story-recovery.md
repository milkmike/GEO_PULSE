# Story pipeline recovery and Jev observation

Jev is disabled by default (`JEV_STORY_MODE=off`). `shadow` observes a bounded
same-country lexical sample after the thread transaction commits. It never
changes memberships. A call has at most20 pairs/24KB and a hard child-process
deadline (default2s, maximum5s); failures preserve the baseline output.

Before enabling shadow, approve a spending limit and arrange log retention for
`jev_story_shadow`. Reports contain article IDs, baseline membership, choices,
confidence, prompt version/hash and reported cost, not article text or secrets.
Confidence is not a calibrated accuracy measure. Use a manually labelled,
multilingual corpus before giving the judge write authority.

## Bounded repair

Use `python -m scripts.recover_analysis --article-ids 123 456` in the analyzer
image for a dry-run. `--apply --max-usd 1` enables explicit paid repair. IDs here
are examples: select actual IDs only after inspecting their evidence. Maximum
25 distinct IDs per run; no embeddings or background worker restart is included.

Eligible rows are failed keyword placeholders and keyword-only rejections, with
no sentiment. Successful model analyses are excluded. Replacement preserves the
analysis ID, checks eligibility again under a row lock, and commits mentions in
the same transaction. A failed model call leaves the original row untouched.

The client reserves $0.10 before each attempt, including unknown outcomes, and
never releases a reservation for reuse. The run ceiling is at most$5; use the
remaining approved task budget when running multiple commands. Prompt size is
limited to32KB UTF-8, output to1000 tokens, and provider prices to$1/M input,
$2/M output and zero per-request fee. No provider fallback or internal retries.
Reports distinguish actual reported costs from reserved allowance; the shared
API key's background consumption is outside this command's local budget.

`scripts.compare_story_models` accepts `--fixtures <path>` with up to20 labelled
pairs (`id`, `left`, `right`, `expected`). The repository's synthetic fixture is
`tests/fixtures/story_pairs.json`; supply it separately when running in Docker.
This command reserves at most$0.21 for one Jev and one request per chat model.

## Rollout limits

- Embedding components now carry anchor IDs in their persisted keys. Existing
  bare-key threads remain; first rollout may create additional threads. Losing
  an anchor from the rolling window can split a thread. Inspect memberships
  before broad rebuilding; this change does not delete historical threads.
- Language hints are checked against Unicode scripts; ambiguous text is `und`.
  This is not full language identification, and keyword recall remains incomplete.
- Post-commit queue delivery fixes premature visibility, not durable delivery.
  Failed jobs remain in dead letters; older failures can fall outside the bounded
  newest-first database retry window and need explicit operational replay.
- Authentication/billing cooldown is process-local, not a fleet-wide breaker.
- Thermometer v1 and API v1 are unchanged. Historical recovery, embedding
  coverage and final story freshness require separate end-to-end verification.
