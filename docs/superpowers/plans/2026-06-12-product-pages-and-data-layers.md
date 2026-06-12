# Product Pages & Data Layers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Brief citations, daily headlines block, /sources transparency page, language coverage, /about mission page, country dossier data (UN votes / trade / agreements), analytics feed tier — per spec `docs/superpowers/specs/2026-06-12-product-pages-and-data-layers-design.md`.

**Architecture:** Backend FastAPI (`src/api/routes/world.py`, prefix `/api/v2`) + pipeline (`src/pipeline/briefs.py`); frontend Next.js app router (`web/`). Each feature lands as atomic commit(s) to `main`; prod auto-deploys within 5 min (cron `deploy/auto-update.sh`). Verify each feature on https://massaraksh.tech after deploy.

**Tech Stack:** Python/FastAPI/SQLAlchemy(text SQL)/TimescaleDB, Next.js+TS+Tailwind, Plotly via `web/components/Plot.tsx`.

**Conventions (CLAUDE.md):** код/коммиты на английском; термометр v1 и API v1 не ломать; новые таблицы/колонки — идемпотентно и в `scripts/migrations/NNN_*.sql`, и в `data/init.sql`. Local python is 3.9 — pure helper modules must avoid `X | None` syntax (use `Optional`). Prod verification: `ssh geopulse-prod`, project dir `/opt/geopulse`.

**Security note:** article URLs come from third-party RSS feeds — treat as untrusted. Any URL rendered into HTML attributes must be scheme-checked (http/https only) and attribute-escaped; titles/sources must be HTML-escaped (see Task 4 Step 4).

**Implementation order:** T1→T2 (citations) → T3→T4 (headlines) → T5 (langs) → T6 (/sources) → T7 (/about) → T8 (UN votes data) → T9 (trade data) → T10 (dossier API) → T11 (dossier UI) → T12 (analytics tier). Frontend nav links land with their pages.

---

### Task 1: Citations helper module (TDD)

**Files:**
- Create: `src/pipeline/citations.py`
- Create: `tests/__init__.py` (empty), `tests/test_citations.py`
- Create: `requirements-dev.txt`

- [ ] **Step 1: Write the failing test**

`tests/test_citations.py`:
```python
from src.pipeline.citations import apply_citations


def test_keeps_valid_citations():
    content, used = apply_citations("Rost otnoshenij [1] i spad [2].", {1, 2, 3})
    assert content == "Rost otnoshenij [1] i spad [2]."
    assert used == {1, 2}


def test_strips_phantom_citations():
    content, used = apply_citations("Fakt [1], vydumka [9].", {1, 2})
    assert content == "Fakt [1], vydumka ."
    assert used == {1}


def test_no_citations_passthrough():
    content, used = apply_citations("Prosto tekst bez snosok.", {1})
    assert content == "Prosto tekst bez snosok."
    assert used == set()


def test_multi_digit_and_adjacent():
    content, used = apply_citations("Sobytie [10][11], eshche [2]", {2, 10, 11})
    assert used == {2, 10, 11}
```

`requirements-dev.txt`:
```
pytest==8.3.4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/miketkachenko/Dev/Projects/GEO PULSE" && python3 -m pip install -q pytest && python3 -m pytest tests/test_citations.py -v`
Expected: FAIL / ERROR — `ModuleNotFoundError: No module named 'src.pipeline.citations'`

- [ ] **Step 3: Write minimal implementation**

`src/pipeline/citations.py` (py3.9-compatible, no DB imports):
```python
"""Citation post-processing for AI briefs.

The LLM is shown numbered headlines [1..N] and asked to cite them.
After generation we strip citation markers that reference numbers we
never gave it (hallucinated), and report which numbers were used.
"""
import re
from typing import Set, Tuple

_CITE_RE = re.compile(r"\[(\d+)\]")


def apply_citations(content: str, valid_numbers: Set[int]) -> Tuple[str, Set[int]]:
    """Strip phantom [n] markers; return (cleaned content, used numbers)."""
    used = set()

    def repl(m):
        n = int(m.group(1))
        if n in valid_numbers:
            used.add(n)
            return m.group(0)
        return ""

    return _CITE_RE.sub(repl, content), used
```

- [ ] **Step 4: Run tests, verify pass**

Run: `python3 -m pytest tests/test_citations.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/pipeline/citations.py tests/ requirements-dev.txt
git commit -m "feat(briefs): citation post-processing helper"
```

---

### Task 2: Numbered inputs + citations in briefs pipeline

**Files:**
- Modify: `src/pipeline/briefs.py` (gather_world_inputs ~L77-150, gather_country_inputs ~L153-229, prompts L20-58, generate_world_brief L243-272, generate_country_brief L275-314, _last_brief L67-74)

- [ ] **Step 1: world inputs — add urls and numbering**

In `gather_world_inputs`: extend the headlines SQL (L100-115) to select `ar.url` and source name:
```sql
SELECT s.country_code, ar.title, ar.url, s.name AS source_name,
       a.sentiment, a.action_level
FROM analysis a
JOIN articles ar ON a.article_id = ar.id
JOIN sources s ON ar.source_id = s.id
WHERE ar.published_at > NOW() - INTERVAL '24 hours'
  AND a.is_relevant = TRUE AND a.action_level >= 3
ORDER BY a.action_level DESC, ar.reprint_count DESC
LIMIT 15
```
Replace the return-dict construction for headlines (keep `index_movers`/`signals` comprehensions exactly as they are now) with numbered entries + a citations registry. GDELT samples get numbers only when they carry a url:
```python
    citations = []

    def _cite(title, url, source, country):
        n = len(citations) + 1
        citations.append({"n": n, "title": title, "url": url,
                          "source": source, "country": country})
        return n

    tier1_headlines = []
    for r in headlines:
        entry = {"country": country_name_ru(r.country_code), "title": r.title,
                 "sentiment": float(r.sentiment or 0),
                 "action_level": int(r.action_level or 1)}
        if r.url:
            entry["n"] = _cite(r.title, r.url, r.source_name, r.country_code)
        tier1_headlines.append(entry)

    world_headlines = []
    for r in gdelt_top:
        for s in (r.article_samples or [])[:2]:
            entry = {"country": country_name_ru(r.country_code),
                     "title": s.get("title", ""),
                     "tone": float(r.tone_avg) if r.tone_avg is not None else None}
            if s.get("url"):
                entry["n"] = _cite(s.get("title", ""), s["url"], "GDELT", r.country_code)
            world_headlines.append(entry)

    return {
        "index_movers": <existing movers comprehension unchanged>,
        "signals": <existing signals comprehension unchanged>,
        "tier1_headlines": tier1_headlines,
        "world_headlines": world_headlines[:12],
        "citations": citations,
    }
```

- [ ] **Step 2: country inputs — same pattern**

In `gather_country_inputs`: add `ar.url, s.name AS source_name` to its headlines SQL (L175-190), build `citations` with the same `_cite` pattern for `own_media_headlines` and for `gdelt_headlines` items that have urls, add `"citations": citations` to the returned dict.

- [ ] **Step 3: prompts — citation instruction**

Append to BOTH `WORLD_BRIEF_PROMPT` and `COUNTRY_BRIEF_PROMPT` (before the final "Пиши сжато" line):
```
Заголовки в данных пронумерованы полем "n". Каждое фактическое утверждение,
основанное на заголовке, помечай сноской [n] (например: «...растёт напряжённость [3]»).
Используй ТОЛЬКО номера из данных, не выдумывай. Утверждения из индексов и
сигналов сносок не требуют.
```

- [ ] **Step 4: generate functions — postprocess + persist meta**

In `generate_world_brief`: pop citations out of the hash/prompt payload, post-process, save to meta:
```python
        inputs = gather_world_inputs(session)
        citations = inputs.pop("citations", [])
        ...
        # after: content, model = chat(...)
        from src.pipeline.citations import apply_citations
        content, used = apply_citations(content, {c["n"] for c in citations})
        ...
        _save_brief(session, "world", content, model, source_hash,
                    {"movers": len(inputs["index_movers"]),
                     "signals": len(inputs["signals"]),
                     "citations": [{**c, "used": c["n"] in used} for c in citations]})
```
Note: headline entries keep their `"n"` inside `inputs` — so the hash stays
deterministic and the LLM sees the numbers; only the url-bearing registry is
popped. Same change in `generate_country_brief` (its `_save_brief(..., {})`
becomes `_save_brief(..., {"citations": [...]})`); its cached-return paths must
also return citations — so extend `_last_brief` SQL to select `meta` and the
cached returns to include `"citations": (last.meta or {}).get("citations", [])`.
Non-cached returns include `"citations"` from the fresh list.

- [ ] **Step 5: world brief endpoint passthrough check**

`/api/v2/brief` (world.py L429-442) already returns `meta` — no change.
`/api/v2/countries/{code}/brief` (L415-426) returns `{**brief}` — now carries `citations`. No change needed; just confirm by reading the code.

- [ ] **Step 6: Commit, push, deploy-verify**

```bash
git add src/pipeline/briefs.py
git commit -m "feat(briefs): numbered headline citations in world and country briefs"
git push origin main
```
Wait ≤5 min for auto-deploy (`ssh geopulse-prod 'tail -2 /var/log/geopulse-deploy.log'`). Check whether `scripts/generate_briefs.py` has a `--force` flag (read its argparse); if absent, add one that passes `force=True` to both generate functions (include in this commit). Then:
```bash
ssh geopulse-prod 'cd /opt/geopulse && docker compose run --rm briefs python scripts/generate_briefs.py --force 2>&1 | tail -5'
curl -s https://massaraksh.tech/api/v2/brief | python3 -c "import json,sys; d=json.load(sys.stdin); print(len((d.get('meta') or {}).get('citations', [])), 'citations')"
```
Expected: citations count > 0.

---

### Task 3: `/api/v2/headlines` endpoint

**Files:**
- Modify: `src/api/routes/world.py` (add route near `/signals`, L445)

- [ ] **Step 1: Implement route**

```python
@router.get("/headlines")
def world_headlines(hours: int = Query(24, ge=1, le=168),
                    tier: Optional[str] = None,
                    country: Optional[str] = None,
                    limit: int = Query(20, ge=1, le=100)):
    """Top relevant headlines across all countries (main page 'news of the day')."""
    conditions = ["a.is_relevant = TRUE",
                  "ar.published_at > NOW() - make_interval(hours => :h)",
                  "ar.url IS NOT NULL"]
    params: dict = {"h": hours, "lim": limit}
    if tier:
        conditions.append("s.tier = :tier")
        params["tier"] = tier
    if country:
        conditions.append("s.country_code = :cc")
        params["cc"] = country.upper()

    with get_session() as session:
        rows = session.execute(
            text(f"""
                SELECT ar.title, ar.url, s.name AS source_name, s.tier,
                       s.country_code, ar.published_at,
                       a.sentiment, a.action_level
                FROM analysis a
                JOIN articles ar ON a.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE {' AND '.join(conditions)}
                ORDER BY a.action_level DESC NULLS LAST,
                         ar.reprint_count DESC NULLS LAST,
                         ar.published_at DESC
                LIMIT :lim
            """), params).fetchall()

    return {"headlines": [
        {"title": r.title, "url": r.url, "source": r.source_name, "tier": r.tier,
         "country_code": r.country_code,
         "country_name": country_name_ru(r.country_code),
         "flag": (COUNTRIES.get(r.country_code) or {}).get("flag", ""),
         "published_at": r.published_at.isoformat() if r.published_at else None,
         "sentiment": float(r.sentiment) if r.sentiment is not None else None,
         "action_level": int(r.action_level or 1)}
        for r in rows], "total": len(rows)}
```
(`country_name_ru`, `COUNTRIES`, `Query`, `Optional` are already imported in world.py — verify imports at top of file.)

- [ ] **Step 2: Commit, push, deploy-verify**

```bash
git add src/api/routes/world.py
git commit -m "feat(api): /api/v2/headlines — top relevant headlines with tier/country filters"
git push origin main
```
After deploy: `curl -s "https://massaraksh.tech/api/v2/headlines?hours=24&limit=5" | python3 -m json.tool | head -30`
Expected: 200, items with title/url/flag/country_name.

---

### Task 4: «Новости дня» block + citations rendering on frontend

**Files:**
- Create: `web/components/HeadlinesFeed.tsx`
- Modify: `web/lib/types.ts`, `web/lib/api.ts`, `web/components/Markdown.tsx`, `web/app/page.tsx` (grid L114-142), `web/app/country/[code]/page.tsx` (Markdown usage)

- [ ] **Step 1: types + api client**

`web/lib/types.ts` — extend `Headline` (L79-90) with:
```ts
  country_code?: string;
  country_name?: string;
  flag?: string;
```
`web/lib/api.ts` — add to `api`:
```ts
  worldHeadlines: (hours = 24, limit = 20) =>
    get<{ headlines: Headline[]; total: number }>(
      `/api/v2/headlines?hours=${hours}&limit=${limit}`),
```

- [ ] **Step 2: component**

`web/components/HeadlinesFeed.tsx`:
```tsx
import type { Headline } from "@/lib/types";
import { fmtDate } from "@/lib/format";

function sentDot(s: number | null | undefined): string {
  if (s == null) return "bg-zinc-600";
  if (s > 0.3) return "bg-emerald-500";
  if (s < -0.3) return "bg-red-500";
  return "bg-yellow-500";
}

export default function HeadlinesFeed({ items }: { items: Headline[] }) {
  if (!items.length)
    return <div className="px-4 py-2 text-xs text-dim">Нет свежих заголовков</div>;
  return (
    <ul className="divide-y divide-white/5">
      {items.map((h, i) => (
        <li key={i} className="flex gap-2 px-4 py-2">
          <span className="shrink-0 text-sm leading-5">{h.flag || "🌐"}</span>
          <div className="min-w-0">
            <a href={h.url ?? "#"} target="_blank" rel="noopener noreferrer"
               className="block truncate text-[13px] leading-5 hover:text-accent"
               title={h.title}>
              {h.title}
            </a>
            <div className="flex items-center gap-2 text-[11px] text-dim">
              <span className={`inline-block h-1.5 w-1.5 rounded-full ${sentDot(h.sentiment)}`} />
              <span className="truncate">{h.source}</span>
              <span>·</span>
              <span>{h.country_name}</span>
              {h.published_at && (<><span>·</span><span>{fmtDate(h.published_at)}</span></>)}
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
```
(JSX renders text/attrs escaped by React — URLs go through `href` only. Check `fmtDate` exists in `web/lib/format.ts`.)

- [ ] **Step 3: main page wiring**

`web/app/page.tsx`: add state + load (inside existing `load()` so it refreshes each 120s):
```tsx
const [headlines, setHeadlines] = useState<Headline[]>([]);
// in load():
api.worldHeadlines().then((d) => setHeadlines(d.headlines)).catch(() => {});
```
Change bottom grid `lg:grid-cols-2` → `lg:grid-cols-3` and add as FIRST section:
```tsx
<section className="card">
  <div className="card-title px-4 pb-1 pt-3">Главные новости дня</div>
  <div className="max-h-[340px] overflow-y-auto">
    <HeadlinesFeed items={headlines} />
  </div>
</section>
```

- [ ] **Step 4: Citations rendering (closes Feature 1 frontend)**

`web/lib/types.ts`:
```ts
export interface Citation {
  n: number; title: string; url: string; source: string;
  country: string; used?: boolean;
}
```
extend `Brief` with BOTH `meta?: { citations?: Citation[] } | null;` AND top-level `citations?: Citation[] | null;` — the world endpoint nests citations under `meta`, the country endpoint returns them top-level. Pass to Markdown as `citations={brief.citations ?? brief.meta?.citations}` in both pages.

`web/components/Markdown.tsx` — full new version. SECURITY: citation url/title
come from third-party RSS via our DB — escape attribute values and allow only
http/https URLs before inserting into the HTML string:
```tsx
import type { Citation } from "@/lib/types";

function escAttr(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

function safeHttpUrl(u: string): string | null {
  try {
    const parsed = new URL(u);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : null;
  } catch {
    return null;
  }
}

function mdToHtml(text: string, cites?: Map<number, Citation>): string {
  const esc = text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  let html = esc
    .replace(/^### (.*)$/gm, '<h4 class="mt-3 mb-1 text-[13px] font-semibold text-accent">$1</h4>')
    .replace(/^## (.*)$/gm, '<h3 class="mt-3 mb-1 text-[13px] font-semibold text-accent">$1</h3>')
    .replace(/^# (.*)$/gm, '<h3 class="mt-3 mb-1 text-sm font-semibold text-accent">$1</h3>')
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/^[-*] (.*)$/gm, '<li class="ml-4 list-disc">$1</li>')
    .replace(/\n{2,}/g, "<br/>");
  if (cites?.size) {
    html = html.replace(/\[(\d+)\]/g, (m, num) => {
      const c = cites.get(Number(num));
      if (!c) return m;
      const url = safeHttpUrl(c.url);
      if (!url) return m;
      const t = escAttr(`${c.title} — ${c.source}`);
      return `<sup><a href="${escAttr(url)}" target="_blank" rel="noopener noreferrer" title="${t}" class="text-accent hover:underline">${num}</a></sup>`;
    });
  }
  return html;
}

export default function Markdown({ text, citations, className = "" }:
  { text: string; citations?: Citation[] | null; className?: string }) {
  const map = citations?.length
    ? new Map(citations.map((c) => [c.n, c])) : undefined;
  return (
    <div className={`text-[13px] leading-relaxed ${className}`}
         dangerouslySetInnerHTML={{ __html: mdToHtml(text, map) }} />
  );
}
```
(Keeps the existing escape-first approach of this file; the only inserted
attribute values pass through `escAttr` + `safeHttpUrl`.)

In `web/app/page.tsx` brief block: `<Markdown text={brief.content} citations={brief.meta?.citations} />`. Same in `web/app/country/[code]/page.tsx` where the dossier brief renders (find `<Markdown` usage; its Brief object must come from the v2 endpoint that now includes `citations` — pass `citations={dossierBrief.citations}` if the country endpoint returns them at top level rather than under meta; check actual response shape from Task 2 Step 4: country brief returns top-level `citations`).

- [ ] **Step 5: Build check, commit, push, verify**

Run: `cd web && npm run build 2>&1 | tail -5` — expected: compiled successfully.
```bash
git add web/
git commit -m "feat(web): daily headlines block + clickable brief citations"
git push origin main
```
After deploy: open https://massaraksh.tech — 3 columns at bottom, headlines clickable; brief shows superscript links (a citation-bearing brief was force-generated in Task 2 Step 6).

---

### Task 5: Native languages registry

**Files:**
- Modify: `src/countries.py` (after L280 `COUNTRIES = ...`), meta endpoint in `src/api/routes/world.py`

- [ ] **Step 1: add `_NATIVE_LANGS` + merge**

After `COUNTRIES` construction in `src/countries.py`:
```python
# ISO 639-1 native language(s) per country — powers the /sources coverage matrix.
_NATIVE_LANGS = {
    "KZ": ["kk", "ru"], "BY": ["be", "ru"], "AM": ["hy"], "AZ": ["az"],
    "GE": ["ka"], "MD": ["ro"], "UZ": ["uz"], "KG": ["ky", "ru"],
    "TJ": ["tg"], "TM": ["tk"],
    "GB": ["en"], "DE": ["de"], "FR": ["fr"], "IT": ["it"], "ES": ["es"],
    "PT": ["pt"], "NL": ["nl"], "BE": ["nl", "fr"], "AT": ["de"],
    "CH": ["de", "fr", "it"], "SE": ["sv"], "NO": ["no"], "FI": ["fi"],
    "DK": ["da"], "IE": ["en", "ga"], "IS": ["is"],
    "UA": ["uk"], "PL": ["pl"], "CZ": ["cs"], "SK": ["sk"], "HU": ["hu"],
    "RO": ["ro"], "BG": ["bg"], "EE": ["et"], "LV": ["lv"], "LT": ["lt"],
    "RS": ["sr"], "GR": ["el"], "CY": ["el"], "HR": ["hr"], "SI": ["sl"],
    "BA": ["bs", "sr", "hr"], "MK": ["mk"], "ME": ["sr"], "AL": ["sq"],
    "US": ["en"], "CA": ["en", "fr"], "MX": ["es"],
    "BR": ["pt"], "AR": ["es"], "VE": ["es"], "CU": ["es"], "NI": ["es"],
    "CL": ["es"], "CO": ["es"], "PE": ["es"], "BO": ["es"],
    "TR": ["tr"], "IR": ["fa"], "IL": ["he"], "SA": ["ar"], "AE": ["ar"],
    "QA": ["ar"], "IQ": ["ar"], "SY": ["ar"], "EG": ["ar"],
    "CN": ["zh"], "JP": ["ja"], "KR": ["ko"], "KP": ["ko"], "MN": ["mn"],
    "TW": ["zh"],
    "IN": ["hi", "en"], "PK": ["ur", "en"], "AF": ["ps", "fa"],
    "BD": ["bn"], "LK": ["si", "ta"],
    "ID": ["id"], "VN": ["vi"], "TH": ["th"], "MY": ["ms"],
    "SG": ["en", "zh"], "PH": ["en", "tl"], "MM": ["my"],
    "ZA": ["en", "af"], "NG": ["en"], "ET": ["am"], "KE": ["en", "sw"],
    "DZ": ["ar", "fr"], "MA": ["ar", "fr"], "TN": ["ar", "fr"],
    "LY": ["ar"], "SD": ["ar"], "ML": ["fr"], "NE": ["fr"], "BF": ["fr"],
    "CF": ["fr"], "AU": ["en"], "NZ": ["en"],
}
assert set(_NATIVE_LANGS) == set(COUNTRIES), "langs registry must cover all countries"
for _code, _langs in _NATIVE_LANGS.items():
    COUNTRIES[_code]["langs"] = _langs
```

- [ ] **Step 2: expose in /api/v2/meta**

Find the `/meta` route in world.py (search `@router.get("/meta")`); in its per-country payload add `"langs": c.get("langs", [])`.

- [ ] **Step 3: commit, push, verify**

```bash
git add src/countries.py src/api/routes/world.py
git commit -m "feat(registry): native languages per country, exposed via /api/v2/meta"
git push origin main
# after deploy:
curl -s https://massaraksh.tech/api/v2/meta | python3 -c "import json,sys; d=json.load(sys.stdin); cs=d['countries']; print(sum(1 for c in cs if c.get('langs')), 'of', len(cs))"
```
Expected: `99 of 99`. (Local import check on py3.9 may fail due to 3.10 syntax elsewhere in the module — prod verification is authoritative.)

---

### Task 6: `/sources` page

**Files:**
- Modify: `web/lib/types.ts`, `web/lib/api.ts`, `web/app/page.tsx` (nav, L84-87)
- Create: `web/app/sources/page.tsx`

- [ ] **Step 1: types + api**

`web/lib/types.ts`:
```ts
export interface SourceRow {
  id: number; name: string; url: string; country_code: string;
  source_type: string; language: string | null; active: boolean;
  tier: string; article_count: number; last_collected: string | null;
  relevant_count: number; avg_sentiment: number | null;
}
export interface SourceHealthRow {
  source_id: number; status: "OK" | "STALE" | "DEAD";
  last_article_at: string | null; articles_30d: number;
}
```
(Check the existing `Meta` type: its countries entries need `code/name/flag/langs` — extend if missing, aligning with the real `/api/v2/meta` response.)

`web/lib/api.ts`:
```ts
  sources: () => get<{ sources: SourceRow[]; total: number }>("/api/v1/sources"),
  healthSources: () =>
    get<{ sources: SourceHealthRow[]; total: number }>("/api/v2/health/sources"),
```
(import the new types in api.ts header)

- [ ] **Step 2: page component**

`web/app/sources/page.tsx` — full implementation:
```tsx
"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { fmtDate } from "@/lib/format";
import type { Meta, SourceHealthRow, SourceRow } from "@/lib/types";

const TIERS: Record<string, { label: string; cls: string }> = {
  official: { label: "Официальный", cls: "bg-red-500/15 text-red-400" },
  mainstream: { label: "Мейнстрим", cls: "bg-blue-500/15 text-blue-400" },
  independent: { label: "Независимый", cls: "bg-emerald-500/15 text-emerald-400" },
  social: { label: "Соцмедиа", cls: "bg-cyan-500/15 text-cyan-400" },
  domestic_opposition: { label: "Оппозиция", cls: "bg-yellow-500/15 text-yellow-400" },
  western_proxy: { label: "Западный прокси", cls: "bg-zinc-500/15 text-zinc-400" },
  analytics: { label: "Аналитика", cls: "bg-purple-500/15 text-purple-400" },
};
const STATUS_CLS: Record<string, string> = {
  OK: "text-emerald-400", STALE: "text-yellow-400", DEAD: "text-red-400",
};

export default function SourcesPage() {
  const [sources, setSources] = useState<SourceRow[]>([]);
  const [health, setHealth] = useState<Map<number, SourceHealthRow>>(new Map());
  const [meta, setMeta] = useState<Meta | null>(null);
  const [loading, setLoading] = useState(true);
  const [fCountry, setFCountry] = useState<string | null>(null);
  const [fTier, setFTier] = useState<string | null>(null);
  const [fLang, setFLang] = useState<string | null>(null);
  const [fStatus, setFStatus] = useState<string | null>(null);
  const [showMatrix, setShowMatrix] = useState(false);

  useEffect(() => {
    Promise.all([api.sources(), api.healthSources(), api.meta()])
      .then(([s, h, m]) => {
        setSources(s.sources);
        setHealth(new Map(h.sources.map((r) => [r.source_id, r])));
        setMeta(m);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const countries = useMemo(
    () => [...new Set(sources.map((s) => s.country_code))].sort(), [sources]);
  const languages = useMemo(
    () => [...new Set(sources.map((s) => s.language).filter(Boolean))].sort() as string[],
    [sources]);

  const filtered = useMemo(() => sources.filter((s) => {
    const st = health.get(s.id)?.status ?? (s.active ? "DEAD" : "OFF");
    return (!fCountry || s.country_code === fCountry)
      && (!fTier || s.tier === fTier)
      && (!fLang || s.language === fLang)
      && (!fStatus || st === fStatus);
  }).sort((a, b) => b.article_count - a.article_count),
    [sources, health, fCountry, fTier, fLang, fStatus]);

  const totalArticles = sources.reduce((t, s) => t + s.article_count, 0);

  // coverage matrix: country -> has ru / en / native active source
  const matrix = useMemo(() => {
    if (!meta) return [];
    const byCountry = new Map<string, SourceRow[]>();
    for (const s of sources.filter((x) => x.active)) {
      const arr = byCountry.get(s.country_code) ?? [];
      arr.push(s); byCountry.set(s.country_code, arr);
    }
    return meta.countries.map((c) => {
      const srcs = byCountry.get(c.code) ?? [];
      const langs = new Set(srcs.map((s) => s.language));
      const native = (c.langs ?? []).some((l) => langs.has(l));
      return { code: c.code, name: c.name, flag: c.flag,
               ru: langs.has("ru"), en: langs.has("en"), native };
    });
  }, [meta, sources]);

  if (loading) return <main className="mx-auto max-w-[1200px] px-3 py-8 text-dim">Загрузка…</main>;

  const chip = (active: boolean) =>
    `cursor-pointer rounded px-2 py-0.5 text-[11px] ${active ? "bg-accent/20 text-accent" : "bg-white/5 text-dim hover:text-fg"}`;

  return (
    <main className="mx-auto max-w-[1200px] px-3 pb-8">
      <header className="flex flex-wrap items-center gap-3 py-3">
        <h1 className="text-base font-semibold tracking-wider">📡 Источники</h1>
        <nav className="ml-auto text-xs text-dim">
          <Link href="/" className="hover:text-accent">← на главную</Link>
        </nav>
      </header>
      <p className="mb-3 max-w-3xl text-[13px] text-dim">
        Все источники платформы — с тиром доверия, языком и живостью. Мы показываем
        и работающие, и замолчавшие фиды: прозрачность важнее красивой статистики.
      </p>

      <div className="mb-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {[["Источников", sources.length], ["Активных", sources.filter((s) => s.active).length],
          ["Статей собрано", totalArticles.toLocaleString("ru")], ["Стран", countries.length],
        ].map(([label, v]) => (
          <div key={String(label)} className="card px-4 py-3">
            <div className="text-xl font-semibold">{v}</div>
            <div className="text-[11px] uppercase text-dim">{label}</div>
          </div>
        ))}
      </div>

      <section className="card mb-3 px-4 py-3">
        <button className="text-[13px] text-accent" onClick={() => setShowMatrix(!showMatrix)}>
          {showMatrix ? "▾" : "▸"} Матрица языкового покрытия (ru / en / native)
        </button>
        {showMatrix && (
          <div className="mt-2 grid max-h-[300px] grid-cols-2 gap-x-6 overflow-y-auto sm:grid-cols-3 lg:grid-cols-4">
            {matrix.map((r) => (
              <button key={r.code} onClick={() => setFCountry(r.code)}
                className="flex items-center justify-between border-b border-white/5 py-1 text-left text-[12px] hover:bg-white/5">
                <span className="truncate">{r.flag} {r.name}</span>
                <span className="shrink-0 font-mono">
                  <span className={r.ru ? "text-emerald-400" : "text-zinc-600"}>ru</span>{" "}
                  <span className={r.en ? "text-emerald-400" : "text-zinc-600"}>en</span>{" "}
                  <span className={r.native ? "text-emerald-400" : "text-zinc-600"}>nat</span>
                </span>
              </button>
            ))}
          </div>
        )}
      </section>

      <div className="mb-3 flex flex-wrap items-center gap-1.5 text-[11px]">
        <select value={fCountry ?? ""} onChange={(e) => setFCountry(e.target.value || null)}
                className="rounded bg-white/5 px-2 py-1">
          <option value="">Все страны</option>
          {countries.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        {Object.entries(TIERS).map(([k, t]) => (
          <span key={k} className={chip(fTier === k)} onClick={() => setFTier(fTier === k ? null : k)}>{t.label}</span>
        ))}
        <select value={fLang ?? ""} onChange={(e) => setFLang(e.target.value || null)}
                className="rounded bg-white/5 px-2 py-1">
          <option value="">Все языки</option>
          {languages.map((l) => <option key={l} value={l}>{l}</option>)}
        </select>
        {["OK", "STALE", "DEAD"].map((s) => (
          <span key={s} className={chip(fStatus === s)} onClick={() => setFStatus(fStatus === s ? null : s)}>{s}</span>
        ))}
        <span className="ml-auto text-dim">{filtered.length} из {sources.length}</span>
      </div>

      <section className="card divide-y divide-white/5">
        {filtered.map((s) => {
          const h = health.get(s.id);
          const t = TIERS[s.tier] ?? { label: s.tier, cls: "bg-white/5 text-dim" };
          return (
            <div key={s.id} className="flex flex-wrap items-center gap-2 px-4 py-2 text-[13px]">
              <a href={s.url} target="_blank" rel="noopener noreferrer"
                 className="min-w-0 flex-1 truncate hover:text-accent">{s.name}</a>
              <span className="text-dim">{s.country_code}</span>
              <span className={`rounded px-1.5 py-0.5 text-[10px] ${t.cls}`}>{t.label}</span>
              <span className="w-6 text-dim">{s.language ?? "—"}</span>
              <span className="w-20 text-right text-dim">{s.article_count.toLocaleString("ru")} ст.</span>
              <span className="w-24 text-right text-[11px] text-dim">
                {h?.last_article_at ? fmtDate(h.last_article_at) : s.last_collected ? fmtDate(s.last_collected) : "—"}
              </span>
              <span className={`w-12 text-right text-[11px] font-semibold ${STATUS_CLS[h?.status ?? ""] ?? "text-zinc-600"}`}>
                {s.active ? (h?.status ?? "—") : "выкл"}
              </span>
            </div>
          );
        })}
      </section>
    </main>
  );
}
```
(Adapt `Meta` usage to the real response field names. Source URLs render via React `href` — React escapes attributes; no raw HTML here.)

- [ ] **Step 3: nav link on main page**

`web/app/page.tsx` nav (L84-87): add before «все сигналы»:
```tsx
<Link href="/sources" className="hover:text-accent">источники</Link>
```

- [ ] **Step 4: build, commit, push, verify**

`cd web && npm run build` → success.
```bash
git add web/
git commit -m "feat(web): /sources transparency page with tiers, liveness and language coverage matrix"
git push origin main
```
Verify: https://massaraksh.tech/sources — metrics, filters work, matrix toggles.

---

### Task 7: `/about` page

**Files:**
- Create: `web/app/about/page.tsx`
- Modify: `web/app/page.tsx` (nav)

- [ ] **Step 1: page**

Implement `web/app/about/page.tsx` as a mostly-static page using the FULL copy from `docs/superpowers/specs/2026-06-12-about-page-copy.md` (each `##` section → `<section className="card px-5 py-4">` with the same v2 styles as other pages; the levels table → simple grid rows). Live numbers: fetch `api.sources()` + `api.meta()` client-side; hero strip shows `{sources.length}` источников / `{meta.countries.length}` стран / `{totalArticles.toLocaleString("ru")}` статей; fall back to the copy's static numbers while loading. RRI scale: horizontal flex bar of 6 colored segments (hostile→ally: `bg-red-600, bg-orange-500, bg-yellow-500, bg-zinc-400, bg-emerald-400, bg-emerald-600`) labeled with the ranges from the copy table. Links: `/sources` via `<Link>`, GitHub external `<a target="_blank" rel="noopener noreferrer">`. All text is static JSX — no dangerouslySetInnerHTML.

- [ ] **Step 2: nav link**

Main page nav: `<Link href="/about" className="hover:text-accent">о проекте</Link>`.

- [ ] **Step 3: build, commit, push, verify**

```bash
cd web && npm run build
git add web/
git commit -m "feat(web): /about mission and methodology page"
git push origin main
```
Verify https://massaraksh.tech/about renders all sections, live numbers appear.

---

### Task 8: UN votes — full registry load + periodic service

**Files:**
- Modify: `docker-compose.yml`, `data/init.sql`
- Create: `scripts/migrations/003_un_votes_trade.sql`

- [ ] **Step 1: migration + init.sql DDL (idempotent)**

FIRST dump the live schema to match exactly:
`ssh geopulse-prod 'cd /opt/geopulse && docker compose exec -T db psql -U thermo -d cis_thermometer -c "\\d un_votes" -c "\\d trade_data"'`

`scripts/migrations/003_un_votes_trade.sql` (adjust columns to the dump):
```sql
CREATE TABLE IF NOT EXISTS un_votes (
    country_code CHAR(2) NOT NULL,
    year INTEGER NOT NULL,
    total_votes INTEGER,
    agree_with_russia INTEGER,
    disagree_with_russia INTEGER,
    abstain INTEGER,
    agreement_pct DECIMAL(5,1),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (country_code, year)
);

CREATE TABLE IF NOT EXISTS trade_data (
    country_code CHAR(2) NOT NULL,
    year INTEGER NOT NULL,
    ru_export_usd BIGINT,
    ru_import_usd BIGINT,
    total_trade_usd BIGINT,
    trade_balance_usd BIGINT,
    yoy_change_pct DECIMAL(8,1),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (country_code, year)
);
```
Append the same DDL to `data/init.sql`.

- [ ] **Step 2: compose service**

Add to `docker-compose.yml` (model on the `briefs` block L146-162; READ `Dockerfile.loaders` first — if it doesn't install the deps load_un_votes needs, use `Dockerfile.temperature` instead):
```yaml
  un-votes-loader:
    build:
      context: .
      dockerfile: Dockerfile.loaders
    command: python scripts/load_un_votes.py --loop
    environment:
      DATABASE_URL: ${DATABASE_URL}
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped
```
(Verify `--loop` flag semantics in scripts/load_un_votes.py main() — it exists per L189; confirm its default interval is ~30 days.)

- [ ] **Step 3: commit, push, run on prod**

```bash
git add docker-compose.yml data/init.sql scripts/migrations/
git commit -m "feat(data): un_votes/trade_data DDL + un-votes loader service (full registry)"
git push origin main
```
After deploy:
```bash
ssh geopulse-prod 'cd /opt/geopulse && docker compose up -d un-votes-loader && docker compose logs un-votes-loader --tail 20'
# after it finishes one pass:
ssh geopulse-prod 'cd /opt/geopulse && docker compose exec -T db psql -U thermo -d cis_thermometer -c "SELECT COUNT(DISTINCT country_code) FROM un_votes;"'
```
Expected: ~98 countries.

---

### Task 9: Trade loader — all countries via IMF DOTS

**Files:**
- Modify: `scripts/auto_trade_loader.py`, `docker-compose.yml`

- [ ] **Step 1: generalize country set**

Read `scripts/auto_trade_loader.py` in full. It iterates a hardcoded CIS map. Replace the iteration source with the registry:
```python
from src.countries import all_codes
TARGET_COUNTRIES = [c for c in all_codes() if c != "RU"]
```
The IMF DOTS branch queries by ISO2 area codes (verify against the request URL builder in the file; DOTS CompactData uses ISO2 like `KZ`). Keep Comtrade branches CIS-only where they need numeric mappings (guard: `if code in <existing CIS map>`), but ALWAYS fall through to IMF DOTS for the rest. Wrap each country in try/except so one failure doesn't kill the pass; keep the existing "only insert NEW years" behavior. Log a per-pass summary line: `loaded X countries, skipped Y, failed Z`.

- [ ] **Step 2: compose service**

```yaml
  trade-loader:
    build:
      context: .
      dockerfile: Dockerfile.loaders
    command: python scripts/auto_trade_loader.py --loop
    environment:
      DATABASE_URL: ${DATABASE_URL}
      COMTRADE_API_KEY: ${COMTRADE_API_KEY:-}
    depends_on:
      db:
        condition: service_healthy
    restart: unless-stopped
```
(Check the script's argparse: if `--loop` doesn't exist, add it — monthly interval via `time.sleep(30 * 86400)` after each pass, matching load_un_votes' pattern.)

- [ ] **Step 3: commit, push, run, verify**

```bash
git add scripts/auto_trade_loader.py docker-compose.yml
git commit -m "feat(data): extend trade loader to all registry countries via IMF DOTS"
git push origin main
ssh geopulse-prod 'cd /opt/geopulse && docker compose up -d trade-loader && sleep 120 && docker compose logs trade-loader --tail 20'
ssh geopulse-prod 'cd /opt/geopulse && docker compose exec -T db psql -U thermo -d cis_thermometer -c "SELECT COUNT(DISTINCT country_code) FROM trade_data;"'
```
Expected: substantially more than 10 countries (IMF DOTS coverage is broad but not universal; 70+ is success).

---

### Task 10: Dossier API — un-votes, trade, agreements

**Files:**
- Modify: `src/api/routes/world.py`
- Create: `src/pipeline/agreements.py`, `tests/test_agreements.py`

- [ ] **Step 1: TDD — agreements grouping helper**

`tests/test_agreements.py`:
```python
from src.pipeline.agreements import group_agreements


def make_row(event_key, title, url, source, published_at, action_level, event_type):
    return {"event_key": event_key, "title": title, "url": url, "source": source,
            "published_at": published_at, "action_level": action_level,
            "event_type": event_type}


def test_groups_by_event_key_sorted_by_recency():
    rows = [
        make_row("gas deal", "A", "u1", "s1", "2026-06-01", 3, "economic"),
        make_row("gas deal", "B", "u2", "s2", "2026-06-02", 4, "economic"),
        make_row("summit", "C", "u3", "s3", "2026-06-05", 3, "diplomatic"),
    ]
    groups = group_agreements(rows, max_articles=5)
    assert [g["event_key"] for g in groups] == ["summit", "gas deal"]
    gas = groups[1]
    assert gas["action_level"] == 4
    assert gas["articles_total"] == 2
    assert gas["last_at"] == "2026-06-02"


def test_caps_articles_per_group():
    rows = [make_row("k", f"t{i}", f"u{i}", "s", f"2026-06-{i+1:02d}", 3, "diplomatic")
            for i in range(8)]
    g = group_agreements(rows, max_articles=3)[0]
    assert len(g["articles"]) == 3
    assert g["articles_total"] == 8
    assert g["articles"][0]["title"] == "t7"  # newest first (dates run 06-01..06-08 for t0..t7)


def test_skips_empty_event_keys():
    rows = [make_row("", "A", "u", "s", "2026-06-01", 3, "diplomatic"),
            make_row(None, "B", "u", "s", "2026-06-01", 3, "diplomatic")]
    assert group_agreements(rows) == []
```

Run: `python3 -m pytest tests/test_agreements.py -v` → FAIL (module missing).

- [ ] **Step 2: implement helper**

`src/pipeline/agreements.py`:
```python
"""Group diplomatic/economic high-action events into agreement cards."""
from typing import Dict, List


def group_agreements(rows: List[dict], max_articles: int = 5) -> List[dict]:
    """rows: flat article rows with event_key; returns grouped cards, newest first."""
    groups: Dict[str, dict] = {}
    for r in rows:
        key = (r.get("event_key") or "").strip()
        if not key:
            continue
        g = groups.setdefault(key, {
            "event_key": key, "event_type": r["event_type"],
            "action_level": 0, "first_at": r["published_at"],
            "last_at": r["published_at"], "articles": [], "articles_total": 0,
        })
        g["action_level"] = max(g["action_level"], int(r.get("action_level") or 0))
        g["first_at"] = min(g["first_at"], r["published_at"])
        g["last_at"] = max(g["last_at"], r["published_at"])
        g["articles_total"] += 1
        g["articles"].append({"title": r["title"], "url": r["url"],
                              "source": r["source"],
                              "published_at": r["published_at"]})
    for g in groups.values():
        g["articles"].sort(key=lambda a: a["published_at"], reverse=True)
        g["articles"] = g["articles"][:max_articles]
    return sorted(groups.values(), key=lambda g: g["last_at"], reverse=True)
```

Run: `python3 -m pytest tests/ -v` → all pass. Commit:
```bash
git add src/pipeline/agreements.py tests/test_agreements.py
git commit -m "feat(dossier): agreements grouping helper"
```

- [ ] **Step 3: three endpoints in world.py**

```python
@router.get("/countries/{code}/un-votes")
def country_un_votes(code: str):
    """UN GA voting agreement with Russia by year."""
    code = code.upper()
    if code not in COUNTRIES:
        raise HTTPException(404, f"Unknown country: {code}")
    with get_session() as session:
        rows = session.execute(
            text("""SELECT year, total_votes, agree_with_russia,
                           disagree_with_russia, abstain, agreement_pct
                    FROM un_votes WHERE country_code = :cc ORDER BY year"""),
            {"cc": code}).fetchall()
    return {"country_code": code, "data": [
        {"year": r.year, "total_votes": r.total_votes,
         "agree_with_russia": r.agree_with_russia,
         "disagree_with_russia": r.disagree_with_russia, "abstain": r.abstain,
         "agreement_pct": float(r.agreement_pct) if r.agreement_pct is not None else None}
        for r in rows]}


@router.get("/countries/{code}/trade")
def country_trade(code: str):
    """Russia trade volumes by year."""
    code = code.upper()
    if code not in COUNTRIES:
        raise HTTPException(404, f"Unknown country: {code}")
    with get_session() as session:
        rows = session.execute(
            text("""SELECT year, ru_export_usd, ru_import_usd, total_trade_usd,
                           trade_balance_usd, yoy_change_pct
                    FROM trade_data WHERE country_code = :cc ORDER BY year"""),
            {"cc": code}).fetchall()
    return {"country_code": code, "data": [
        {"year": r.year,
         "ru_export_usd": int(r.ru_export_usd) if r.ru_export_usd is not None else None,
         "ru_import_usd": int(r.ru_import_usd) if r.ru_import_usd is not None else None,
         "total_trade_usd": int(r.total_trade_usd) if r.total_trade_usd is not None else None,
         "trade_balance_usd": int(r.trade_balance_usd) if r.trade_balance_usd is not None else None,
         "yoy_change_pct": float(r.yoy_change_pct) if r.yoy_change_pct is not None else None}
        for r in rows]}


@router.get("/countries/{code}/agreements")
def country_agreements(code: str, days: int = Query(180, ge=7, le=365)):
    """Diplomatic/economic high-action events grouped by event_key."""
    code = code.upper()
    if code not in COUNTRIES:
        raise HTTPException(404, f"Unknown country: {code}")
    from src.pipeline.agreements import group_agreements
    with get_session() as session:
        rows = session.execute(
            text("""
                SELECT a.event_key, a.event_type, a.action_level,
                       ar.title, ar.url, s.name AS source,
                       ar.published_at
                FROM analysis a
                JOIN articles ar ON a.article_id = ar.id
                JOIN sources s ON ar.source_id = s.id
                WHERE s.country_code = :cc
                  AND a.event_type IN ('diplomatic', 'economic')
                  AND a.action_level >= 3
                  AND a.event_key IS NOT NULL AND a.event_key != ''
                  AND ar.published_at > NOW() - make_interval(days => :days)
                ORDER BY ar.published_at DESC
                LIMIT 500
            """), {"cc": code, "days": days}).fetchall()
    flat = [{"event_key": r.event_key, "event_type": r.event_type,
             "action_level": r.action_level, "title": r.title, "url": r.url,
             "source": r.source,
             "published_at": r.published_at.isoformat() if r.published_at else ""}
            for r in rows]
    return {"country_code": code, "agreements": group_agreements(flat)}
```

- [ ] **Step 4: commit, push, verify on prod**

```bash
git add src/api/routes/world.py
git commit -m "feat(api): v2 country un-votes, trade and agreements endpoints"
git push origin main
# after deploy:
curl -s https://massaraksh.tech/api/v2/countries/KZ/un-votes | head -c 300
curl -s https://massaraksh.tech/api/v2/countries/KZ/trade | head -c 300
curl -s "https://massaraksh.tech/api/v2/countries/KZ/agreements?days=180" | head -c 400
```
Expected: 200 + data arrays (agreements may be sparse for tier-2 countries — that's fine).

---

### Task 11: Dossier UI — three new sections

**Files:**
- Create: `web/components/UNVotesPanel.tsx`, `web/components/TradePanel.tsx`, `web/components/AgreementsPanel.tsx`
- Modify: `web/lib/api.ts`, `web/lib/types.ts`, `web/app/country/[code]/page.tsx`

- [ ] **Step 1: types + api**

`web/lib/types.ts`:
```ts
export interface UNVoteYear {
  year: number; total_votes: number | null; agree_with_russia: number | null;
  disagree_with_russia: number | null; abstain: number | null;
  agreement_pct: number | null;
}
export interface TradeYear {
  year: number; ru_export_usd: number | null; ru_import_usd: number | null;
  total_trade_usd: number | null; trade_balance_usd: number | null;
  yoy_change_pct: number | null;
}
export interface AgreementGroup {
  event_key: string; event_type: string; action_level: number;
  first_at: string; last_at: string; articles_total: number;
  articles: { title: string; url: string | null; source: string; published_at: string }[];
}
```
`web/lib/api.ts`:
```ts
  unVotes: (code: string) =>
    get<{ data: UNVoteYear[] }>(`/api/v2/countries/${code}/un-votes`),
  trade: (code: string) =>
    get<{ data: TradeYear[] }>(`/api/v2/countries/${code}/trade`),
  agreements: (code: string, days = 180) =>
    get<{ agreements: AgreementGroup[] }>(`/api/v2/countries/${code}/agreements?days=${days}`),
```

- [ ] **Step 2: UNVotesPanel**

`web/components/UNVotesPanel.tsx`:
```tsx
"use client";

import Plot from "@/components/Plot";
import type { UNVoteYear } from "@/lib/types";

export default function UNVotesPanel({ data }: { data: UNVoteYear[] }) {
  if (!data.length) return null;
  const last = data[data.length - 1];
  const pct = last.agreement_pct;
  const pctCls = pct == null ? "text-dim"
    : pct >= 60 ? "text-emerald-400" : pct >= 40 ? "text-yellow-400" : "text-red-400";
  return (
    <section className="card">
      <div className="card-title px-4 pb-1 pt-3">Голосования ООН (совпадение с РФ)</div>
      <div className="grid grid-cols-2 gap-2 px-4 pt-1 sm:grid-cols-4">
        <div><div className={`text-lg font-semibold ${pctCls}`}>{pct != null ? `${pct.toFixed(0)}%` : "—"}</div>
          <div className="text-[10px] uppercase text-dim">совпадение {last.year}</div></div>
        <div><div className="text-lg font-semibold">{last.total_votes ?? "—"}</div>
          <div className="text-[10px] uppercase text-dim">голосований</div></div>
        <div><div className="text-lg font-semibold text-emerald-400">{last.agree_with_russia ?? "—"}</div>
          <div className="text-[10px] uppercase text-dim">вместе с РФ</div></div>
        <div><div className="text-lg font-semibold text-red-400">{last.disagree_with_russia ?? "—"}</div>
          <div className="text-[10px] uppercase text-dim">против РФ</div></div>
      </div>
      <Plot className="h-[180px] w-full px-2 pb-2"
        data={[{
          x: data.map((d) => d.year), y: data.map((d) => d.agreement_pct),
          type: "scatter", mode: "lines+markers", fill: "tozeroy",
          line: { color: "#a78bfa" }, fillcolor: "rgba(167,139,250,0.15)",
          hovertemplate: "%{x}: %{y:.0f}%<extra></extra>",
        }]}
        layout={{
          margin: { l: 32, r: 8, t: 8, b: 24 },
          paper_bgcolor: "transparent", plot_bgcolor: "transparent",
          font: { color: "#9ca3af", size: 10 },
          yaxis: { range: [0, 100], gridcolor: "rgba(255,255,255,0.06)" },
          xaxis: { gridcolor: "rgba(255,255,255,0.06)" },
          shapes: [{ type: "line", x0: data[0].year, x1: last.year, y0: 50, y1: 50,
                     line: { color: "rgba(255,255,255,0.25)", dash: "dot", width: 1 } }],
        }} />
    </section>
  );
}
```

- [ ] **Step 3: TradePanel**

`web/components/TradePanel.tsx`:
```tsx
"use client";

import Plot from "@/components/Plot";
import type { TradeYear } from "@/lib/types";

function fmtUsd(v: number | null): string {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
  return `$${v.toLocaleString("en")}`;
}

export default function TradePanel({ data }: { data: TradeYear[] }) {
  if (!data.length) return null;
  const last = data[data.length - 1];
  const yoy = last.yoy_change_pct;
  return (
    <section className="card">
      <div className="card-title px-4 pb-1 pt-3">Торговля с Россией</div>
      <div className="grid grid-cols-2 gap-2 px-4 pt-1 sm:grid-cols-4">
        <div><div className="text-lg font-semibold">{fmtUsd(last.total_trade_usd)}</div>
          <div className="text-[10px] uppercase text-dim">оборот {last.year}</div></div>
        <div><div className="text-lg font-semibold text-indigo-400">{fmtUsd(last.ru_export_usd)}</div>
          <div className="text-[10px] uppercase text-dim">экспорт РФ</div></div>
        <div><div className="text-lg font-semibold text-teal-400">{fmtUsd(last.ru_import_usd)}</div>
          <div className="text-[10px] uppercase text-dim">импорт РФ</div></div>
        <div><div className={`text-lg font-semibold ${yoy != null && yoy < 0 ? "text-red-400" : "text-emerald-400"}`}>
          {yoy != null ? `${yoy > 0 ? "+" : ""}${yoy.toFixed(1)}%` : "—"}</div>
          <div className="text-[10px] uppercase text-dim">за год</div></div>
      </div>
      <Plot className="h-[200px] w-full px-2 pb-2"
        data={[
          { x: data.map((d) => d.year), y: data.map((d) => d.ru_export_usd),
            type: "bar", name: "Экспорт РФ", marker: { color: "#818cf8" } },
          { x: data.map((d) => d.year), y: data.map((d) => d.ru_import_usd),
            type: "bar", name: "Импорт РФ", marker: { color: "#2dd4bf" } },
          { x: data.map((d) => d.year), y: data.map((d) => d.total_trade_usd),
            type: "scatter", mode: "lines", name: "Оборот",
            line: { color: "#e5e7eb", dash: "dash", width: 1.5 } },
        ]}
        layout={{
          barmode: "stack", margin: { l: 40, r: 8, t: 8, b: 24 },
          paper_bgcolor: "transparent", plot_bgcolor: "transparent",
          font: { color: "#9ca3af", size: 10 },
          legend: { orientation: "h", y: 1.15 },
          yaxis: { gridcolor: "rgba(255,255,255,0.06)" },
          xaxis: { gridcolor: "rgba(255,255,255,0.06)" },
        }} />
    </section>
  );
}
```

- [ ] **Step 4: AgreementsPanel**

`web/components/AgreementsPanel.tsx`:
```tsx
import type { AgreementGroup } from "@/lib/types";
import { fmtDate } from "@/lib/format";

const TYPE_LABEL: Record<string, string> = {
  diplomatic: "дипломатия", economic: "экономика",
};

export default function AgreementsPanel({ items }: { items: AgreementGroup[] }) {
  if (!items.length) return null;
  return (
    <section className="card">
      <div className="card-title px-4 pb-1 pt-3">Договоры и намерения (180 дней)</div>
      <div className="max-h-[360px] divide-y divide-white/5 overflow-y-auto">
        {items.map((g) => (
          <div key={g.event_key} className="px-4 py-2">
            <div className="flex items-baseline gap-2 text-[13px]">
              <span className="font-medium">{g.event_key}</span>
              <span className="rounded bg-white/5 px-1.5 text-[10px] text-dim">
                {TYPE_LABEL[g.event_type] ?? g.event_type} · AL{g.action_level}
              </span>
              <span className="ml-auto shrink-0 text-[11px] text-dim">{fmtDate(g.last_at)}</span>
            </div>
            <ul className="mt-1 space-y-0.5">
              {g.articles.map((a, i) => (
                <li key={i} className="truncate text-[12px]">
                  <a href={a.url ?? "#"} target="_blank" rel="noopener noreferrer"
                     className="text-dim hover:text-accent">↗ {a.title}</a>
                  <span className="text-[10px] text-zinc-600"> — {a.source}</span>
                </li>
              ))}
            </ul>
            {g.articles_total > g.articles.length && (
              <div className="text-[10px] text-zinc-600">+{g.articles_total - g.articles.length} статей</div>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
```

- [ ] **Step 5: wire into country page**

`web/app/country/[code]/page.tsx`: add three states + parallel loads (`api.unVotes(cc).then(d => setUn(d.data)).catch(() => {})` etc.), render `<UNVotesPanel data={un} />`, `<TradePanel data={trade} />`, `<AgreementsPanel items={agreements} />` after the GDELT/currency charts, following the page's existing section layout. All three render nothing when empty — safe for countries without data.

- [ ] **Step 6: build, commit, push, verify**

```bash
cd web && npm run build
git add web/
git commit -m "feat(web): dossier sections — UN votes, Russia trade, agreements"
git push origin main
```
Verify https://massaraksh.tech/country/KZ shows all three sections; /country/FR shows UN votes (after Task 8) and hides empty ones.

---

### Task 12: Analytics/intel feed tier

**Files:**
- Modify: `src/collectors/sources_world.yaml` (or the file collect.py reads — verify `ensure_sources_in_db` source list), `data/init.sql`, `scripts/collect.py` (if yaml keys need passing through)
- Create: `scripts/migrations/004_source_trust_flags.sql`, `scripts/validate_feed.py`
- Create: `web/app/analytics/page.tsx`

- [ ] **Step 1: migration — trust flags**

`scripts/migrations/004_source_trust_flags.sql`:
```sql
ALTER TABLE sources ADD COLUMN IF NOT EXISTS state_affiliated BOOLEAN DEFAULT FALSE;
ALTER TABLE sources ADD COLUMN IF NOT EXISTS propaganda_risk VARCHAR(10) DEFAULT 'low';
```
Mirror in `data/init.sql` sources DDL. Apply on prod:
`ssh geopulse-prod 'cd /opt/geopulse && docker compose exec -T db psql -U thermo -d cis_thermometer' < scripts/migrations/004_source_trust_flags.sql` (run from the local repo; psql reads stdin).

- [ ] **Step 2: feed validator**

`scripts/validate_feed.py`:
```python
"""Validate an RSS/Atom feed candidate before adding it to the registry.

Usage: python scripts/validate_feed.py <url> [--max-age-days 30]
Exit 0 = usable, 1 = rejected (reason printed).
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

import feedparser
import httpx


def main():
    p = argparse.ArgumentParser()
    p.add_argument("url")
    p.add_argument("--max-age-days", type=int, default=30)
    args = p.parse_args()

    try:
        resp = httpx.get(args.url, timeout=15, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (GEO PULSE feed check)"})
    except Exception as e:
        print(f"REJECT: fetch failed: {e}")
        sys.exit(1)
    if resp.status_code != 200:
        print(f"REJECT: HTTP {resp.status_code}")
        sys.exit(1)

    feed = feedparser.parse(resp.content)
    if feed.bozo and not feed.entries:
        print(f"REJECT: parse error: {feed.bozo_exception}")
        sys.exit(1)
    if not feed.entries:
        print("REJECT: feed has no entries")
        sys.exit(1)

    newest = None
    for e in feed.entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        if t:
            dt = datetime(*t[:6], tzinfo=timezone.utc)
            newest = max(newest, dt) if newest else dt
    if newest is None:
        print("WARN: no dates in feed; accepting on entry count")
    elif datetime.now(timezone.utc) - newest > timedelta(days=args.max_age_days):
        print(f"REJECT: newest entry {newest.date()} older than {args.max_age_days}d")
        sys.exit(1)

    print(f"OK: {len(feed.entries)} entries, newest {newest.date() if newest else 'n/a'}: "
          f"{feed.feed.get('title', '(no title)')}")
    sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: analytics feeds**

Verify each URL with `scripts/validate_feed.py` FIRST (run inside collector container: `ssh geopulse-prod 'cd /opt/geopulse && docker compose run --rm collector python scripts/validate_feed.py <url>'`, or locally with `pip3 install feedparser httpx`), then add the passing ones to the yaml the collector reads, under the source's HQ country (US/GB/etc.), `tier: analytics`, `language: en`. Candidate set (use Google News pattern `https://news.google.com/rss/search?q=site:{domain}+russia&hl=en-US&gl=US&ceid=US:en` for sites without native RSS):
- Jamestown Foundation (jamestown.org — native RSS), ISW (understandingwar.org), Carnegie Endowment (carnegieendowment.org), RUSI (rusi.org), CSIS (csis.org), ECFR (ecfr.eu), Chatham House (chathamhouse.org), War on the Rocks (warontherocks.com — native RSS), FPRI (fpri.org), Atlantic Council (atlanticcouncil.org), Bellingcat (bellingcat.com — native RSS), OCCRP (occrp.org — native RSS), Responsible Statecraft (responsiblestatecraft.org), Brookings (brookings.edu), RAND (rand.org).
Mark `state_affiliated: true, propaganda_risk: high` on existing state news agencies in the CIS yaml while editing (grep sources.yaml for state agencies; only ADD flags, never remove sources). Read `ensure_sources_in_db` in scripts/collect.py: if it ignores unknown yaml keys, extend the Source insert to write `state_affiliated`/`propaganda_risk` columns; `tier: analytics` must reach the DB (tier passes through already — verify the allowed-tier list in CLAUDE.md conventions includes analytics: it does).

- [ ] **Step 4: /analytics page**

`web/app/analytics/page.tsx` — list page over the headlines endpoint:
```tsx
"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import HeadlinesFeed from "@/components/HeadlinesFeed";
import { apiBase } from "@/lib/api";
import type { Headline } from "@/lib/types";

export default function AnalyticsPage() {
  const [items, setItems] = useState<Headline[]>([]);
  const [country, setCountry] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const qs = `hours=168&limit=100&tier=analytics${country ? `&country=${country}` : ""}`;
    fetch(`${apiBase()}/api/v2/headlines?${qs}`, { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => setItems(d.headlines ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [country]);

  const countries = [...new Set(items.map((h) => h.country_code).filter(Boolean))] as string[];

  return (
    <main className="mx-auto max-w-[900px] px-3 pb-8">
      <header className="flex flex-wrap items-center gap-3 py-3">
        <h1 className="text-base font-semibold tracking-wider">🔍 Аналитические центры о России</h1>
        <nav className="ml-auto text-xs text-dim">
          <Link href="/" className="hover:text-accent">← на главную</Link>
        </nav>
      </header>
      <p className="mb-3 text-[13px] text-dim">
        Публикации think tanks и OSINT-расследователей за неделю. Это не новости,
        а аналитика — у каждого центра своя оптика и свои спонсоры; читайте с поправкой.
      </p>
      <div className="mb-3 flex flex-wrap gap-1.5 text-[11px]">
        <span onClick={() => setCountry(null)}
          className={`cursor-pointer rounded px-2 py-0.5 ${!country ? "bg-accent/20 text-accent" : "bg-white/5 text-dim"}`}>все</span>
        {countries.map((c) => (
          <span key={c} onClick={() => setCountry(c)}
            className={`cursor-pointer rounded px-2 py-0.5 ${country === c ? "bg-accent/20 text-accent" : "bg-white/5 text-dim"}`}>{c}</span>
        ))}
      </div>
      <section className="card">
        {loading ? <div className="px-4 py-3 text-xs text-dim">Загрузка…</div>
                 : <HeadlinesFeed items={items} />}
      </section>
    </main>
  );
}
```
Add nav link on main page: `<Link href="/analytics" className="hover:text-accent">аналитика</Link>`.

- [ ] **Step 5: build, commit, push, verify**

```bash
cd web && npm run build
git add src/collectors/ scripts/ data/init.sql web/
git commit -m "feat(sources): analytics/intel feed tier with trust flags + /analytics page"
git push origin main
```
After deploy + one collector cycle (30 min): `curl -s "https://massaraksh.tech/api/v2/headlines?tier=analytics&hours=168" | head -c 400` — expect entries; https://massaraksh.tech/analytics renders.

---

## Verification checklist (after all tasks)

- [ ] https://massaraksh.tech — 3-колоночный низ, новости дня кликабельны
- [ ] Бриф на главной — сноски `[n]` кликабельны, ведут на статьи
- [ ] /sources — фильтры, живость, матрица ru/en/native
- [ ] /about — миссия, шкала RRI, живые цифры
- [ ] /country/KZ — ООН + торговля + договоры; /country/DE — ООН (после загрузки), торговля (если IMF отдал)
- [ ] /analytics — лента think tanks
- [ ] `python3 -m pytest tests/ -v` — зелёный
- [ ] Прод: `docker compose ps` — все сервисы Up, включая un-votes-loader и trade-loader
