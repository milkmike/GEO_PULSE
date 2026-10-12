# Autonomous world monitoring

The hourly `early-signals` service covers the 248 countries and territories in
`MONITORING_COUNTRIES`. Country selection in the browser does not schedule work.
The existing 99-country RRI methodology is unchanged.

## Automatic pipeline

- A durable, fair queue selects missing or unhealthy source coverage. Wikidata
  supplies publisher provenance; a candidate is not a source until its public
  website and first-party RSS/Atom feed pass bounded validation.
- Accepted feeds enter the existing collector. A unique broken direct feed can
  be repaired in place; publisher identity and article history are preserved.
- Recent articles are sampled across countries and publishers. Source-bound
  model decisions distinguish a concrete change from routine reporting, with
  exact quotations and separate event and publisher geography.
- Fresh, grounded observations appear automatically with source links and
  Russian machine translations. Longer hypothesis dossiers retain editorial
  release; an observation is not an independently verified forecast.
- Public reads make no model calls. Source discovery continues when the paid
  analysis allowance is disabled or exhausted.

## Runtime controls

Production configuration is held only in the server's ignored `.env`:

```dotenv
AUTO_SOURCE_ONBOARDING=1
EARLY_SIGNAL_CAMPAIGN=early-signals-2026-10-12-world
EARLY_SIGNAL_BUDGET_USD=2
EARLY_SIGNAL_OPENROUTER_PROVIDER=streamlake
```

The campaign cap is persistent and shared by screening, translation and writing.
It is **not** a daily allowance. Retain the campaign ID across restarts; do not
reset old ledgers. Unknown outcomes retain their full reservation. The $2 cap is
inside the user's previously approved $20 total allowance. The existing server
proxy and credentials stay in place.

Live checks found incomplete unstructured OpenRouter responses. Screening and
translation now require strict schemas, and the early-signal service can pin a
tested provider while retaining tariff bounds. Native provider adapters remain
available. Invalid evidence is rejected, not silently repaired.

Translations use bounded current source context to disambiguate foreign place
names. A changed title or excerpt prevents stale output from being saved; its
new content identity permits a new attempt within the same campaign allowance.

Wikidata can return HTTP 200 with `maxlag`/`Retry-After`. Rate-limited country
research waits six hours; other failed discovery still uses the longer cooldown.
Migration 044 shortens only old unleased rate-limit retries and is idempotent.

## Release evidence and limitations

The first autonomous release (PR #46) admitted four feeds without country prompts:
Diari d'Andorra, Caribbean News Agency, La Nouvelle Tribune and Le Matinal.
Three delivered 50 stored articles in a bounded first collection. Caribbean
News Agency passed admission but a subsequent fetch failed; admission alone does
not establish sustained availability.

The observed fresh local sample reached 94 of 248 areas. This is measured coverage,
not a claim that all 248 areas already have working sources or complete analysis.
Source discovery is limited by upstream availability, publisher feed support and
its bounded queue. The API exposes coverage gaps and analysis status separately.

Verify releases through CI, the official `deploy/auto-update.sh`, its durable
success marker, running service settings, fresh monitor records and public API
observations. A successful build or model response alone does not prove that the
hourly production loop is active.
