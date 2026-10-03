# Jev source evidence for decision cards

DeepSeek drafts the Russian card. With `JEV_EVIDENCE_MODE=apply`, quote fields in
the draft reference existing source fragment IDs; code copies literal source
text. Jev independently selects fragments and then reviews each claim against
its selected fragment. Unsupported optional prose is omitted. A card requires
supported headline, Russia relation and at least one participating country.

Native confidence is not an accuracy estimate. Admission retains the existing
conservative thresholds: confidence >= .80 and supported probability >= .90.
Selection alone never admits a claim. When English/Russian country names exist
in the source, country selection offers those fragments instead of an implicit
company or publisher association. Other languages keep semantic selection.

Each review has at most two native Decisions requests, a 24KB payload limit,
13 questions and a $.10 reservation per attempt in the existing persistent
campaign ledger. Preflight requires every available Jev endpoint to have input
price <= $.10/M, zero output price and no request fee. Missing/unknown costs keep
the full reservation. There is no paid retry or provider fallback. A tariff or
request failure leaves an existing card intact. A fresh named campaign must
respect the already authorized overall budget; old ledgers are never reset.

Stored proof binds exact source ID/title/excerpt and retained annotation through
hashes. Public projection rechecks source fragments; changed proof cannot become
a legacy card. The API publishes only claim label, exact quote and source part.
Cards expose these through “Цитата и источник”, grouping labels when one quote
supports several claims. Legacy reviewed cards remain readable without relabeling.

Default `off` retains the current review. The switch belongs only to the agendas
worker: discovery/screening continue using `DECISION_PROVIDER`, and public
requests never call a model. Apply mode upgrades current relevant legacy cards
as budget permits; already proven and irrelevant cards are skipped.

Live check through the existing server gateway, 2026-10-03: Jev catalog returned
200, input price $.042/M, output zero. On publication 1725486, first selection
chose a company-only headline as country proof and the independent review
abstained. Restricting country options to the explicitly named Serbia fragment
produced supported headline/Russia/country evidence without lowering thresholds.
Four requests across both experiments cost $.000347844 in the separate $.30
`jev-evidence-2026-10-03` account. One example establishes transport and the
mechanism, not global recall, article truth or a calibrated country-wide score.

Rollback: set `JEV_EVIDENCE_MODE=off` and recreate the agendas worker through the
normal deploy. Existing source proof remains readable; no schema rollback or
ledger reset is needed.

Selector v2 uses short request-local choice labels and compact selection rules,
then maps choices back to the exact stable source offsets in code. This removes
duplicated text that caused normal 4000-character Cyrillic sources to exceed
24KB before a paid call. The full second-pass semantic rules, thresholds and
request/cost caps remain unchanged. An operation revision changes the attempted
source key for the corrected pipeline; the stored proof schema remains v1 so
existing cards stay readable. A regression covers both passes with eight claims
and a 4000-character source.
