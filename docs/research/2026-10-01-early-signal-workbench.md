# Early-signal workbench: offline first stage

The current reviewed article lane correctly requires an explicit Russia link inside an article. That rule cannot represent a local precursor whose relevance only becomes apparent after reading older context. This workbench tests a separate editorial path without weakening the existing factual checks or creating an empty production widget.

## What works

- Recent source snapshots are sampled across countries and publishers; the starting position rotates hourly. Legacy Russia relevance labels do not control admission. Conflicting duplicate IDs are rejected.
- Jev requests ask independently about a concrete local change, mechanism, stage and involved country. One source can be a candidate. Publisher country is not sent as event evidence. Requests contain at most eight articles and 24 KB.
- Excerpts sent to screening are capped at 2,000 characters and retained exactly through the workbench. Longer context is explicitly selected separately. Responses must match a prepared request and intact source snapshot. Missing batches remain visibly unanswered.
- A writer receives up to twelve explicitly selected source snapshots, at most 90 days old, and a 24 KB prompt. The dossier separates observations, interpretation, conditional hypothesis, possible opportunity, alternative explanation, and dated strengthening/weakening triggers.
- Quote/reference checks reject invented quotations, missing articles, invalid dates and unreferenced Russia-basis pointers. All dossiers remain `needs_review`: a valid quote does not prove the paraphrase, causal mechanism, country assignment or proposed opportunity.
- The generated HTML keeps the exact cited snapshots and checksum in an expandable review appendix. Publication and collection timestamps are shown in Moscow time. The synthetic acceptance example is prominently labelled and has no clickable fictional source links.

## Run locally

From the repository root, using the project's Python environment:

```sh
python scripts/review_early_signals.py example --out /tmp/early-signal-example.html
python scripts/review_early_signals.py prepare --articles articles.json --as-of 2026-10-01T18:00:00Z --limit 80 --out prepared.json
python scripts/review_early_signals.py screen --prepared prepared.json --responses responses.json --out screened.json
python scripts/review_early_signals.py context --screened screened.json --article-id 123 --context context-articles.json --out context.json
python scripts/review_early_signals.py review --context context.json --draft draft.json --out review.html
```

Outputs must be new paths; the tool refuses overwrites. `articles.json` and `context-articles.json` are arrays with `id,title,excerpt,source_id,source_name,country_code,url,published_at,collected_at`. Dates need explicit timezones; country codes denote publisher location only. `responses.json` is an array of `{request_hash,data}` envelopes corresponding to prepared requests. `data` is the provider decision response. `context.json` contains the writer prompt and evidence; `draft.json` is an externally authored object following that prompt's schema. No command obtains provider answers or runs a writer automatically.

## Verification and limitations

46 new offline tests cover the complete mocked-response workflow, independent country attribution, conservative parsing, sampling rotation, conflicting duplicates, stale/future evidence, source metadata changes, unseen excerpt text, fabricated citations, opposing triggers, provisional status, retained provenance and CLI overwrite protection. Full local backend suite: 1,403 passed, 107 skipped; skipped database/integration suites are not claimed as verified.

The fixture contains invented events and hand-authored decisions/narrative. It measures contract behavior, not Jev accuracy, early-warning recall, lead time or editorial quality on real news. Context retrieval is manual in this stage. There are no model calls, production writes, budget modifications, scheduled processing, new API routes or live homepage changes.

Before enabling a live lane: diagnose existing provider rejection causes and reconcile held campaign costs; review real multilingual positive and noise samples; measure missed small signals, false opportunities, corroboration independence and useful lead time; then integrate historical retrieval and lifecycle updates. Historical co-occurrence and lexical similarity can nominate context but cannot establish a mechanism or national public sentiment.
