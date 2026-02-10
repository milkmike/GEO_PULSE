# Threads v2 — Implementation Plan

## Step 1: LLM-powered deduplication (build_threads.py)
- Lower pg_trgm threshold to 0.4
- After clustering, send candidate groups to LLM for merge confirmation
- Merge confirmed duplicates: combine articles, pick best title

## Step 2: DB schema upgrade
- Add: `summary_json JSONB` (structured narrative)
- Add: `velocity FLOAT` (articles per hour growth rate)
- Add: `sentiment_shift FLOAT` (sentiment change over thread lifetime)
- Add: `related_threads INTEGER[]` (cross-thread links)
- Add: `merged_keys TEXT[]` (all event_keys merged into this thread)

## Step 3: Enhanced importance formula
- velocity * source_diversity * tier_coverage * action_escalation * freshness_decay

## Step 4: Structured narrative via LLM
- summary, dynamics, impact, forecast, indicators

## Step 5: API improvements
- /threads/{id}/related — related threads
- /threads/merge — manual merge endpoint
- Enhanced list with dedup

## Step 6: UI overhaul
- Timeline visualization
- Structured narrative cards
- Merge UI in admin
