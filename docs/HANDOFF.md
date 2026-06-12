# GEO PULSE — продолжение работы локально (handoff)

Этот документ переносит контекст из облачной сессии Claude в локальную среду,
где у тебя (или у Claude Code на твоей машине) **есть SSH-доступ к серверу** —
то, чего облачная песочница лишена (она режет исходящий порт 22).

> В этом файле НЕТ паролей и ключей. Места, где они нужны, помечены `<…>`.

---

## 1. Что это и где оно живёт

- **Проект:** платформа мониторинга отношений 99 стран мира с Россией
  (worldmonitor через «российскую призму»). Бэкенд Python/FastAPI +
  PostgreSQL(TimescaleDB) + Redis, фронтенд Next.js, всё в docker compose.
- **Репозиторий:** https://github.com/milkmike/GEO_PULSE — рабочая ветка `main`
  (канонична). Ветка `nextjs` — отдельная история старого фронтенда. Ветка
  `claude/cool-allen-flyikw` — дубликат main, можно удалить в GitHub UI.
- **Прод:** сервер `5.42.122.100`, публичный URL **https://massaraksh.tech**
  (nginx → FastAPI :8100 и Next.js :3334, HTTPS через certbot).
- **Состояние прода:** работает, данные пишутся (~400 статей/час), БД ~490k
  статей перенесена со старого сервера `212.67.17.32` (он оставлен тёплым
  бэкапом: writers остановлены, db/api ещё крутятся, volume не трогать).

---

## 2. Состояние git на момент хендоффа

Последний коммит `main`: **9369075** `feat(deploy): pull-based auto-update + env-driven server config`

Ключевые недавние коммиты:
- `9369075` авто-деплой `deploy/auto-update.sh` + серверные настройки через `.env`
- `ebe370b` **защита admin/записи в API** (fail-closed, см. §4.1)
- `463476d` эталонный безопасный nginx-конфиг `deploy/nginx/geopulse.conf.example`
- `fb44776` фикс GDELT 429 (пейсинг 5с, бэкофф, circuit-breaker)
- `b61bdce` чистка секретов из всей истории перед публикацией

⚠️ **ВАЖНО: коммиты `fb44776`, `463476d`, `ebe370b`, `9369075` НЕ применены на
прод-сервере.** Прод крутит код по состоянию до них (примерно `1d25cc2`/v2-релиз).
Чтобы применить — см. §4.

---

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

---

## 4. Что осталось доделать на ПРОДЕ (по приоритету)

Для всего ниже нужен SSH: `ssh root@5.42.122.100` (пароль `<NEW_SERVER_PASSWORD>`).
Каталог проекта на сервере: `/opt/geopulse`.

### 4.1 🔴 Закрыть admin/запись (код уже готов, надо применить)
API публичный и без аутентификации — сейчас наружу открыты `/api/v1/admin/*`
(метаданные ключей, расходы) и методы записи. Коммит `ebe370b` добавил
fail-closed-защиту: без `ADMIN_API_KEY` admin и запись отдают 403 всем.
Применить:
```bash
cd /opt/geopulse && git pull origin main && docker compose up -d --build api
# проверка снаружи (ждём 403):
curl -s -o /dev/null -w "%{http_code}\n" https://massaraksh.tech/api/v1/admin/summary
```
Если когда-то понадобится доступ к admin — задать `ADMIN_API_KEY=<секрет>` в
`/opt/geopulse/.env` и слать заголовок `X-Admin-Key: <секрет>`.

### 4.2 🟠 AI-брифинги не отдаются (`/api/v2/brief` → 404)
Либо сервис `briefs` не отработал, либо пуст `OPENROUTER_API_KEY`.
```bash
cd /opt/geopulse
grep -q '^OPENROUTER_API_KEY=.' .env && echo "ключ есть" || echo "КЛЮЧ ПУСТ"
docker compose logs briefs --tail 30
docker compose run --rm briefs python scripts/generate_briefs.py   # разовый прогон
```

### 4.3 🟡 GDELT ловит 429 (пауза стоит 90с — это перебор)
После применения `main` пауза управляется из `.env` (коммит `9369075`):
```bash
# в /opt/geopulse/.env:
GDELT_PAUSE_SEC=40
# и убрать ручную правку GDELT_PAUSE_SEC=90 из docker-compose.yml (вернуть файл к репо):
git checkout docker-compose.yml
docker compose up -d gdelt-collector
```

### 4.4 ⚪ Health DEGRADED — почистить мёртвые источники (через ~неделю)
196/403 источников OK — это ~150 экспериментальных мировых RSS, часть мёртвая.
Безопасный прунинг (трогает только те, что когда-то работали и замолчали >30 дн):
```sql
UPDATE sources SET active=false WHERE active AND id IN (
  SELECT source_id FROM articles GROUP BY source_id
  HAVING MAX(published_at) < NOW() - INTERVAL '30 days');
```
(через `docker compose exec -T db psql -U thermo -d cis_thermometer -c "…"`)

---

## 5. Автономный деплой (опционально — «я пушу, прод сам обновляется»)

Коммит `9369075` добавил `deploy/auto-update.sh`: по таймеру делает
`git pull --ff-only` + `docker compose up -d --build`, если `main` сдвинулся.
Одноразовая настройка на сервере:
```bash
crontab -e
# добавить строку:
*/5 * * * * /opt/geopulse/deploy/auto-update.sh
```
Условие работы: рабочее дерево на сервере должно быть **чистым** (без правок
отслеживаемых файлов). Серверные настройки держать только в `.env` (он
gitignored). Доступные ключи `.env`: `GDELT_PAUSE_SEC`, `ADMIN_API_KEY`,
`CORS_ORIGINS`, `LLM_MODELS`, `OLLAMA_URL`. Поэтому перед включением крона
верни `docker-compose.yml` к репо (`git checkout docker-compose.yml`), а паузу
GDELT перенеси в `.env` (см. §4.3).

---

## 6. Безопасность — обязательные действия оператора

1. **Сменить SSH-пароли** обоих серверов (`5.42.122.100` и `212.67.17.32`) —
   они засветились в переписке.
2. **Сменить пароль БД** на проде: `DB_PASSWORD` + строка в `DATABASE_URL` в
   `/opt/geopulse/.env`, затем `docker compose up -d db api` (старый пароль
   `oIa-…` был в истории git, вычищен, но мог сохраниться в ref'ах PR).
3. Применить §4.1 (закрыть admin) — самое важное после публикации.
4. Удалить ветку `claude/cool-allen-flyikw` в GitHub UI.
5. Telegram-сессию завершать НЕ нужно (репо был приватным, доступ только у
   владельца — утечки не было).

---

## 7. Доступы, которые понадобятся (значения — у оператора, НЕ в этом файле)

| Что | Где использовать |
|-----|------------------|
| SSH-пароль `5.42.122.100` | вход на прод-сервер |
| SSH-пароль `212.67.17.32` | старый сервер (бэкап) |
| `OPENROUTER_API_KEY` | LLM-анализ и брифинги (в `.env`, перенесён со старого) |
| `JINA_API_KEY` | эмбеддинги/сюжеты (в `.env`) |
| `TELEGRAM_API_ID/HASH` + `sessions/` | tg-коллекторы (в `.env` + файлы сессий) |
| GitHub-доступ к `milkmike/GEO_PULSE` | push в main (репо приватный — нужен токен, либо сделать публичным) |

---

## 8. Карта проекта (быстрый старт по коду)

- `src/countries.py` — реестр 99 стран; `src/engine/ru_index.py` — индекс RRI;
  `src/engine/signals.py` — 7 детекторов; `src/engine/health.py` — свежесть.
- `src/llm.py` — цепочка моделей + кеш; `src/pipeline/` — промпт v2.0, темы,
  sentiment, брифинги; `src/entities.py` — 43 сущности РФ-орбиты.
- `src/api/routes/world.py` — API v2; `web/` — Next.js дашборд;
  `src/api/static/world.html` — встроенный мини-дашборд.
- `scripts/collect_gdelt.py`, `calc_ru_index.py`, `detect_signals.py`,
  `generate_briefs.py`, `collect_fx.py` — воркеры.
- `deploy/deploy.sh` — деплой с нуля; `deploy/nginx/geopulse.conf.example` —
  безопасный nginx; `deploy/auto-update.sh` — авто-деплой.
- `CLAUDE.md` — конвенции и грабли (читать первым).
- `docs/ROADMAP_WORLDMONITOR.md` — матрица паритета и дальнейшие фазы.

---

## 9. Как продолжить эту сессию с Claude Code локально

1. Установи Claude Code на машину с SSH-доступом к серверу.
2. `git clone` репозитория, открой его в Claude Code (он подхватит `CLAUDE.md`).
3. Дай стартовый промпт примерно так:

   > Продолжаем GEO PULSE. Прочти docs/HANDOFF.md и CLAUDE.md. Прод на
   > https://massaraksh.tech (сервер 5.42.122.100, SSH-доступ у меня есть).
   > Незакрытые задачи — в §4 HANDOFF. Начни с §4.1 (закрыть admin-эндпоинты):
   > покажи команды, я подтвержу, потом применим по SSH.

Локальный Claude Code сможет САМ ходить по SSH (в отличие от облачной сессии),
так что деплой и проверку прода он выполнит без посредника-агента.
