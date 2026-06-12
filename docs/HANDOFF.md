# GEO PULSE — операционная памятка (бывший handoff)

> Хендофф из облачной сессии в локальную **завершён 2026-06-12**: локальный
> Claude Code ходит на прод по SSH сам, автодеплой включён. Документ переписан
> из «что осталось сделать» в «как это устроено сейчас».
> Паролей и ключей в файле нет.

---

## 1. Что это и где оно живёт

- **Проект:** платформа мониторинга отношений 99 стран мира с Россией
  (worldmonitor через «российскую призму»). Бэкенд Python/FastAPI +
  PostgreSQL(TimescaleDB) + Redis, фронтенд Next.js, всё в docker compose.
- **Репозиторий:** https://github.com/milkmike/GEO_PULSE — единственная рабочая
  ветка `main`. Ветка `nextjs` — отдельная история старого фронтенда.
- **Прод:** публичный URL **https://massaraksh.tech** (nginx → FastAPI :8100
  и Next.js :3334, HTTPS через certbot). Каталог на сервере: `/opt/geopulse`.
  IP не публикуем — доступ через SSH-алиас `geopulse-prod` из
  `~/.ssh/config` оператора (этот файл не в репозитории).
- **Старый сервер** — тёплый бэкап (writers остановлены, db/api крутятся,
  volume не трогать). SSH-алиас `kach-vps` в конфиге оператора.

## 2. Как устроен деплой (с 2026-06-12)

Два равноправных пути:

1. **Автодеплой (основной):** пуш в `main` → cron на сервере (`*/5 * * * *
   /opt/geopulse/deploy/auto-update.sh`) делает `git pull --ff-only` +
   `docker compose up -d --build`. Лог: `/var/log/geopulse-deploy.log`
   (пишутся только DEPLOYED/SKIP, no-op молчит).
2. **Прямой SSH:** локальный Claude Code / оператор заходит `ssh geopulse-prod`
   (алиас в `~/.ssh/config`, ключ `~/.ssh/geopulse_prod_ed25519`, беспарольный).

Правила, чтобы автодеплой не ломался:
- рабочее дерево на сервере держать **чистым** — никаких правок отслеживаемых
  файлов руками. Хотфикс на сервере → сразу коммить в `main` (как это было с
  `session.flush()` в `collect.py`, бэкап того эпизода лежит в `git stash`);
- серверные настройки только в `/opt/geopulse/.env` (gitignored). Доступные
  ключи: `GDELT_PAUSE_SEC`, `ADMIN_API_KEY`, `CORS_ORIGINS`, `LLM_MODELS`,
  `OLLAMA_URL`;
- `docker-compose.override.yml` на сервере — намеренный (пин образа
  TimescaleDB + shm_size 2gb), он untracked и pull'у не мешает.

## 3. Поднять локально (для разработки)

```bash
git clone https://github.com/milkmike/GEO_PULSE.git
cd GEO_PULSE
cp .env.example .env       # заполнить OPENROUTER_API_KEY, JINA_API_KEY, DB_PASSWORD
docker compose up -d --build db redis api web ru-index signals gdelt-collector fx-collector
# дашборд: http://localhost:3334 · API: http://localhost:8100/api/v2/countries
```
Тесты без прод-БД: локальный Postgres (initdb в /tmp) + `data/init.sql` +
`data/002_threads.sql`, фикстуры, FastAPI TestClient (перед импортом app
заглушить feedparser: `sys.modules['feedparser']=types.ModuleType('feedparser')`).
Детали и грабли — в `CLAUDE.md`.

## 4. Журнал: что было сделано 2026-06-12

- ✅ **Admin/запись закрыты** (коммит `ebe370b` задеплоен): `/api/v1/admin/*`
  и POST-эндпоинты отдают 403 без `X-Admin-Key`. Чтобы открыть admin себе —
  задать `ADMIN_API_KEY=<секрет>` в `.env` и слать заголовок.
- ✅ **Брифинги починены**: причиной 404 был отозванный ключ OpenRouter
  (все LLM-вызовы падали 401). Ключ заменён в `.env`, analyzer/briefs
  пересозданы, `/api/v2/brief` отдаёт 200.
- ✅ **GDELT-пауза** управляется из `.env`: `GDELT_PAUSE_SEC=40`. На старте
  прохода изредка бывают 429 — бэкофф и circuit-breaker справляются; если
  зашумит, поднять значение до 60 и пересоздать gdelt-collector.
- ✅ **Автодеплой включён** (cron, см. §2).
- ✅ Хотфикс `collect.py` (session.flush) закоммичен в `main` (`94155b0`).
- ✅ Ветка-дубликат `claude/cool-allen-flyikw` удалена из GitHub.

### 4.1 Предзапуск (вечер 2026-06-12) — большой блок
- ✅ **Ребренд → «Массаракш»** (UI, тайтлы, промпты брифов; репозиторий/код
  остаются `GEO_PULSE`).
- ✅ **Продуктовые страницы:** сноски-цитаты `[n]` в брифах, «Новости дня»
  (диверсификация по странам, UA демотирована), `/sources` (матрица языков,
  Redis-кеш — 6.9с → 0.5с), `/about` (журнальный лонгрид в тоне Стругацких),
  `/analytics` (think tanks, период до 2 лет), тематическая линза фильтрует
  карту+новости+брифинг.
- ✅ **Данные досье:** ООН (98 стран, Voeten), торговля (97 стран, IMF IMTS —
  легаси API МВФ умер, мигрировали на api.imf.org), договоры из analysis;
  новые сервисы `un-votes-loader`, `trade-loader`.
- ✅ **Аналитический слой:** тир `analytics` (24 фида), флаги `state_affiliated`/
  `propaganda_risk`, архивный бэкфилл — 4762 статьи, глубина до 1994.
- ✅ **Сигналы:** детектор `notable_event` (значимые события AL≥4 с якорем на РФ),
  устойчивость к бэкфиллу (исключение `is_backfill`), сортировка по важности,
  index_shift с потолком ±18 против артефактов.
- ✅ **Дизайн:** редактура (Piazzolla/Golos/JetBrains Mono, триколор), башня-
  логотип с анимацией, микроанимации (Motion), ☢-пасхалка на карте.
- ✅ **Брифы:** оптика «российских интересов, но честно».
- ✅ **Инцидент OOM (см. §5):** фронт-билд на свопless-сервере заморозил машину;
  закрыто свапом 4ГБ + потолком памяти node-билда + `nice/ionice` в auto-update.

## 5. Открытые задачи

- ⚪ **Прунинг мёртвых источников** (health DEGRADED, ~50% покрытия): когда
  накопится ~неделя наблюдений после 2026-06-12, выключить замолчавшие >30 дней:
  ```sql
  UPDATE sources SET active=false WHERE active AND id IN (
    SELECT source_id FROM articles GROUP BY source_id
    HAVING MAX(published_at) < NOW() - INTERVAL '30 days');
  ```
  (через `docker compose exec -T db psql -U thermo -d cis_thermometer -c "…"`)
- 🔒 **Сменить пароль БД** на проде: `DB_PASSWORD` + строка в `DATABASE_URL`
  в `/opt/geopulse/.env`, затем `docker compose up -d db api` (старый пароль
  был в истории git до чистки).
- 🔒 Сменить SSH-пароли обоих серверов, если ещё не сменены (вход по ключам
  от паролей не зависит).
- ⚪ **Долг по дизайну деплоя:** фронт-образ собирается на проде. Перенести
  сборку в GitHub Actions → registry, сервер только тянет готовое (фаза 5
  роадмапа). До этого защищает свап + потолок памяти + nice.
- Дальше по продукту — `docs/ROADMAP_WORLDMONITOR.md` (фазы 2–4 в основном
  закрыты; фаза 5 — слои worldmonitor: финрадар, энергетика, санкции, ИИ).

## 6. Карта проекта (быстрый старт по коду)

- `src/countries.py` — реестр 99 стран; `src/engine/ru_index.py` — индекс RRI;
  `src/engine/signals.py` — детекторы; `src/engine/health.py` — свежесть.
- `src/llm.py` — цепочка моделей + кеш; `src/pipeline/` — промпт v2.0, темы,
  sentiment, брифинги; `src/entities.py` — 43 сущности РФ-орбиты.
- `src/api/routes/world.py` — API v2; `web/` — Next.js дашборд;
  `src/api/static/world.html` — встроенный мини-дашборд.
- `scripts/collect_gdelt.py`, `calc_ru_index.py`, `detect_signals.py`,
  `generate_briefs.py`, `collect_fx.py` — воркеры.
- `deploy/deploy.sh` — деплой с нуля; `deploy/auto-update.sh` — автодеплой;
  `deploy/nginx/geopulse.conf.example` — безопасный nginx.
- `CLAUDE.md` — конвенции и грабли (читать первым).
