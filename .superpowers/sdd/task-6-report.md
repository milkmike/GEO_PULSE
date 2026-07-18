# Task 6 report — Early Warning Radar web experience

Status: **DONE**

## Delivered

- Added fail-closed `FEATURE_EARLY_WARNING_RADAR` server snapshot and Docker/Compose propagation. The radar navigation, routes, data requests, existing-page placements, and About methodology stay off unless the value is exactly `true`.
- Added typed Task 5 API contracts and clients for global/country lists, detail, timeline, evidence, coverage, and methodology. Requests are abortable; page and placement clients suppress stale updates.
- Added `/radar` with URL-backed state/contour/country filters, cursor pagination, recoverable loading/error states, an explicit empty-state limitation, keyboard traversal, and safe evidence URLs.
- Added `/radar/[id]` with shareable `view=propagation|evidence|coverage|method` state and the approved eight-question investigation sequence. Media/action contours, country waves, automatic/effective T0, contradictions, collection gaps, evidence, and backend methodology are separated explicitly.
- Added reusable `EarlyWarningPanel`, `TrendCard`, and `TrendInvestigation` components without changing the existing market `RadarPanel`.
- Added gated placements: eight-column home panel before stories, full-width country panel after dynamics, one signal-related trend, story analytical trends, radar navigation, and backend-sourced About methodology.
- Preserved the established dark editorial visual system and existing tokens/components. Focus-visible, keyboard, minimum touch-target, and reduced-motion behavior are present on new interactions.

## Test-first evidence

- RED: `npm test -- --run app/radar components/EarlyWarningPanel` failed on the missing routes/components before implementation.
- GREEN focused: radar routes and feature flag tests pass (15/15).
- Full web tests: `npm test -- --run` — 24 files, 129 tests passed.
- Type check: `npx tsc --noEmit` — passed.
- Production build: `npm run build` — passed; `/radar` is statically generated and `/radar/[id]` is available as a dynamic route.
- Diff hygiene: `git diff --check` — passed.

## Self-review

- Verified every radar data call uses a typed `api` method; no component issues an ad-hoc fetch.
- Verified `safeHttpUrl` guards preview and investigation evidence links.
- Verified compact panels select confirmed trends plus only exceptional emerging trends (high velocity, high confidence, and coverage above the confirmation hard-gate).
- Verified all new public entry points are gated and the default remains off.
- No Task 5 backend or unrelated web implementation was reverted.

## Concerns

None. The UI consumes the Task 5 response shapes as currently implemented.

## Review corrections — 2026-07-18

- Evidence pagination now keeps absence claims page-bounded until `next_cursor` is exhausted. The evidence view says “в загруженных доказательствах” while incomplete and supports repeated, abortable load-more requests.
- The four shareable investigation views are now materially distinct: propagation uses the fetched timeline, evidence owns support and contradictions, coverage owns country gaps and limitations, and method owns baseline/T0/confidence details. The URL explicitly retains every selected `view`, including `view=propagation`.
- Radar list pagination now owns a dedicated `AbortController` and request generation. Filter changes and unmounts abort pending pages; responses from older filter generations cannot append.
- Story and signal placements now request exact `story_id` and `signal_id` relations through typed `RadarFilters`, rather than substituting a first-country trend list.

### Review test evidence

- Focused regression suite: `npm test -- --run app/radar 'app/stories/[id]/StoryDetailPage.test.tsx' components/SignalEvidence.test.tsx lib/api.radar.test.ts` — 5 files, 40 tests passed.
- Full web suite: `npm test -- --run` — 25 files, 135 tests passed.
- Type check: `npx tsc --noEmit` — passed sequentially after the production build generated Next.js route types.
- Production build: `npm run build` — passed; `/radar` remains static and `/radar/[id]` remains dynamic.
- Diff hygiene: `git diff --check` — passed.

### Review concerns

None.
