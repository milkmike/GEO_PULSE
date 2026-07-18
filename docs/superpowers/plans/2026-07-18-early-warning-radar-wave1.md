# Early Warning Radar Wave 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship one production-safe vertical slice of the Early Warning Radar with independent media/action contours, 90-day country baselines, auditable T0, cross-country meta-trends, read-only v2 APIs, and feature-flagged public pages.

**Architecture:** Add an isolated `src/radar` domain beside existing stories, signals, Thermometer, and RRI. Background workers read current immutable evidence, persist versioned observations and trend state, and public GET routes only read saved results. Existing product tables and formulas remain untouched; all schema is additive and every trend decision is replayable.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL/TimescaleDB, pgvector, River 0.25.0, ruptures 1.1.10, Next.js/React/TypeScript, Plotly, pytest, Vitest.

## Global Constraints

- Preserve API v1, Thermometer v1, RRI v1, existing articles, analysis, temperature history, signals, briefs, threads, and stories.
- Never rewrite `articles.source_id`, `articles.publisher_source_id`, `articles.external_id`, discovery URLs, or existing uniqueness boundaries.
- Add every table to both an idempotent numbered migration and `data/init.sql`.
- `analysis.action_level` is media classification and cannot independently confirm the action contour.
- Every country wave uses a rolling 90-day baseline and a 7-day acceleration window.
- Store `first_observed_at`, `detected_at`, `confirmed_at`, `t0_auto`, and `t0_effective` separately.
- Ordinary emerging signals are dashboard-only; confirmed and exceptional early signals are notification-eligible.
- Public GET endpoints never call an LLM, create embeddings, update trend state, or enqueue paid work.
- Use evidence roles `trigger`, `support`, `context`, and `contradiction` exactly.
- Use `safe_public_url` in Python and `safeHttpUrl` in TypeScript for public evidence links.
- New UI uses `EarlyWarningPanel`, never the existing market `RadarPanel` name.
- Wave 1 must degrade honestly when an action contour or country coverage is insufficient.

---

## File Structure

Create focused domain modules:

- `src/radar/types.py` — immutable enums and dataclasses shared by detectors and API serializers.
- `src/radar/baseline.py` — 90-day baseline, acceleration, coverage confidence, and T0 calculations.
- `src/radar/lifecycle.py` — deterministic state transition rules.
- `src/radar/repository.py` — SQL reads/writes and idempotent upserts.
- `src/radar/media.py` — media observations from existing stories, signals, GDELT, and indexed article evidence.
- `src/radar/actions.py` — structured and authoritative action observations; no media self-confirmation.
- `src/radar/grouping.py` — country-wave and cross-country meta-trend assignment.
- `src/radar/service.py` — one replayable build cycle and public read models.
- `src/api/routes/radar.py` — read-only v2 HTTP contract.
- `scripts/build_radar.py` — CLI/background entry point, shadow audit, and bounded replay.
- `web/app/radar/**` — list and investigation routes.
- `web/components/EarlyWarningPanel.tsx`, `TrendCard.tsx`, and `TrendInvestigation.tsx` — reusable radar UI.

Do not add radar logic to `src/engine/index.py`, `src/engine/ru_index.py`, `src/engine/signals.py`, or the existing market `web/components/RadarPanel.tsx`.

---

### Task 1: Add the additive radar schema

**Files:**

- Create: `scripts/migrations/027_early_warning_radar.sql`
- Modify: `data/init.sql`
- Modify: `tests/test_schema_contract.py`
- Test: `tests/test_postgres_migrations.py`

**Interfaces:**

- Consumes: existing `countries`, `articles`, `stories`, `signals`, and `canonical_entities` IDs.
- Produces: `radar_observations`, `action_events`, `radar_trends`, `radar_trend_members`, `radar_trend_evidence`, `radar_state_events`, `radar_t0_revisions`, `radar_contour_links`, `analysis_runs`, and `notification_events`.

- [ ] **Step 1: Write the failing schema contract test**

Add a test that loads migration 027 and `data/init.sql`, then asserts both contain every table, lifecycle/contour/evidence-role check constraints, unique idempotency keys, and country/time indexes:

```python
def test_radar_schema_is_additive_auditable_and_bootstrapped():
    migration_sql = migration("027_early_warning_radar.sql")
    init_sql = (ROOT / "data" / "init.sql").read_text()
    tables = (
        "radar_observations", "action_events", "radar_trends",
        "radar_trend_members", "radar_trend_evidence", "radar_state_events",
        "radar_t0_revisions", "radar_contour_links", "analysis_runs",
        "notification_events",
    )
    for sql in (migration_sql, init_sql):
        for table in tables:
            assert f"CREATE TABLE IF NOT EXISTS {table}" in sql
        assert "CHECK (contour IN ('media','action'))" in sql
        assert "'candidate','emerging','confirmed','cooling','resolved','rejected'" in sql
        assert "'trigger','support','context','contradiction'" in sql
    assert "UPDATE articles SET" not in migration_sql
    assert "DELETE FROM articles" not in migration_sql
```

- [ ] **Step 2: Run the contract test and verify failure**

Run: `python -m pytest tests/test_schema_contract.py::test_radar_schema_is_additive_auditable_and_bootstrapped -q`

Expected: FAIL because migration 027 and radar tables do not exist.

- [ ] **Step 3: Implement the idempotent migration and bootstrap schema**

Use `BIGSERIAL` record IDs, `UUID` public IDs generated by the application, explicit `CHECK` constraints, `JSONB NOT NULL DEFAULT '{}'`, and foreign keys with `ON DELETE RESTRICT` for evidence roots. The core trend row must expose this stable contract:

```sql
CREATE TABLE IF NOT EXISTS radar_trends (
  id BIGSERIAL PRIMARY KEY,
  public_id UUID UNIQUE NOT NULL,
  scope VARCHAR(16) NOT NULL CHECK (scope IN ('country','meta')),
  contour VARCHAR(16) CHECK (contour IN ('media','action')),
  country_code CHAR(2) REFERENCES countries(code),
  subject_key TEXT NOT NULL,
  title_ru TEXT NOT NULL,
  direction VARCHAR(24) NOT NULL,
  state VARCHAR(16) NOT NULL CHECK (state IN
    ('candidate','emerging','confirmed','cooling','resolved','rejected')),
  confidence NUMERIC(5,4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  coverage_confidence NUMERIC(5,4) NOT NULL CHECK (coverage_confidence BETWEEN 0 AND 1),
  velocity NUMERIC(12,4) NOT NULL DEFAULT 0,
  first_observed_at TIMESTAMPTZ NOT NULL,
  detected_at TIMESTAMPTZ,
  confirmed_at TIMESTAMPTZ,
  t0_auto TIMESTAMPTZ,
  t0_effective TIMESTAMPTZ,
  detector_version VARCHAR(40) NOT NULL,
  baseline JSONB NOT NULL DEFAULT '{}',
  explanation JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(scope, contour, country_code, subject_key, direction, detector_version)
);
```

`radar_state_events` and `radar_t0_revisions` are append-only. `notification_events` uses a unique `(trend_id, transition_id, channel, audience_key)` delivery key. `analysis_runs` stores input hash, parameters, detector/model version, status, cost, and output references.

- [ ] **Step 4: Run schema tests**

Run: `python -m pytest tests/test_schema_contract.py tests/test_postgres_migrations.py -q`

Expected: schema contract PASS; opt-in database tests either PASS or SKIP without a disposable DSN.

- [ ] **Step 5: Commit**

```bash
git add scripts/migrations/027_early_warning_radar.sql data/init.sql tests/test_schema_contract.py tests/test_postgres_migrations.py
git commit -m "feat(radar): add additive trend schema"
```

---

### Task 2: Implement deterministic baseline, T0, confidence, and lifecycle rules

**Files:**

- Create: `src/radar/__init__.py`
- Create: `src/radar/types.py`
- Create: `src/radar/baseline.py`
- Create: `src/radar/lifecycle.py`
- Create: `tests/test_radar_baseline.py`
- Create: `tests/test_radar_lifecycle.py`
- Modify: `requirements-temperature.txt`

**Interfaces:**

- Produces: `BaselineResult`, `calculate_baseline(points, as_of, coverage)`, `refine_t0(points, detected_at)`, `TrendDecision`, and `decide_state(metrics, previous_state)`.
- Consumes later: Tasks 4 and 5 call these pure functions without a database session.

- [ ] **Step 1: Write failing tests for a time-correct 90-day baseline**

```python
def test_baseline_uses_only_the_90_days_before_as_of():
    result = calculate_baseline(_daily_points(days=110), as_of=NOW, coverage=0.9)
    assert result.window_days == 90
    assert result.acceleration_days == 7
    assert all(point.at < NOW for point in result.baseline_points)

def test_critical_coverage_suppresses_confirmation():
    result = calculate_baseline(_shifted_points(), as_of=NOW, coverage=0.42)
    assert result.coverage_gate == "critical"
    assert result.confirmation_allowed is False
```

- [ ] **Step 2: Write failing tests for T0 and state transitions**

```python
def test_t0_is_first_supported_change_not_detection_time():
    result = refine_t0(_regime_change(day=63), detected_at=DAY_70)
    assert result.t0_auto == DAY_63
    assert result.t0_auto < result.detected_at

def test_media_and_action_confirm_independently():
    media = decide_state(_metrics(contour="media", persistent=True), "emerging")
    action = decide_state(_metrics(contour="action", authoritative=True), "emerging")
    assert media.state == "confirmed"
    assert action.state == "confirmed"
```

- [ ] **Step 3: Run tests and verify failure**

Run: `python -m pytest tests/test_radar_baseline.py tests/test_radar_lifecycle.py -q`

Expected: FAIL because `src.radar` does not exist.

- [ ] **Step 4: Add pinned detector dependencies**

Append to `requirements-temperature.txt`:

```text
river==0.25.0
ruptures==1.1.10
```

- [ ] **Step 5: Implement immutable domain types and pure calculations**

Define string enums `Contour`, `TrendState`, `CoverageGate`, and dataclasses with timezone-aware datetimes. `calculate_baseline` must exclude points at or after `as_of`, dense-fill missing daily buckets as missing rather than zero, use the latest 90 days, report the 7-day acceleration, and calculate:

```python
confidence = clamp01(
    semantic_signal
    * source_independence
    * coverage_confidence
    * persistence
)
```

Use River `ADWIN` and `PageHinkley` for online candidate flags. Use ruptures `Pelt(model="l2", min_size=7)` over `log1p`-transformed non-negative volume/attention metrics to refine T0. If fewer than 28 valid daily points exist, return `insufficient_history` without inventing a changepoint.

- [ ] **Step 6: Implement explicit lifecycle gates**

Media confirmation requires persistence, at least two independent publisher families, non-critical coverage, and configured signal strength. Action confirmation requires authoritative/registry evidence or two independent authoritative sources; `analysis.action_level` alone never satisfies it. Cooling and resolved transitions require elapsed quiet windows and cannot happen from one missing collection cycle.

- [ ] **Step 7: Run focused tests**

Run: `python -m pytest tests/test_radar_baseline.py tests/test_radar_lifecycle.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add requirements-temperature.txt src/radar tests/test_radar_baseline.py tests/test_radar_lifecycle.py
git commit -m "feat(radar): add baseline and lifecycle engine"
```

---

### Task 3: Normalize independent media and action observations

**Files:**

- Create: `src/radar/media.py`
- Create: `src/radar/actions.py`
- Create: `src/radar/repository.py`
- Create: `tests/test_radar_observations.py`

**Interfaces:**

- Produces: `build_media_observations(session, window) -> list[Observation]`, `build_action_observations(session, window) -> list[Observation]`, and `upsert_observations(session, observations) -> int`.
- Consumes: `article_country_facts`, `stories`, `story_articles`, `story_countries`, `story_entities`, `story_events`, `signal_evidence`, `sanctions_pressure`, `un_votes`, `trade_data`, and `ru_fossil_imports`.

- [ ] **Step 1: Write failing media-observation tests**

Create fixtures with duplicate articles, two feeds from one publisher family, two independent families, one Google News discovery-only row, and one story. Assert one daily observation per `(country, subject, direction, metric)` and family-level independence:

```python
def test_media_observation_counts_publisher_families_not_feed_rows(session):
    observations = build_media_observations(session, _window())
    point = _only(observations, metric="attention_share")
    assert point.publisher_family_count == 2
    assert point.article_ids == (ARTICLE_A, ARTICLE_B, ARTICLE_C)
```

- [ ] **Step 2: Write failing action-independence tests**

```python
def test_media_action_level_never_confirms_action_observation(session):
    _insert_high_action_media_article(session, action_level=6)
    assert build_action_observations(session, _window()) == []

def test_sanction_delta_creates_authoritative_action_observation(session):
    _insert_sanction_delta(session, country="ES", delta=4)
    [point] = build_action_observations(session, _window())
    assert point.contour == Contour.ACTION
    assert point.authority == "registry"
```

- [ ] **Step 3: Run tests and verify failure**

Run: `python -m pytest tests/test_radar_observations.py -q`

Expected: FAIL because observation adapters do not exist.

- [ ] **Step 4: Implement media observation queries**

Read effective country and verified publisher through `article_country_facts`. Exclude duplicates, quarantined discoveries, and future-dated rows. Reuse stored story/event/entity evidence; do not run an LLM. Persist exact article/story IDs and a baseline denominator derived from successfully indexed national coverage.

- [ ] **Step 5: Implement action observation queries**

Map structured changes to stable subject keys:

```python
ACTION_SUBJECTS = {
    "sanctions_pressure": "policy:sanctions:russia",
    "un_votes": "diplomacy:un_alignment:russia",
    "trade_data": "economy:trade:russia",
    "ru_fossil_imports": "energy:imports:russia",
}
```

Annual or snapshot datasets must use their real effective/updated timestamp and expose low temporal resolution. No unchanged snapshot creates a new action. Official-media events may be stored as `reported`, but only registry/formal evidence or independent authoritative corroboration may create `confirmed` action evidence.

- [ ] **Step 6: Implement idempotent observation upserts**

Use a SHA-256 input hash over contour, country, subject, direction, observed time, metric, and sorted evidence IDs. `ON CONFLICT (input_hash) DO NOTHING` makes reruns safe. Return only the number of inserted records.

- [ ] **Step 7: Run focused and compatibility tests**

Run: `python -m pytest tests/test_radar_observations.py tests/test_stories.py tests/test_signal_evidence.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/radar/media.py src/radar/actions.py src/radar/repository.py tests/test_radar_observations.py
git commit -m "feat(radar): normalize media and action evidence"
```

---

### Task 4: Build country waves, meta-trends, T0 history, and shadow replay

**Files:**

- Create: `src/radar/grouping.py`
- Create: `src/radar/service.py`
- Create: `scripts/build_radar.py`
- Create: `tests/test_radar_grouping.py`
- Create: `tests/test_radar_service.py`
- Modify: `docker-compose.yml`

**Interfaces:**

- Produces: `assign_country_waves(observations, previous)`, `assign_meta_trends(waves, stories)`, `run_radar_cycle(session, as_of, shadow)`, and CLI flags `--as-of`, `--days`, `--shadow`, `--apply`, `--json-report`.
- Consumes: Tasks 1–3 domain, schema, and repository contracts.

- [ ] **Step 1: Write failing grouping tests**

```python
def test_one_meta_trend_keeps_independent_country_waves():
    result = assign_meta_trends([_wave("ES", t0=DAY_1), _wave("PT", t0=DAY_4)], [])
    assert len(result.meta_trends) == 1
    assert {w.country_code for w in result.meta_trends[0].waves} == {"ES", "PT"}
    assert result.meta_trends[0].t0_auto == DAY_1

def test_contradictory_direction_does_not_merge():
    result = assign_meta_trends([_wave("ES", direction="warming"), _wave("PT", direction="cooling")], [])
    assert len(result.meta_trends) == 2
```

- [ ] **Step 2: Write failing replay and audit tests**

Assert dry-run creates no rows, apply is idempotent, state events are append-only, and analyst T0 correction survives automatic recalculation:

```python
def test_radar_apply_is_idempotent_and_preserves_t0_override(session):
    first = run_radar_cycle(session, AS_OF, shadow=False)
    _override_t0(session, first.trend_id, ANALYST_T0)
    second = run_radar_cycle(session, AS_OF, shadow=False)
    assert second.inserted_observations == 0
    assert _trend(session, first.trend_id).t0_effective == ANALYST_T0
```

- [ ] **Step 3: Run tests and verify failure**

Run: `python -m pytest tests/test_radar_grouping.py tests/test_radar_service.py -q`

Expected: FAIL because grouping and service modules do not exist.

- [ ] **Step 4: Implement conservative country-wave assignment**

Group only compatible subject, country, contour, direction, and bounded time observations. Use stored stories/entities/events as anchors. Semantic score may strengthen a match but cannot override a contradictory canonical event or direction. Persist baseline snapshot, metrics, exact evidence, detector version, and state transition in one transaction.

- [ ] **Step 5: Implement meta-trend assignment and contour links**

Require compatible canonical subject and direction across countries. Keep local T0/state/confidence on members. Meta T0 is earliest confirmed member T0. Media/action alignment is a separate `radar_contour_links` row with `aligned`, `divergent`, or `insufficient` status and never rewrites member states.

- [ ] **Step 6: Implement shadow/apply CLI**

Default to `--shadow`. Require explicit `--apply` for writes. JSON report includes candidates, emerging, confirmed, rejected, collector-suppressed, country coverage, T0 distribution, evidence completeness, and protected row counts. Add a compose `radar-worker` profile using the temperature image but do not enable a perpetual schedule until shadow replay passes.

- [ ] **Step 7: Run focused tests and one local shadow replay**

Run: `python -m pytest tests/test_radar_grouping.py tests/test_radar_service.py -q`

Run: `python scripts/build_radar.py --days 90 --shadow --json-report /tmp/radar-shadow.json`

Expected: tests PASS; report is valid JSON; existing product row counts are unchanged.

- [ ] **Step 8: Commit**

```bash
git add src/radar/grouping.py src/radar/service.py scripts/build_radar.py tests/test_radar_grouping.py tests/test_radar_service.py docker-compose.yml
git commit -m "feat(radar): build replayable country and meta trends"
```

---

### Task 5: Add read-only radar and methodology APIs

**Files:**

- Create: `src/api/routes/radar.py`
- Modify: `src/api/routes/__init__.py`
- Modify: `src/api/main.py`
- Create: `tests/test_radar_api.py`
- Modify: `tests/test_methodology.py`

**Interfaces:**

- Produces: `GET /api/v2/radar`, `/api/v2/radar/trends/{public_id}`, `/api/v2/countries/{code}/radar`, `/api/v2/radar/trends/{public_id}/timeline`, `/api/v2/radar/trends/{public_id}/evidence`, `/api/v2/radar/coverage`, and `/api/v2/methodology/radar`.
- Consumes: persisted read models from `src.radar.service`; no provider or worker imports.

- [ ] **Step 1: Write failing route-contract tests**

```python
def test_radar_list_is_read_only_and_exposes_both_contours(client, seeded_radar):
    before = _radar_row_counts()
    response = client.get("/api/v2/radar?state=confirmed&limit=20")
    assert response.status_code == 200
    assert response.json()["items"][0]["contours"].keys() == {"media", "action"}
    assert _radar_row_counts() == before

def test_trend_evidence_preserves_trigger_and_context_roles(client, seeded_radar):
    body = client.get(f"/api/v2/radar/trends/{seeded_radar}/evidence").json()
    assert {item["role"] for item in body["items"]} >= {"trigger", "context"}
```

- [ ] **Step 2: Run API tests and verify failure**

Run: `python -m pytest tests/test_radar_api.py tests/test_methodology.py -q`

Expected: FAIL with missing routes.

- [ ] **Step 3: Implement typed serializers and filter-bound cursor pagination**

Follow search/story cursor signing and validation. List items expose state, thesis, direction, contour states, country waves, velocity, T0, detection/confirmation timestamps, coverage confidence, contradiction marker, one evidence preview, and `why_included`. Invalid filters return 422; missing trends return 404; absent action evidence returns `insufficient`, not an empty confirmed contour.

- [ ] **Step 4: Implement immutable methodology response**

Return detector version, 90-day baseline, 7-day acceleration, lifecycle gates, T0 fields, confidence factors, coverage hard-gate rule, action-independence rule, evidence roles, limitations, and update timestamp from a backend constant covered by tests.

- [ ] **Step 5: Run API and existing investigation tests**

Run: `python -m pytest tests/test_radar_api.py tests/test_methodology.py tests/test_investigation_flows.py tests/test_search.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/api/routes/radar.py src/api/routes/__init__.py src/api/main.py tests/test_radar_api.py tests/test_methodology.py
git commit -m "feat(api): expose early warning radar"
```

---

### Task 6: Add the feature-flagged radar UI and existing-page placements

**Files:**

- Create: `web/app/radar/page.tsx`
- Create: `web/app/radar/[id]/page.tsx`
- Create: `web/components/EarlyWarningPanel.tsx`
- Create: `web/components/TrendCard.tsx`
- Create: `web/components/TrendInvestigation.tsx`
- Modify: `web/components/SiteHeader.tsx`
- Modify: `web/app/page.tsx`
- Modify: `web/app/country/[code]/page.tsx`
- Modify: `web/app/stories/[id]/page.tsx`
- Modify: `web/components/SignalEvidence.tsx`
- Modify: `web/app/about/page.tsx`
- Modify: `web/lib/api.ts`
- Modify: `web/lib/types.ts`
- Modify: `web/lib/features.ts`
- Modify: `web/lib/features.server.ts`
- Modify: `docker-compose.yml`
- Create: `web/app/radar/page.test.tsx`
- Create: `web/app/radar/[id]/page.test.tsx`

**Interfaces:**

- Produces: `/radar`, `/radar/[id]`, `FEATURE_EARLY_WARNING_RADAR`, typed API clients, and reusable trend cards.
- Consumes: Task 5 API only.

- [ ] **Step 1: Write failing feature-flag and list-page tests**

Test fail-closed flag behavior, URL-backed filters, confirmed/exceptional-emerging selection, honest empty/error states, abort of stale requests, keyboard navigation, and safe evidence links.

```tsx
it("keeps ordinary emerging trends off the home panel", async () => {
  render(<EarlyWarningPanel trends={[ordinaryEmerging, criticalEmerging, confirmed]} />);
  expect(screen.queryByText(ordinaryEmerging.title)).not.toBeInTheDocument();
  expect(screen.getByText(criticalEmerging.title)).toBeInTheDocument();
  expect(screen.getByText(confirmed.title)).toBeInTheDocument();
});
```

- [ ] **Step 2: Write failing investigation-page tests**

Assert the page answers the eight approved questions in sequence, separates media/action contours, renders country waves and T0, labels contradictions and coverage gaps, and links evidence through `safeHttpUrl`.

- [ ] **Step 3: Run UI tests and verify failure**

Run: `cd web && npm test -- --run app/radar components/EarlyWarningPanel`

Expected: FAIL because radar routes/components do not exist.

- [ ] **Step 4: Add typed API contracts and fail-closed flag**

Extend `FeatureFlags` with `earlyWarningRadar: boolean`; read only `FEATURE_EARLY_WARNING_RADAR === "true"`. Add typed clients for list, detail, country waves, timeline, evidence, coverage, and methodology. Components never issue untyped ad-hoc fetches.

- [ ] **Step 5: Implement radar list and investigation routes**

Reuse the editorial header, existing `Plot`, evidence/limitation card patterns, cursor pagination, and URL-backed filter conventions. Use shareable `view=propagation|evidence|coverage|method` state. Respect reduced motion and focus management.

- [ ] **Step 6: Add compact placements without redesigning existing pages**

Add navigation `радар`; an eight-column home panel before stories; a full-width country panel after relationship dynamics and before stories; one related-trend card on signal detail; one analytical-trends block on story detail; and backend-sourced “Как работает радар” methodology on About.

- [ ] **Step 7: Run UI tests, type check, and build**

Run: `cd web && npm test -- --run`

Run: `cd web && npm run typecheck`

Run: `cd web && npm run build`

Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add web docker-compose.yml
git commit -m "feat(web): add early warning radar experience"
```

---

### Task 7: Production shadow gate, enablement, and rollback runbook

**Files:**

- Create: `scripts/audit_radar_wave1.py`
- Create: `tests/test_radar_wave1_audit.py`
- Create: `docs/release/early-warning-radar-wave1.md`
- Modify: `README.md`

**Interfaces:**

- Produces: machine-readable preflight/shadow/apply reports, protected-row comparison, public smoke checklist, and rollback procedure.
- Consumes: Tasks 1–6.

- [ ] **Step 1: Write failing audit contract tests**

Protected tables must include `articles`, `analysis`, `temperature`, `ru_index`, `signals`, `briefs`, `threads`, `thread_articles`, `stories`, and `story_articles`. The audit fails if any row count decreases, any legacy primary key changes, evidence completeness falls below the configured gate, or public GETs write rows.

- [ ] **Step 2: Run audit tests and verify failure**

Run: `python -m pytest tests/test_radar_wave1_audit.py -q`

Expected: FAIL because the audit script and release document do not exist.

- [ ] **Step 3: Implement read-only preflight and apply verification**

`audit_radar_wave1.py` supports `--phase before|shadow|after`, `--json`, and `--compare`. Reports include protected row counts, migration state, observation/trend counts, state distribution, contour completeness, evidence validity, coverage suppression, T0 sanity, duplicate public IDs, and notification idempotency.

- [ ] **Step 4: Write the exact production runbook**

Document:

1. database backup and protected-row snapshot;
2. deploy with `FEATURE_EARLY_WARNING_RADAR=false`;
3. migration verification;
4. 90-day shadow replay and audit review;
5. bounded apply;
6. API smoke tests while navigation remains hidden;
7. set `FEATURE_EARLY_WARNING_RADAR=true` only after gates pass;
8. web/api smoke tests for home, `/radar`, one trend, one country, one story, one signal, and About;
9. rollback by disabling the flag and stopping the radar worker without dropping tables or restoring old data.

- [ ] **Step 5: Run full local verification**

Run: `python -m pytest -q`

Run: `cd web && npm test -- --run && npm run typecheck && npm run build`

Run: `git diff --check`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/audit_radar_wave1.py tests/test_radar_wave1_audit.py docs/release/early-warning-radar-wave1.md README.md
git commit -m "docs: add radar wave one release gate"
```

- [ ] **Step 7: Deploy and verify production**

Push `main`, wait for the existing auto-deploy, then follow `docs/release/early-warning-radar-wave1.md` exactly. Do not report completion until the production API, public pages, protected row counts, radar build timestamp, and feature flag have all been verified on `massaraksh.tech`.

---

## Deferred to Separate Wave 2 Plans

Wave 1 intentionally does not hide these requirements; they are separated so the
first deployable radar is reviewable and reversible:

- append-only `source_fetch_events`, persisted publisher families/collections,
  and bounded adaptive polling;
- paragraph-sized `article_chunks`, MinHash LSH, chunk embeddings, profile-specific
  HNSW search, and exact passage results;
- feed rediscovery and Media Cloud-style research comparison/export views;
- calibrated Leiden community detection after production grouping evaluation;
- external notification channels beyond persisted idempotent notification events.

Each deferred subsystem gets its own approved plan before implementation and
must preserve the Wave 1 contracts above.
