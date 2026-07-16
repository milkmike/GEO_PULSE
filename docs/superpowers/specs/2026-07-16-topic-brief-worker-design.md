# Reliable thematic lens briefs

## Goal

Every thematic lens on the home page must show a current cached brief when topic
data exists. Provider or generation failures must never be presented as
"insufficient data".

## Confirmed production failure

The `culture_sport` lens has roughly 422 analyzed articles in the 30-day country
breakdown and eight current headlines, but `/api/v2/topics/culture_sport/brief`
returns 404. Topic generation currently runs synchronously inside the API
container. That container has `OPENROUTER_API_KEY` but no Finland `HTTPS_PROXY`,
so OpenRouter returns 403. The endpoint converts the provider failure to `None`
and then to the same 404 used for genuinely empty topics. The frontend converts
every non-200 response to "Недостаточно данных по теме".

## Chosen design

The proxy-enabled `briefs` worker owns generation for all 16 thematic lenses.
Each normal worker pass refreshes topic briefs after the world brief and before
country briefs, using the existing six-hour cache and per-topic error isolation.
The API endpoint reads the latest cached topic brief and does not call an LLM.

For a topic with data but no cached brief, the API returns a distinct temporary
unavailable response. A genuinely empty topic keeps the insufficient-data
response. The frontend renders three separate states: loading, temporarily
unavailable/updating, and genuinely insufficient data.

Immediately after deployment, run a one-off topic-only generation pass in the
proxy-enabled worker so all lenses become useful without waiting for the next
scheduled cycle.

## Safety and rollback

- Do not add the Finland proxy to the public API container.
- Topic generation failures must not stop world or country brief generation.
- Existing cached briefs remain readable throughout deployment.
- Topic upserts are additive and use the existing `briefs` table.
- Rollback restores the previous worker/API/web images; cached rows may remain.

## Testing

- A worker pass attempts every configured topic and isolates one topic failure.
- The API never invokes generation and returns a cached topic brief.
- The API distinguishes data-present/cache-missing from genuinely empty input.
- The frontend distinguishes temporary generation failure from insufficient data.
- The normal world and country brief paths remain unchanged.

## Acceptance criteria

- All 16 topic endpoints return a cached brief or an explicit temporary state.
- `culture_sport` returns a brief while its topic data remains non-empty.
- No new OpenRouter 403 appears in API logs.
- World and country brief generation continues normally.
- Home, topic countries, topic headlines, and topic brief APIs return expected
  responses after deployment.
