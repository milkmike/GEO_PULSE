# Live early signals: first release

The homepage keeps the map first. Below it, **На горизонте** presents reviewed Russian dossiers: a small change, a conditional hypothesis, an opportunity, an alternative explanation, and observable follow-up. A selected country filters event geography; the world view remains available. Empty content is hidden. Source quotations and publication/collection dates are disclosed next to the observation they support.

## Collection and publication

- `early-signals` screens a rotating country/publisher-balanced selection of recent articles with TypeSafe Jev 1.13. Russia does not have to occur in the title. Each batch distinguishes change/routine/uncertain, mechanism, proposal/decision/implementation, and event country. It stores decisions against the exact screened snapshot and prompt version.
- Previously attempted snapshots are removed before fair selection, so an unchanged high-volume feed cannot indefinitely crowd out unseen local reports. Invalid/future/archive snapshots are excluded. One cycle makes at most four requests; it stops at the first failure and has no automatic retry.
- `scripts/build_early_signals.py write` accepts an explicit source context and makes one prepaid writer request. Qwen 3.6 Flash and DeepSeek V4 Flash are the allowed comparison writers. A valid JSON envelope and exact source quotations do **not** establish semantic accuracy. Draft writing never publishes.
- `scripts/build_early_signals.py publish` requires a reviewed draft and a nonempty review note. The public endpoint is read-only and cannot call a model. It revalidates the stored snapshot, quotations, event countries and dates, and omits expired dossiers or those with no cited publication in the last seven days.
- Published interpretations remain `needs_review` hypotheses. The review note states evidence limitations; editorial release does not turn a hypothesis into a verified prediction. Only cited metadata and necessary quotations are public, not full collected excerpts.

First reviewed dossiers: Serbia/NIS (five reports from one outlet, RSS fragments; OFAC documents require independent verification) and Addis Ababa's vocational-training bureau (local reporting, separate China–Ethiopia training programme, and a dated Russian embassy contact in another institution). They explicitly avoid treating the older visit as a response to the new reorganisation.

## Budget and operation

`EARLY_SIGNAL_BUDGET_USD=0` disables paid screening by default. The first production campaign is `early-signals-2026-10-02`, capped at **$2**, inside the user's **$20 total** authorization including previous task costs. Screening reserves $0.01 before each request; writing reserves $0.10. Uncertain or invalid responses retain their reservation. Existing failed reservations are not reset. Reading the page incurs no model charge. This campaign limit does not change the site's other existing model workloads.

Migration `039_early_signal_dossiers.sql` creates immutable source-bound dossiers and the screening cache; the fresh database baseline contains the same schema. Deployment uses the existing locked `deploy/auto-update.sh` path. Do not publish the result of a writer command without a semantic source review.

## Diagnostic replay

A twelve-article replay of `early-signal-v3` completed three Jev requests for $0.00119805. It rejected a celebrity custody item as routine, retained a small municipal institutional change, distinguished proposals from implementation, and assigned a Kenyan infrastructure event to Kenya despite its Ethiopian publisher. Two strategic/ownership reports remained uncertain. This is a diagnostic sample, not a precision/recall benchmark.

The Qwen and DeepSeek writer probes did not meet publication quality without editing: unsupported motives, institutional conflation, unsuitable refutation by absence of coverage, or invalid schema/quotations appeared. Both first dossiers were edited and reviewed separately; automatic publication stays disabled.

The older analyst campaign had exhausted its conservative reservation cap. Historical generic error records cannot distinguish output truncation from parsing/citation rejection. This release adds content-free failure categories for subsequent extraction/translation calls; it does not refund old holds or restart that campaign.
