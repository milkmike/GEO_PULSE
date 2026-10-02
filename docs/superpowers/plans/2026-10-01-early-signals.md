# Early signal workbench implementation plan

**Goal:** Test an early-warning editorial workflow without changing production, spending money, or presenting hypothetical links as established facts.

**Architecture:** Separate local-change screening from Russia relevance. An offline workbench prepares bounded Jev questions, imports matching decisions, assembles explicit source context and validates a writer's dossier. Every dossier remains a draft requiring semantic review; quote validation alone cannot establish a causal link. The existing reviewed factual lane remains unchanged.

**Tech stack:** Python standard library, existing country catalog, pytest; self-contained HTML for editorial review.

**Approved design:** User approved the five-step observation/context/hypothesis/opportunity/follow-up workflow in this conversation on 1 October 2026 and previously requested GPT-6 Sol agents and autonomous implementation.

## Boundaries

- No network calls, production writes, budget resets or scheduled jobs in this first workbench.
- Evidence keeps source identity, original text and dates. Publisher geography is never event geography.
- One specific local document can be a candidate; Russia mention and publication count are not admission requirements.
- Generated text is always `needs_review`. A separate Russia basis is necessary before suggesting a Russian cooperation opportunity; semantic validity still requires review.
- Synthetic examples are visibly labelled and never inserted in production.

## Work units

- [x] `src/early_signals.py`, `tests/test_early_signals.py`: bounded country-balanced selection; typed Jev questions; strict parsing; source fingerprints. Owner precursor_core. Test negative legacy relevance, country confusion, malformed decisions, sampling fairness and byte limits.
- [x] `src/signal_hypotheses.py`, `tests/test_signal_hypotheses.py`: bounded context prompt; exact source quote/reference checks; alternatives, horizon and strengthening/weakening triggers; always provisional output. Owner hypothesis_core. Test invented quotes, future/stale dates, missing bridge, invalid URLs and unsafe text.
- [x] `scripts/review_early_signals.py`, `src/signal_workbench.py`, tests and documented example: prepare/import/context/review commands and readable HTML dossier. Owner root. Test imported request hash mismatch, escaping, missing sources and complete offline workflow.
- [x] Independent review and full relevant regression tests. Inspected rendered report at 1440/390 pixels and source disclosure with Enter. Full suite: 1403 passed, 107 skipped. Pipeline contract results are separate from unmeasured model accuracy.

## Next release gate

This workbench does not constitute a live early-warning service. Production integration requires measured model replay on real articles, semantic review of opportunities and false alarms, provider error diagnosis, and reconciliation of the already-reserved campaign budget. Future history retrieval must distinguish shared actors/assets from evidence of a mechanism, count publisher families rather than reprints, and preserve contrary evidence. No new live widget is added while this lane has no reviewed output.
