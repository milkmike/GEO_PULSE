# National source expansion: hybrid RSS-first design

## Goal

Increase direct national-media coverage across the 99-country catalog without
reintroducing Google News country-attribution errors or risking existing
articles, analysis, temperature history, signals, briefs, and stories.

The first release adds only verified publisher-owned RSS or Atom feeds. Sources
that require HTML extraction enter a separate adapter queue and are not enabled
until the existing collector can demonstrably extract fresh article URLs from
them.

## Production baseline

The read-only production audit on 2026-07-17 found:

- 518 active source rows across 98 of the 99 catalog countries;
- 433 direct-publisher rows and 85 Google News discovery rows;
- 347 rows with a successful most recent collection status;
- 263 direct publishers with `last_status = 'ok'`;
- 14 represented countries with no working direct publisher, 33 with one, and
  30 with two;
- Singapore is absent from the source table entirely;
- 80 direct web sources are reachable but currently yield no extracted
  articles;
- 63 Telegram rows report `unknown_type` in the RSS/web collector because they
  belong to the separate Telegram collector and must not be counted as failed
  RSS coverage.

At this baseline, reaching a floor of three working direct publishers for every
catalog country requires 141 successful additions. The rollout is therefore
deliberately wave-based.

## Coverage contract

A country meets the baseline only when it has at least three distinct, working
publisher domains. Where the media system permits it, the set should contain:

1. one official, public-service, or state source;
2. one high-volume national mainstream source;
3. one independent, investigative, opposition, or reader-supported source.

Publisher families count once even when the catalog contains several languages
or feeds. Google News, AllAfrica, generic aggregators, Russian mirrors, and
downstream wire republications do not count as independent national publishers.

Closed or conflict-affected media systems use an explicit exception instead of
pretending that a balanced local market exists. The accepted set must show the
available competing information systems, such as state media paired with an
exiled national newsroom, and expose the ownership and censorship limitation in
source metadata.

## Options considered

### Bulk activation

Add every discovered URL directly to `sources_world.yaml`. This is fastest but
would activate unverified HTML sites immediately because catalog sync always
sets configured sources to `active = true`. The current 80 `web/no_articles`
rows show that reachability alone is not an adequate gate. This option is
rejected.

### RSS-only activation

Add only publisher-owned feeds and never onboard HTML-only publishers. This has
the lowest operational risk but leaves Bolivia, North Korea, Mongolia,
Nicaragua, New Zealand, Sudan, and other important gaps unresolved. This option
is useful as the first lane but insufficient as the complete design.

### Hybrid RSS-first rollout (chosen)

Keep unpromoted candidates outside the loaded catalogs. Promote verified direct
feeds first, observe them in production, then build bounded publisher-specific
web adapters for candidates without usable feeds. This provides immediate
coverage gains while preserving a path to all 99 countries.

## Candidate registry

Create `src/collectors/source_candidates.yaml` as a staging registry. It is not
included in `config.load_sources()` and therefore cannot create or reactivate a
production source. Each candidate records:

- publisher name and canonical domain;
- country code and ownership-country evidence URL;
- feed or website URL and intended collector type;
- language, tier, state affiliation, and propaganda/syndication risk;
- expected publisher domain and approved domain aliases;
- validation state and reason;
- research date and provenance notes.

`scripts/validate_source_candidates.py` reads the staging registry and produces
machine-readable and Markdown reports. It does not edit production catalogs or
the database.

## Validation gates

### Common gates

A candidate is promotable only when:

- its country exists in `src.countries.COUNTRIES`;
- publisher ownership or national editorial identity is supported by a direct
  publisher, corporate, regulator, or public-service description;
- its canonical domain is not already configured or present in production;
- its tier and state-affiliation metadata are explicit;
- it is not a generic aggregator, Russian mirror, or Google News feed;
- article links resolve to the expected publisher domain or approved aliases;
- detected content language agrees with configured language;
- publisher-country attribution resolves through the verified domain registry,
  never from a ccTLD or Google News locale.

### RSS/Atom gates

- HTTP succeeds after redirects;
- the body parses as RSS or Atom even when the server uses an imperfect content
  type;
- at least three entries are present;
- the newest dated entry is no older than 14 days, with a documented 30-day
  exception for low-cadence investigative outlets;
- entry URLs and GUIDs are stable enough for existing deduplication;
- at least 80% of sampled entry URLs belong to the publisher domain or approved
  aliases.

### HTML adapter gates

An HTML-only source is not promoted as a generic `web` source merely because its
homepage returns HTTP 200. It requires either a successful existing scraper
probe or a bounded publisher-specific adapter with fixtures. The adapter must:

- extract at least five current article URLs from a representative page;
- reject navigation, tags, search, sponsored pages, and archive loops;
- recover title, canonical URL, and publication time;
- remain within the publisher domain;
- pass a repeat run without creating duplicate URLs.

## Wave 1: verified RSS canary

The first canary contains 17 missing publisher-owned feeds across eight
represented countries that currently have no working direct source, plus the
entirely absent Singapore: nine countries in total.

| Country | Publisher | Tier | Feed |
|---|---|---|---|
| Albania | RTSH | official | `https://rtsh.al/feed/` |
| Albania | Reporter.al | independent | `https://reporter.al/feed/` |
| Cyprus | Philenews | mainstream | `https://www.philenews.com/feed/` |
| Cyprus | Politis | independent | `https://www.politis.com.cy/feed/` |
| Denmark | Politiken | mainstream | `https://politiken.dk/rss/senestenyt.rss` |
| Denmark | Information | independent | `https://www.information.dk/feed` |
| Ireland | The Irish Times | mainstream | `https://www.irishtimes.com/arc/outboundfeeds/rss/?outputType=xml` |
| Ireland | TheJournal.ie | independent | `https://www.thejournal.ie/feed/` |
| Montenegro | RTCG | official | `https://rtcg.me/rss.html` |
| Montenegro | Vijesti | mainstream | `https://www.vijesti.me/rss` |
| North Macedonia | MRT | official | `https://www.mrt.com.mk/rss.xml` |
| North Macedonia | Meta.mk | independent | `https://meta.mk/feed/` |
| Portugal | RTP Noticias | official | `https://www.rtp.pt/noticias/rss` |
| Portugal | Observador | mainstream/independent | `https://observador.pt/feed/` |
| Slovenia | RTV Slovenija | official | `https://www.rtvslo.si/feeds/01.xml` |
| Slovenia | N1 Slovenija | mainstream | `https://n1info.si/feed/` |
| Singapore | CNA Singapore | official/state-linked | `https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml&category=10416` |

These domains were checked against production and are not currently configured.
The canary intentionally gives Singapore one verified direct publisher rather
than counting two feeds from the same publisher as diversity.

## Promotion and production observation

1. Validate the complete candidate record locally and save the report.
2. Copy only passing entries into `sources_world.yaml`; the staging record remains
   as an audit trail with status `promoted`.
3. Run catalog and publisher-attribution tests.
4. Deploy the collector configuration without restarting PostgreSQL or Redis.
5. Allow the normal 30-minute collector cycle to sync and collect the feeds.
6. Observe the canary for 12 hours, covering at least two successful fetches per
   source.

A canary source passes production observation when:

- `last_status` is `ok` on at least two collection passes;
- at least one article is persisted or the feed is demonstrably fresh but has
  no new item during the observation window;
- no article receives an unknown, blocked, or foreign publisher attribution;
- sampled titles and URLs belong to the intended publisher and country;
- the source does not create a material duplicate or retry storm.

No historical backfill is required for Wave 1. The first feed fetch supplies a
small bounded recent window, and all existing relevance, deduplication, analysis,
temperature, thread, and story processing remains unchanged.

## Later waves

### Wave 2: verified feeds and editorial balance

Promote publisher-owned feeds for countries with one or two working direct
sources and for well-covered countries whose mix is structurally biased. The
audited priority set includes CORRECTIV, Cumhuriyet, Byline Times, Mediapart,
RaiNews, elDiario.es, NU.nl, Ukrinform, Onliner, Chequeado, Agencia Publica, PBS
NewsHour, TRT Haber, Gulf Times, Kyunghyang Shinmun, and Indian Express.

Each batch is limited to 20 feeds and uses the same 12-hour observation gate.
Orda.kz is excluded from the new-source queue because it is already present in
production.

### Wave 3: publisher-specific HTML adapters

Build adapters for the strongest web-only gaps, beginning with Bolivia,
Mongolia, Nicaragua, New Zealand, and Sudan. North Korean sources enter only
after dedicated timeout, retry, and intermittent-hosting tests. Stale or
suspicious feeds such as SUNA remain quarantined until date integrity is proven.

## Failure handling and rollback

- A validation failure leaves the candidate in staging with a reason and never
  touches production.
- A production canary failure deactivates only the newly added source row and
  removes only its catalog entry in the follow-up deployment.
- Articles already collected from a later-disabled source are retained with
  provenance; they are not deleted or silently reassigned.
- Existing articles, analyses, temperature rows, signals, briefs, stories,
  embeddings, and publisher-domain history are never deleted or recomputed by
  this rollout.
- Protected row counts are captured before and after every production wave and
  must not decrease.

## Observability

Add a coverage report based on distinct publisher domains, not raw source rows.
For every country it shows:

- configured, direct, discovery, and working-direct counts;
- official/mainstream/independent mix;
- last successful fetch and current failure reason;
- publisher-family duplicates;
- target state: `uncovered`, `thin`, `baseline`, or `balanced`.

Google discovery and Telegram collection remain visible as separate dimensions
but do not inflate working-direct RSS/web coverage.

## Testing

Implementation follows red-green-refactor and covers:

- the staging registry is never loaded by `config.load_sources()`;
- candidate schema, country codes, tiers, and required provenance fields;
- duplicate-domain detection across all catalogs and an optional production
  inventory;
- RSS parsing, freshness, redirect, domain-ratio, and malformed-feed failures;
- content-type tolerance for valid XML returned as HTML;
- rejection of Google News, generic aggregators, and unapproved off-domain links;
- verified publisher-country attribution and domain aliases;
- HTML adapter extraction and duplicate rejection with saved fixtures;
- coverage counts use distinct working publisher domains;
- catalog promotion remains idempotent.

## Acceptance criteria

- all 17 Wave 1 candidates pass local validation before promotion;
- at least 15 of 17 pass the 12-hour production canary; failures are disabled and
  reported rather than hidden;
- Singapore has a verified direct national publisher for the first time;
- at least eight of the nine Wave 1 countries move from zero working direct
  sources to at least one;
- no Wave 1 article is attributed to a Russian or unrelated foreign publisher;
- no protected production row count decreases;
- collector duration remains within its 30-minute schedule;
- source health and country coverage APIs continue returning HTTP 200.

## Out of scope

- deleting old Google News discovery rows;
- rewriting historical publisher attribution;
- recalculating historical temperature or RRI values;
- treating state media as neutral or independent;
- building a public source-management UI in this release;
- enabling every web-only candidate before a tested adapter exists.
