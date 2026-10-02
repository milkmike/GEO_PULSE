# Live early signals implementation plan

Goal: ship real, source-grounded early warning dossiers in the existing map workspace.

Architecture: Jev screens small local changes without requiring Russia in the headline. A bounded background writer accepts explicitly selected context. Storage separates provisional interpretation from editorial release; only explicitly reviewed releases enter the read-only public feed. The map stays first and empty sections stay hidden.

Authorization: user approved the observation/context/hypothesis/opportunity/follow-up design and autonomous multi-agent execution. On 2 October the user authorized a total $20 including prior task expenses. This extends the prior offline-only slice; no old failed-call reservations are reset. The existing early-signals-2026-10-02 campaign remains capped at $2 inside that total.

- [x] Storage/API: migration 039, immutable cited snapshots, quote validation, event geography, release note, freshness/expiry and filtered read-only endpoint. Owner live_signal_store.
- [x] UI: concise Russian editorial panel beneath the map, disclosure of hypotheses/alternatives/triggers/quotes, abort stale requests, hide empty content and preserve refresh data. Owner live_signal_frontend.
- [x] Screening: improve celebrity/private-event rejection and handling of strategic official attention, bump prompt/cache version, preserve stage uncertainty. Owner weak_signal_screening.
- [x] Worker/CLI: persistent prepaid cap, no automatic retry, exact error category, screening cache; bounded writer and explicit reviewed publication. Owner root.
- [x] First release: Serbia NIS + Ethiopia training/institutional changes. Semantic review inspected Russia basis, independent-source limits, dates, event country and contrary evidence. Raw writer probes rejected; edited releases prepared separately.
- [ ] Verify backend/frontend suites, PostgreSQL migration in CI, desktop/mobile rendering and source disclosures; then merge and deploy via existing locked auto-update path. Check live API and exact content after rollout.

Review focus: archive articles collected today are not fresh events; publisher location is not event geography; many copies are not independent confirmation; a new local body is not automatically the Russian partner; a structural JSON check is not semantic verification; model outages cannot blank the map or established data. No automated publication of unreviewed writer drafts.

Local evidence: 1429 backend tests passed, 108 skipped without PostgreSQL; frontend suite passed, production build passed; 390px viewport has no horizontal overflow; keyboard hypothesis/source disclosures verified. CI must run the PostgreSQL migration tests before merge.
