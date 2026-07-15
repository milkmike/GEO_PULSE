# Signal Evidence Hybrid Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show two concrete source links on every signal card when available and provide a full drill-down page that distinguishes detector evidence from contextual news.

**Architecture:** Add one read-only batch loader that prefers persisted detector article IDs and otherwise selects country news from the 72 hours before the signal. Reuse the same serialized article shape in the signal list and detail endpoint, then render compact previews on cards and a clearly caveated context section on the existing detail page.

**Tech Stack:** FastAPI, SQLAlchemy text queries, PostgreSQL, Next.js 15, React 19, TypeScript, Vitest, pytest.

## Global Constraints

- Never mutate or delete existing articles, signal evidence, temperature history, or source data.
- Do not add a database migration or trigger a backfill/recalculation.
- Return at most two preview articles per signal list item.
- Use persisted `signal_evidence.article_ids` before contextual articles.
- Contextual articles are relevant, non-duplicate articles from sources in the signal country, published in the 72 hours ending at signal creation.
- Label contextual articles as possible context; never claim that correlation proves causation.
- Sanitize every public article URL with the existing `safe_public_url` helper.
- Avoid N+1 API or SQL calls on the signal feed.
- Keep the existing signal-detail page behind `FEATURE_SIGNAL_DETAIL` until deployment.

---

### Task 1: Batch signal article preview API

**Files:**
- Create: `src/api/signal_article_context.py`
- Modify: `src/api/routes/world.py:670-715`
- Test: `tests/test_world_dossier.py`

**Interfaces:**
- Consumes: signal rows with `id`, `country_code`, and `created_at`; persisted `signal_evidence.article_ids`; existing `safe_public_url`.
- Produces: `load_signal_article_previews(session, signal_rows, limit=2) -> dict[int, dict[str, Any]]` and list response field `evidence_preview` with `{kind, articles}`.

- [ ] **Step 1: Write failing list API tests**

Add tests that provide two session result sets: the existing signal rows and a
batch preview result. Assert that exact evidence returns `kind == "evidence"`,
context returns `kind == "context"`, unsafe URLs serialize as `None`, and the
second SQL call receives all signal IDs once rather than one call per signal.

```python
assert result["signals"][0]["evidence_preview"] == {
    "kind": "context",
    "articles": [{
        "id": 501,
        "title": "Правительство прокомментировало отношения с Россией",
        "url": "https://example.es/story",
        "published_at": "2026-07-15T01:30:00+00:00",
        "source_name": "Ejemplo",
        "country_code": "ES",
    }],
}
assert len(session.calls) == 2
assert session.calls[1][1]["signal_ids"] == [22]
assert session.calls[1][1]["lim"] == 2
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `.venv311/bin/python -m pytest tests/test_world_dossier.py -q`

Expected: FAIL because `evidence_preview` and the batch loader do not exist.

- [ ] **Step 3: Implement the batch loader**

Create `src/api/signal_article_context.py`. Execute one PostgreSQL query for all
requested signal IDs. Its `requested` CTE joins `signals` to `signal_evidence`;
its `exact_candidates` CTE unnests the first persisted article IDs with
ordinality; its `context_candidates` CTE runs only where the evidence array is
empty and filters:

```sql
ar.published_at > requested.created_at - INTERVAL '72 hours'
AND ar.published_at <= requested.created_at
AND ar.is_duplicate = FALSE
AND analysis.is_relevant = TRUE
AND source.country_code = requested.country_code
```

Rank exact rows by their ordinality. Rank context rows by action level,
absolute sentiment, reprint count, and publication time. Apply
`ROW_NUMBER() OVER (PARTITION BY signal_id ...) <= :lim`. Serialize rows with:

```python
{
    "id": int(row.article_id),
    "title": row.title,
    "url": safe_public_url(row.url),
    "published_at": row.published_at.isoformat() if row.published_at else None,
    "source_name": row.source_name,
    "country_code": row.country_code,
}
```

Initialize signals without rows as `{kind: "unavailable", articles: []}` and
reject limits outside `1..100` with `ValueError`. The signal-list caller always
passes `2`; the larger bound is reserved for one signal's detail page.

- [ ] **Step 4: Attach previews to the list response**

Inside the existing `with get_session()` block, call the loader once after the
signal query. Add to each serialized signal:

```python
"evidence_preview": previews.get(
    int(r.id), {"kind": "unavailable", "articles": []}
),
```

Keep every existing field and ordering rule unchanged.

- [ ] **Step 5: Run focused backend tests and commit**

Run: `.venv311/bin/python -m pytest tests/test_world_dossier.py tests/test_signal_evidence.py -q`

Expected: all tests pass.

Commit: `feat: add signal article previews`

---

### Task 2: Context articles on signal detail

**Files:**
- Modify: `src/api/routes/signal_detail.py:69-438`
- Modify: `web/lib/types.ts:576-650`
- Modify: `web/components/SignalEvidence.tsx:184-340`
- Test: `tests/test_signal_evidence.py`
- Test: `web/components/SignalEvidence.test.tsx`

**Interfaces:**
- Consumes: `load_signal_article_previews` from Task 1 and the existing exact `articles` array.
- Produces: detail response field `context_articles: SignalArticleReference[]` and a visually distinct `Новостной контекст` section.

- [ ] **Step 1: Write failing backend detail tests**

Extend the fake session in `tests/test_signal_evidence.py` with the batch-preview
query result. For a `tone_shift` with no persisted article IDs, assert:

```python
assert detail["articles"] == []
assert detail["context_articles"][0]["title"] == "Контекст сдвига"
```

For a detector with exact evidence, assert `context_articles == []` so exact
evidence is never duplicated as contextual material.

- [ ] **Step 2: Run backend detail tests and verify RED**

Run: `.venv311/bin/python -m pytest tests/test_signal_evidence.py -q`

Expected: FAIL because `context_articles` is absent.

- [ ] **Step 3: Load context only when exact evidence is absent**

In `SqlSignalDetailService.detail`, while the database session is open, call:

```python
context_articles = []
if not articles:
    preview = load_signal_article_previews(session, [signal], limit=20).get(
        int(_value(signal, "id")),
        {"kind": "unavailable", "articles": []},
    )
    if preview["kind"] == "context":
        context_articles = preview["articles"]
```

Pass it to `_serialize` and return it as `context_articles`. Exact detector
evidence remains in the existing `articles` field.

- [ ] **Step 4: Write failing frontend detail tests**

Add `context_articles` to the fixture. Assert the page renders heading
`Новостной контекст`, the source link, and this caveat:

```text
Публикации совпадают с окном сигнала и помогают исследовать возможные причины, но сами по себе не доказывают причинную связь.
```

Also assert that `Публикации-доказательства` is used for exact evidence.

- [ ] **Step 5: Run frontend detail tests and verify RED**

Run: `npm test -- --run components/SignalEvidence.test.tsx`

Expected: FAIL because the context section does not exist.

- [ ] **Step 6: Add the shared TypeScript article shape and render context**

Define `SignalArticleReference` once and use it for `SignalDetail.articles`,
`SignalDetail.context_articles`, and the list preview type. Extract the repeated
article-row markup in `SignalEvidence.tsx` into a small local component. Render
the contextual section only when `context_articles.length > 0`; keep safe
external-link behavior and do not call contextual rows evidence.

- [ ] **Step 7: Run focused tests and commit**

Run: `.venv311/bin/python -m pytest tests/test_signal_evidence.py -q`

Run: `npm test -- --run components/SignalEvidence.test.tsx`

Expected: all tests pass.

Commit: `feat: add news context to signal detail`

---

### Task 3: Hybrid signal cards

**Files:**
- Modify: `web/lib/types.ts:38-60`
- Modify: `web/components/SignalFeed.tsx:100-156`
- Test: `web/components/SignalFeed.test.tsx`

**Interfaces:**
- Consumes: `Signal.evidence_preview` from Task 1 and `SignalArticleReference` from Task 2.
- Produces: a compact article preview plus explicit `/signals/{id}` CTA without nested links.

- [ ] **Step 1: Write failing card tests**

Extend the signal fixture with two context articles and assert:

```tsx
expect(screen.getByText("Что происходило в момент сдвига")).toBeVisible();
expect(screen.getByRole("link", { name: /Ejemplo.*Правительство/i }))
  .toHaveAttribute("href", "https://example.es/story");
expect(screen.getByRole("link", { name: /Разобрать причины и источники/i }))
  .toHaveAttribute("href", "/signals/17");
```

Assert the card itself has role `article`, exact evidence uses heading
`На чём основан сигнал`, detail-disabled cards omit only the CTA, and unavailable
previews show the honest fallback.

- [ ] **Step 2: Run card tests and verify RED**

Run: `npm test -- --run components/SignalFeed.test.tsx`

Expected: FAIL because the article preview and CTA do not exist.

- [ ] **Step 3: Implement the hybrid card**

Always render the outer element as `<article>`. Below the facts, render at most
two preview rows. Each valid article URL is an external anchor with
`target="_blank"` and `rel="noopener noreferrer"`; missing URLs render as text.
Use these headings:

```ts
const heading = preview.kind === "evidence"
  ? "На чём основан сигнал"
  : "Что происходило в момент сдвига";
```

When `detailEnabled`, render a separate internal Link with the exact label
`Разобрать причины и источники →`. Preserve reduced-motion parity and the current
fact/status rendering.

- [ ] **Step 4: Run frontend checks and commit**

Run: `npm test -- --run components/SignalFeed.test.tsx components/SignalEvidence.test.tsx`

Run: `npm run build`

Expected: tests and production build pass.

Commit: `feat: make signal cards explainable`

---

### Task 4: Release verification and production deployment

**Files:**
- Modify only if required: `docs/release/investigation-search-stories.md`

**Interfaces:**
- Consumes: the three reviewed implementation commits and existing Docker Compose deployment.
- Produces: `FEATURE_SIGNAL_DETAIL=true` on the production web image plus verified API/web behavior.

- [ ] **Step 1: Run the complete relevant verification**

Run: `.venv311/bin/python -m pytest tests/test_world_dossier.py tests/test_signal_evidence.py tests/test_public_urls.py -q`

Run: `npm test -- --run components/SignalFeed.test.tsx components/SignalEvidence.test.tsx app/signals/[id]/page.test.tsx app/signals/[id]/SignalDetailClient.test.tsx lib/features.test.tsx`

Run: `npm run build`

Run: `git diff --check`

Expected: all tests pass, build succeeds, diff check is empty.

- [ ] **Step 2: Review production data safety**

Confirm the diff contains no migration, DELETE, TRUNCATE, UPDATE of historical
tables, collector/analyzer restart policy change, or volume replacement. Confirm
the deployment updates only API/web services.

- [ ] **Step 3: Merge, push, and deploy only API/web**

Merge the reviewed branch into `main`, push `main`, set
`FEATURE_SIGNAL_DETAIL=true` in the production build environment, rebuild the web
image with that build argument, and recreate API/web without `-v` and without
recreating PostgreSQL, Redis, collectors, analyzers, or temperature workers.

- [ ] **Step 4: Run production smoke checks**

Verify:

```text
GET /api/v2/signals?days=7&limit=10 -> 200 with evidence_preview
GET /api/v2/signals/{tone_shift_id} -> 200 with context_articles or exact articles
GET / -> 200 and card contains “Разобрать причины и источники”
GET /signals -> 200
GET /signals/{tone_shift_id} -> 200
```

Open one source URL from the card and confirm it is an HTTP(S) public link. Check
article count and latest temperature timestamp before and after deployment remain
present and non-decreasing.

- [ ] **Step 5: Record the release commit**

Commit any release-documentation-only change as:
`docs: record signal evidence release`
