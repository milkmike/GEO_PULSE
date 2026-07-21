# Page Reliability and Product Radar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Сделать все динамические страницы гарантированно видимыми и заменить техническую простыню радара компактной выдачей только доказательных ускоряющихся метатрендов.

**Architecture:** Исправление разделено на независимые границы: CSS/API-клиент отвечают за видимость и конечное время ожидания, серверный endpoint сущностей — за старые некорректные JSON-строки, Radar persistence — за актуальные членства, Radar API/UI — за продуктовую проекцию. Исторические строки не удаляются: устаревшие членства закрываются через `left_at`.

**Tech Stack:** Next.js 15, React 19, TypeScript, Vitest, FastAPI, SQLAlchemy, PostgreSQL 16, Pytest, Docker Compose.

## Global Constraints

- Накопленные статьи, температура, сюжеты и радарные наблюдения не удаляются.
- `.reveal` никогда не должен делать контент прозрачным.
- Публичный список радара не показывает `media:coverage`, нулевую скорость или более восьми волн на карточку.
- Полная страница тренда сохраняет все активные волны, таймлайн и доказательства.
- Все изменения выполняются тестами вперёд и проверяются на продакшене.

---

### Task 1: Гарантированная видимость и конечная загрузка

**Files:**
- Create: `web/app/visibility.test.ts`
- Create: `web/lib/api.timeout.test.ts`
- Modify: `web/app/globals.css:130-175`
- Modify: `web/lib/api.ts:20-35`
- Test: `tests/test_world_dossier.py`
- Modify: `src/api/routes/world.py:198-225`

**Interfaces:**
- Consumes: существующие `api.*(..., signal?: AbortSignal)` методы.
- Produces: `DEFAULT_REQUEST_TIMEOUT_MS = 15000`; общий `get<T>` отклоняет запрос `TimeoutError` через 15 секунд; `/entities` игнорирует JSON не-массивы.

- [ ] **Step 1: Написать падающий CSS-регрессионный тест**

```ts
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

describe("dynamic content visibility", () => {
  it("never makes reveal content depend on opacity animation", () => {
    const css = readFileSync(new URL("./globals.css", import.meta.url), "utf8");
    const fadeUp = css.match(/@keyframes fadeUp\s*\{[\s\S]*?\n\}/)?.[0] ?? "";
    expect(fadeUp).not.toContain("opacity:");
  });
});
```

- [ ] **Step 2: Запустить CSS-тест и увидеть FAIL**

Run: `cd web && npm test -- app/visibility.test.ts`

Expected: FAIL, блок `fadeUp` содержит `opacity: 0` и `opacity: 1`.

- [ ] **Step 3: Сделать анимацию transform-only**

```css
@keyframes fadeUp {
  from { transform: translateY(10px); }
  to { transform: translateY(0); }
}
```

- [ ] **Step 4: Написать падающий тест таймаута API**

```ts
it("aborts a stalled public request after 15 seconds", async () => {
  vi.useFakeTimers();
  vi.stubGlobal("fetch", vi.fn((_url, options: RequestInit) => new Promise((_resolve, reject) => {
    options.signal?.addEventListener("abort", () => reject(options.signal?.reason), { once: true });
  })));
  const pending = api.meta();
  await vi.advanceTimersByTimeAsync(15_000);
  await expect(pending).rejects.toMatchObject({ name: "TimeoutError" });
  vi.useRealTimers();
});
```

- [ ] **Step 5: Запустить тест таймаута и увидеть FAIL**

Run: `cd web && npm test -- lib/api.timeout.test.ts`

Expected: FAIL, promise остаётся pending.

- [ ] **Step 6: Реализовать единый таймаут с сохранением внешней отмены**

```ts
export const DEFAULT_REQUEST_TIMEOUT_MS = 15_000;

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const controller = new AbortController();
  const forwardAbort = () => controller.abort(signal?.reason);
  if (signal?.aborted) forwardAbort();
  else signal?.addEventListener("abort", forwardAbort, { once: true });
  const timeout = window.setTimeout(
    () => controller.abort(new DOMException("API request timed out", "TimeoutError")),
    DEFAULT_REQUEST_TIMEOUT_MS,
  );
  try {
    const res = await fetch(`${apiBase()}${path}`, { cache: "no-store", signal: controller.signal });
    if (!res.ok) throw new Error(`${res.status} ${path}`);
    return await res.json() as T;
  } finally {
    window.clearTimeout(timeout);
    signal?.removeEventListener("abort", forwardAbort);
  }
}
```

- [ ] **Step 7: Написать падающий API-тест старого JSON-скаляра**

В `tests/test_world_dossier.py` перехватить SQL `country_entities` и проверить наличие:

```python
assert "jsonb_typeof(a.entities) = 'array'" in sql
```

- [ ] **Step 8: Запустить API-тест и увидеть FAIL**

Run: `.venv/bin/python -m pytest -q tests/test_world_dossier.py -k entities`

Expected: FAIL, SQL содержит только `a.entities IS NOT NULL`.

- [ ] **Step 9: Ограничить lateral-разворачивание массивами**

```sql
AND jsonb_typeof(a.entities) = 'array'
```

- [ ] **Step 10: Проверить Task 1 и закоммитить**

Run: `cd web && npm test -- app/visibility.test.ts lib/api.timeout.test.ts`

Run: `.venv/bin/python -m pytest -q tests/test_world_dossier.py`

Expected: PASS.

Commit: `git commit -am "fix: keep dynamic pages visible and bounded"`

---

### Task 2: Scoped-жизненный цикл членств радара

**Files:**
- Modify: `tests/test_radar_service.py:1140-1170`
- Modify: `src/radar/service.py:1253-1340`

**Interfaces:**
- Consumes: `MetaTrend`, `wave_ids`, `incremental=True`.
- Produces: `_close_stale_meta_members(session, meta_id: int, active_country_ids: tuple[int, ...], as_of: datetime) -> None`.

- [ ] **Step 1: Написать падающий тест scoped-закрытия**

```python
def test_incremental_meta_refresh_closes_only_stale_members_of_current_meta():
    session = CaptureSession(existing_meta_id=77, wave_ids=(901, 902))
    _persist_meta_and_contours(session, (meta,), wave_ids, AS_OF, incremental=True)
    sql, params = next(
        (sql, params) for sql, params in session.calls
        if "UPDATE radar_trend_members" in sql and "meta_trend_id = :meta_id" in sql
    )
    assert params == {"meta_id": 77, "active_country_ids": [901, 902], "as_of": AS_OF}
    assert "detector_version" not in params
```

- [ ] **Step 2: Запустить тест и увидеть FAIL**

Run: `.venv/bin/python -m pytest -q tests/test_radar_service.py -k closes_only_stale`

Expected: FAIL, scoped UPDATE отсутствует.

- [ ] **Step 3: Добавить scoped UPDATE без удаления истории**

```python
_CLOSE_STALE_META_MEMBERS = text("""
UPDATE radar_trend_members
SET left_at = :as_of
WHERE meta_trend_id = :meta_id
  AND left_at IS NULL
  AND NOT (country_trend_id = ANY(CAST(:active_country_ids AS bigint[])))
""")
```

После upsert текущих членов вызвать UPDATE только при `incremental=True`, передав актуальные `country_id` текущего `meta`.

- [ ] **Step 4: Запустить весь сервисный набор радара**

Run: `.venv/bin/python -m pytest -q tests/test_radar_service.py tests/test_radar_grouping.py`

Expected: PASS; существующий тест сохранения несвязанных meta-членств остаётся зелёным.

- [ ] **Step 5: Закоммитить Task 2**

Commit: `git commit -am "fix: retire stale radar memberships incrementally"`

---

### Task 3: Продуктовая проекция Radar API и карточки

**Files:**
- Modify: `tests/test_radar_api.py`
- Modify: `src/api/routes/radar.py`
- Modify: `web/lib/types.ts:721-775`
- Modify: `web/components/TrendCard.test.tsx`
- Modify: `web/components/TrendCard.tsx`
- Modify: `web/app/radar/page.test.tsx`
- Modify: `web/app/radar/page.tsx`

**Interfaces:**
- Produces: `RadarTrend.country_count: number`, `RadarTrend.wave_count: number`.
- Produces: `serialize_trend(row, *, wave_limit: int | None = None)`; list endpoint uses `wave_limit=8`, detail uses `None`.
- Produces: `_humanize_thesis(subject_key: str, direction: str) -> str`.

- [ ] **Step 1: Написать падающие API-тесты quality gate и компактной выдачи**

Проверить SQL списка:

```python
assert "trend.subject_key <> 'media:coverage'" in sql
assert "ABS(COALESCE(trend.velocity, 0)) >= 0.05" in sql
assert "COUNT(DISTINCT quality_wave.country_code) >= 2" in sql
```

Проверить сериализацию:

```python
payload = serialize_trend(row_with_twelve_waves, wave_limit=8)
assert payload["country_count"] == 12
assert payload["wave_count"] == 12
assert len(payload["country_waves"]) == 8
assert payload["thesis"] == "Торговые связи с Россией ослабевают"
```

- [ ] **Step 2: Запустить API-тесты и увидеть FAIL**

Run: `.venv/bin/python -m pytest -q tests/test_radar_api.py -k 'quality or compact or human'`

Expected: FAIL из-за отсутствующих фильтров, полей и подписи.

- [ ] **Step 3: Реализовать quality gate в CTE `ranked`**

```sql
AND trend.subject_key <> 'media:coverage'
AND ABS(COALESCE(trend.velocity, 0)) >= 0.05
AND 2 <= (
  SELECT COUNT(DISTINCT quality_wave.country_code)
  FROM radar_trend_members quality_member
  JOIN radar_trends quality_wave ON quality_wave.id = quality_member.country_trend_id
  WHERE quality_member.meta_trend_id = trend.id
    AND quality_member.left_at IS NULL
)
AND EXISTS (
  SELECT 1
  FROM radar_trend_evidence quality_evidence
  WHERE quality_evidence.trend_id = trend.id
     OR EXISTS (
       SELECT 1 FROM radar_trend_members quality_member
       WHERE quality_member.meta_trend_id = trend.id
         AND quality_member.country_trend_id = quality_evidence.trend_id
         AND quality_member.left_at IS NULL
     )
)
```

- [ ] **Step 4: Реализовать компактную сериализацию и русские тезисы**

```python
def _humanize_thesis(subject: str, direction: str) -> str:
    labels = {
        ("economy:trade:russia", "increase"): "Торговые связи с Россией усиливаются",
        ("economy:trade:russia", "decrease"): "Торговые связи с Россией ослабевают",
        ("diplomacy:un_alignment:russia", "increase"): "Голосования сближаются с позицией России",
        ("diplomacy:un_alignment:russia", "decrease"): "Голосования расходятся с позицией России",
        ("energy:imports:russia", "increase"): "Импорт российских энергоресурсов растёт",
        ("energy:imports:russia", "decrease"): "Импорт российских энергоресурсов сокращается",
    }
    return labels.get((subject, direction), subject.removeprefix("event:").replace(":", " · "))
```

`serialize_trend` сначала считает полные `wave_count` и `country_count`, затем ограничивает только возвращаемый массив.

- [ ] **Step 5: Написать падающий компонентный тест карточки**

```tsx
render(<TrendCard trend={{ ...trend, country_count: 12, wave_count: 14 }} />);
expect(screen.getByText("12 стран")).toBeInTheDocument();
expect(screen.getByText("+7 стран")).toBeInTheDocument();
expect(screen.queryByText(/ES → PT →/)).not.toBeInTheDocument();
expect(screen.getByText("медиа пока не подтверждает")).toBeInTheDocument();
```

- [ ] **Step 6: Запустить компонентный тест и увидеть FAIL**

Run: `cd web && npm test -- components/TrendCard.test.tsx app/radar/page.test.tsx`

Expected: FAIL, карточка всё ещё печатает цепочку стран и технические статусы.

- [ ] **Step 7: Пересобрать карточку в редакционном формате**

- Использовать первые пять уникальных кодов и `+N стран`.
- Показать `country_count`, локализованную дату `t0_effective ?? detected_at ?? first_observed_at` и скорость.
- Подписи контуров формировать как `медиа подтверждает`, `медиа пока не подтверждает`, `действия подтверждают`, `действия пока не подтверждают`.
- Сохранить ссылку на `/radar/{public_id}` и первоисточник.

- [ ] **Step 8: Обновить честное пустое состояние страницы**

Текст:

```text
Сейчас нет трендов, прошедших проверку
Радар покажет изменение, когда оно появится минимум в двух странах, получит измеримое ускорение и проверяемые материалы. Статические годовые показатели сюда не попадают.
```

- [ ] **Step 9: Проверить Task 3 и закоммитить**

Run: `.venv/bin/python -m pytest -q tests/test_radar_api.py`

Run: `cd web && npm test -- components/TrendCard.test.tsx app/radar/page.test.tsx`

Expected: PASS.

Commit: `git commit -am "fix: publish only actionable radar trends"`

---

### Task 4: Полная проверка и безопасный деплой

**Files:**
- Verify only: repository and production services.

**Interfaces:**
- Consumes: Docker Compose services `web`, `api`, `radar-worker`.
- Produces: production release with no data deletion.

- [ ] **Step 1: Запустить полный backend-набор**

Run: `.venv/bin/python -m pytest -q`

Expected: все тесты PASS, допустимы только существующие skips/warnings.

- [ ] **Step 2: Запустить полный frontend-набор и сборку**

Run: `cd web && npm test`

Run: `cd web && npm run build`

Expected: PASS.

- [ ] **Step 3: Проверить compose и diff**

Run: `docker compose config --quiet`

Run: `git diff --check`

Expected: exit code 0.

- [ ] **Step 4: Отправить коммиты и обновить продакшен**

```bash
git push origin HEAD:main
ssh geopulse-prod 'cd /opt/geopulse && git pull --ff-only origin main'
ssh geopulse-prod 'cd /opt/geopulse && docker compose build web api radar-worker'
ssh geopulse-prod 'cd /opt/geopulse && docker compose up -d --no-deps web api radar-worker'
```

- [ ] **Step 5: Дождаться одного radar cycle**

Проверить лог `Radar cycle complete`, `RestartCount=0` и что количество активных членов верхнего meta-тренда больше не растёт между повторными запросами.

- [ ] **Step 6: Проверить продакшен снаружи**

Проверить `200` для `/`, `/stories?period=30d`, `/country/ES`, `/radar`, `/api/v2/countries/ES/entities?days=30`, `/api/v2/radar?limit=25`.

В браузере подтвердить:

- у сюжетов видны две текущие карточки, а не прозрачная секция;
- страница страны переходит от loader к досье;
- радар либо показывает компактные доказательные карточки, либо честное пустое состояние;
- в списке нет `economy:trade:russia`, `media:coverage`, стрелочной цепочки всех стран или `insufficient`.

- [ ] **Step 7: Финальный коммит состояния плана**

Commit: `git commit -am "chore: complete page and radar recovery"` только если чекбоксы плана отмечаются в репозитории; иначе изменений не требуется.
