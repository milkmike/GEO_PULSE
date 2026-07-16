# Reliable Thematic Lens Briefs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate all thematic briefs in the proxy-enabled background worker and make the home page distinguish cached, pending, and genuinely insufficient topic states.

**Architecture:** `scripts/generate_briefs.py` owns topic generation alongside world and country briefs. The public API reads cached topic rows only and returns a small status union; it never calls OpenRouter. The React client renders the returned status without collapsing provider/cache failures into an insufficient-data message.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, PostgreSQL, pytest, Next.js 15, React, TypeScript, Vitest.

## Global Constraints

- Do not add `HTTPS_PROXY` to the public API container.
- Keep the existing six-hour topic cache and the 16 keys in `src.pipeline.topics.TOPICS`.
- One failed topic must not stop other topic, world, or country briefs.
- Existing cached topic rows remain readable throughout rollout.
- Unknown topic slugs remain HTTP 404.
- Known topic states are exactly `ready`, `pending`, or `insufficient`.

---

### Task 1: Add cache-only topic brief domain functions

**Files:**
- Modify: `src/pipeline/briefs.py:425-550`
- Create: `tests/test_topic_briefs.py`

**Interfaces:**
- Consumes: existing `_last_brief(session, scope)`, `gather_topic_inputs(session, topic)`, and `TOPICS`.
- Produces: `read_cached_topic_brief(topic: str) -> dict | None` and `topic_has_inputs(topic: str) -> bool`.

- [ ] **Step 1: Write failing cache and availability tests**

Add focused fakes in `tests/test_topic_briefs.py` and assert the public functions do not call `chat`:

```python
def test_read_cached_topic_brief_never_generates(monkeypatch):
    monkeypatch.setattr(briefs, "get_session", lambda: FakeSessionContext(cached_topic_row()))
    monkeypatch.setattr(briefs, "chat", lambda *a, **k: pytest.fail("LLM called"))
    result = briefs.read_cached_topic_brief("culture_sport")
    assert result["content"] == "cached culture brief"
    assert result["cached"] is True


def test_topic_has_inputs_distinguishes_empty_topic(monkeypatch):
    monkeypatch.setattr(briefs, "get_session", lambda: FakeSessionContext(topic_count=3))
    assert briefs.topic_has_inputs("culture_sport") is True
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_topic_briefs.py -q`

Expected: failures because `read_cached_topic_brief` and `topic_has_inputs` do not exist.

- [ ] **Step 3: Implement cache-only reads**

Implement exactly these contracts in `src/pipeline/briefs.py`:

```python
def read_cached_topic_brief(topic: str) -> dict | None:
    scope = f"topic:{topic}"
    with get_session() as session:
        last = _last_brief(session, scope)
    if not last:
        return None
    return {
        "content": last.content,
        "model": last.model,
        "created_at": last.created_at.isoformat(),
        "cached": True,
        "citations": (last.meta or {}).get("citations", []),
    }


def topic_has_inputs(topic: str) -> bool:
    with get_session() as session:
        count = session.execute(text("""
            SELECT COUNT(*)
            FROM analysis a
            JOIN articles ar ON ar.id = a.article_id
            WHERE a.is_relevant = TRUE
              AND :topic = ANY(a.topics)
              AND ar.published_at > NOW() - INTERVAL '14 days'
        """), {"topic": topic}).scalar_one()
    return int(count or 0) > 0
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_topic_briefs.py -q`

Expected: all Task 1 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/briefs.py tests/test_topic_briefs.py
git commit -m "feat: expose cached topic brief state"
```

---

### Task 2: Generate all topics in the briefs worker

**Files:**
- Modify: `scripts/generate_briefs.py:1-70`
- Test: `tests/test_topic_briefs.py`

**Interfaces:**
- Consumes: `generate_topic_brief(topic, force=False)` and `TOPICS`.
- Produces: `generate_topic_briefs(force: bool = False) -> dict[str, int]` and CLI flag `--topics-only`.

- [ ] **Step 1: Write failing worker-isolation tests**

```python
def test_generate_topic_briefs_isolates_one_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(generate_briefs, "TOPICS", {"a": "A", "b": "B", "c": "C"})
    def fake(topic, force=False):
        calls.append((topic, force))
        if topic == "b":
            raise RuntimeError("provider down")
        return {"content": topic}
    monkeypatch.setattr(generate_briefs, "generate_topic_brief", fake)
    assert generate_briefs.generate_topic_briefs(force=True) == {
        "generated": 2, "empty": 0, "failed": 1,
    }
    assert calls == [("a", True), ("b", True), ("c", True)]
```

Also assert `run_pass()` calls world, topics, then country briefs even if one topic fails.

- [ ] **Step 2: Run the worker tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_topic_briefs.py -q`

Expected: failure because `generate_topic_briefs` and `--topics-only` are missing.

- [ ] **Step 3: Implement the bounded topic loop and CLI**

Add imports for `TOPICS` and `generate_topic_brief`, then implement:

```python
def generate_topic_briefs(force: bool = False) -> dict[str, int]:
    result = {"generated": 0, "empty": 0, "failed": 0}
    for topic in TOPICS:
        try:
            brief = generate_topic_brief(topic, force=force)
            result["generated" if brief else "empty"] += 1
        except Exception as exc:
            result["failed"] += 1
            logger.error("Topic brief %s failed: %s", topic, exc)
        time.sleep(1)
    return result
```

Call it after `generate_world_brief()` and before country briefs. Add `--topics-only`; in both loop and one-shot modes that flag must skip world/country work and call only `generate_topic_briefs()`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.venv/bin/python -m pytest tests/test_topic_briefs.py -q`

Expected: worker order, error isolation, and CLI routing pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/generate_briefs.py tests/test_topic_briefs.py
git commit -m "feat: refresh topic briefs in background worker"
```

---

### Task 3: Make the topic API cache-only and explicit

**Files:**
- Modify: `src/api/routes/world.py:875-887`
- Test: `tests/test_topic_briefs.py`

**Interfaces:**
- Consumes: `read_cached_topic_brief(topic)` and `topic_has_inputs(topic)`.
- Produces: JSON union `{status: "ready" | "pending" | "insufficient", topic, label, ...}`.

- [ ] **Step 1: Write failing FastAPI route tests**

Use `TestClient(src.api.main.app)` and monkeypatch the two domain functions:

```python
def test_topic_route_returns_cached_without_generating(monkeypatch):
    monkeypatch.setattr(briefs, "read_cached_topic_brief", lambda topic: cached_payload())
    monkeypatch.setattr(briefs, "topic_has_inputs", lambda topic: True)
    monkeypatch.setattr(briefs, "generate_topic_brief", lambda *a, **k: pytest.fail("LLM called"))
    response = TestClient(app).get("/api/v2/topics/culture_sport/brief")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


@pytest.mark.parametrize("has_inputs,status", [(True, "pending"), (False, "insufficient")])
def test_topic_route_distinguishes_missing_cache(monkeypatch, has_inputs, status):
    monkeypatch.setattr(briefs, "read_cached_topic_brief", lambda topic: None)
    monkeypatch.setattr(briefs, "topic_has_inputs", lambda topic: has_inputs)
    response = TestClient(app).get("/api/v2/topics/culture_sport/brief")
    assert response.status_code == (202 if has_inputs else 200)
    assert response.json()["status"] == status
```

- [ ] **Step 2: Run route tests and verify RED**

Run: `.venv/bin/python -m pytest tests/test_topic_briefs.py -q`

Expected: current route invokes generation or returns 404.

- [ ] **Step 3: Implement the status union**

Import and call cache-only functions. Return 200 for `ready` and `insufficient`, 202 via `JSONResponse` for `pending`, and retain 404 only for unknown topic slugs. Do not import or call `generate_topic_brief` from this route.

- [ ] **Step 4: Run route and regression tests**

Run: `.venv/bin/python -m pytest tests/test_topic_briefs.py tests/test_world_dossier.py -q`

Expected: all tests pass; no test observes an LLM call from the API.

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/world.py tests/test_topic_briefs.py
git commit -m "fix: keep topic brief API cache only"
```

---

### Task 4: Render honest thematic-lens states

**Files:**
- Modify: `web/lib/types.ts`
- Modify: `web/lib/api.ts:145-152`
- Modify: `web/app/page.tsx:25-115,190-225`
- Create: `web/app/TopicBrief.test.tsx`

**Interfaces:**
- Consumes: backend topic status union.
- Produces: `TopicBriefResponse` and visible Russian states for `ready`, `pending`, and `insufficient`.

- [ ] **Step 1: Write failing UI tests**

Render `HomePage` with a test filter control that selects `culture_sport`. Cover these responses separately:

```typescript
{ status: "pending", topic: "culture_sport", label: "Культура и спорт" }
{ status: "insufficient", topic: "culture_sport", label: "Культура и спорт" }
{ status: "ready", topic: "culture_sport", label: "Культура и спорт",
  content: "Свежий брифинг", model: "qwen", created_at: "2026-07-16T13:00:00Z",
  cached: true, citations: [] }
```

Assert pending displays `Тематический брифинг обновляется`, insufficient displays `Недостаточно данных по теме`, and ready renders the brief metadata.

- [ ] **Step 2: Run the UI test and verify RED**

Run: `npm test -- --run web/app/TopicBrief.test.tsx`

Expected: current client collapses pending/insufficient or tries to render missing content.

- [ ] **Step 3: Add the response union and rendering**

Add to `web/lib/types.ts`:

```typescript
export type TopicBriefResponse =
  | ({ status: "ready"; topic: string; label: string } & Brief)
  | { status: "pending" | "insufficient"; topic: string; label: string };
```

Change `api.topicBrief()` to return `TopicBriefResponse`. Store that union in `HomePage`; render Markdown only for `status === "ready"`, the updating copy for `pending`, and insufficient copy only for `insufficient`. A network exception must render `Не удалось загрузить тематический брифинг`, not insufficient data.

- [ ] **Step 4: Run frontend tests and production build**

Run: `npm test -- --run`

Expected: 114 existing tests plus the new topic-state tests pass.

Run: `npm run build`

Expected: Next.js production build succeeds with no TypeScript errors.

- [ ] **Step 5: Commit**

```bash
git add web/lib/types.ts web/lib/api.ts web/app/page.tsx web/app/TopicBrief.test.tsx
git commit -m "fix: distinguish topic brief availability states"
```

---

### Task 5: Release and prewarm all thematic lenses

**Files:**
- No repository file changes.
- Production backup: `/opt/geopulse/backups/pre-topic-brief-release-<timestamp>.dump`

**Interfaces:**
- Consumes: merged Tasks 1-4.
- Produces: all topic caches populated where source data exists.

- [ ] **Step 1: Run full verification before push**

Run: `.venv/bin/python -m pytest -q`

Expected: zero failures.

Run: `cd web && npm test -- --run && npm run build`

Expected: zero failures and successful build.

- [ ] **Step 2: Capture production invariants and backup briefs**

Record counts for articles, analysis, temperature, briefs by scope, and the current Git HEAD. Dump the `briefs` table to a timestamped custom-format backup and validate it with `pg_restore --list`.

- [ ] **Step 3: Push once and monitor the deployment**

Push the combined release commit once. Monitor `api`, `web`, and `briefs` until all are running and `/`, `/api/v2/health`, and `/api/v2/brief` return 200.

- [ ] **Step 4: Prewarm topic briefs through the proxy-enabled worker**

Run inside the production briefs container:

```bash
python scripts/generate_briefs.py --topics-only --force
```

Expected: each topic is logged as generated or empty; one failure does not abort the pass.

- [ ] **Step 5: Verify the reported bug and every topic endpoint**

Check all 16 `/api/v2/topics/{topic}/brief` endpoints. `culture_sport` must return HTTP 200 with `status=ready`, and API logs must contain no new OpenRouter 403. Confirm protected production counts did not decrease.

