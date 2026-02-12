# GeoPulse — Операционное руководство

## Архитектура

```
┌─────────────────────────────────────────────────────────┐
│                    VPS-2 (YOUR_SERVER_IP)                  │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│  │ Collector │  │ TG Coll. │  │ VOX Coll.│               │
│  │(web/rss) │  │(telegram)│  │(comments)│               │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘               │
│       │              │              │                     │
│       ▼              ▼              ▼                     │
│  ┌──────────────────────────────────────┐                │
│  │        PostgreSQL + TimescaleDB      │                │
│  │    articles, analysis, threads,      │                │
│  │    comments, comment_analysis,       │                │
│  │    vox_temperature, temperature      │                │
│  └──────────────┬───────────────────────┘                │
│       │         │         │         │                     │
│  ┌────┴───┐ ┌───┴────┐ ┌─┴──────┐ ┌┴─────────┐         │
│  │Analyzer│ │Threads │ │  VOX   │ │Temperature│         │
│  │Gemini3 │ │Builder │ │Analyzer│ │  Engine   │         │
│  │ Flash  │ │        │ │GemFlash│ │           │         │
│  └────┬───┘ └───┬────┘ └─┬──────┘ └┬─────────┘         │
│       │         │         │         │                     │
│       ▼         ▼         ▼         ▼                     │
│  ┌──────────────────────────────────────┐                │
│  │          FastAPI (port 8100)          │                │
│  └──────────────────┬───────────────────┘                │
│                     │                                     │
│  ┌──────────────────┴───────────────────┐                │
│  │     Nginx (443 → massaraksh.tech)    │                │
│  │  /api/ → 127.0.0.1:8100             │                │
│  │  /     → 127.0.0.1:3333 (Next.js)   │                │
│  └──────────────────────────────────────┘                │
│                                                          │
│  ┌──────────┐  ┌──────────┐                              │
│  │ Firecrawl│  │ Next.js  │                              │
│  │ (3002)   │  │ (3333)   │                              │
│  └──────────┘  └──────────┘                              │
└─────────────────────────────────────────────────────────┘
```

## Репозитории

| Repo | Branch | Что | Путь на VPS |
|------|--------|-----|-------------|
| `milkmike/GEO_PULSE` | `main` | Backend (Python/FastAPI) | `/opt/cis-thermometer/` |
| `milkmike/GEO_PULSE` | `nextjs` | Frontend (Next.js) | `/root/geo-pulse-next/` |
| `milkmike/GEO-LAB` | `main` | Ontology Lab прототип | — |

## Доступ

- **VPS-2**: `ssh -i ~/.ssh/graf_vlasti root@YOUR_SERVER_IP`
- **Домен**: https://massaraksh.tech (фронт), https://lab.massaraksh.tech (лаб)
- **API**: https://massaraksh.tech/api/v1/
- **SSL**: Let's Encrypt, expires 2026-05-13

## Docker-сервисы

```bash
# Посмотреть все контейнеры
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# Ключевые сервисы:
# cis-thermometer-api-1         — FastAPI backend (8100)
# cis-thermometer-collector-1   — Web/RSS сборщик (каждые 30 мин)
# cis-thermometer-tg-collector-1— Telegram сборщик
# cis-thermometer-analyzer-1    — LLM анализ статей (Gemini 3 Flash)
# cis-thermometer-threads-1     — Построитель сюжетных нитей
# cis-thermometer-temperature-1 — Расчёт медийной температуры
# cis-thermometer-vox-collector-1— VOX: сбор комментов из TG
# cis-thermometer-vox-analyzer-1 — VOX: LLM анализ комментов (Gemini Flash)
# cis-thermometer-vox-engine-1   — VOX: расчёт народной температуры
# geo-pulse-next                 — Next.js фронтенд (3333)
```

---

## Деплой фронтенда

```bash
# С локальной машины (автоматический):
ssh -i ~/.ssh/graf_vlasti root@YOUR_SERVER_IP 'bash /root/geo-pulse-next/deploy.sh'

# Или вручную на VPS:
cd /root/geo-pulse-next
git pull origin nextjs
docker build --no-cache --build-arg API_URL="" -t geo-pulse-next:massaraksh .
docker stop geo-pulse-next && docker rm geo-pulse-next
docker run -d --name geo-pulse-next -p 3333:3000 --restart unless-stopped geo-pulse-next:massaraksh
```

**⚠️ ВАЖНО**: `API_URL=""` (пустая строка!) — клиент использует относительные пути, nginx проксирует `/api/` → backend.

## Деплой бэкенда

```bash
cd /opt/cis-thermometer

# Пересобрать конкретный сервис:
docker compose build <service>
docker compose up -d <service>

# Пересобрать всё:
docker compose build
docker compose up -d

# Примеры:
docker compose build analyzer && docker compose up -d analyzer
docker compose build collector && docker compose up -d collector
```

---

## Модели LLM (через OpenRouter)

| Сервис | Модель | Цена $/M | Файл |
|--------|--------|----------|------|
| Article Analyzer | `google/gemini-3-flash` | $1→$4 | `src/pipeline/sentiment.py` |
| VOX Analyzer | `google/gemini-2.0-flash-001` | ~$0.01 | env `VOX_MODEL` |
| Ранее | `anthropic/claude-sonnet-4` | $3→$15 | — |

**Сменить модель analyzer:**
```bash
# Редактируем
vi /opt/cis-thermometer/src/pipeline/sentiment.py
# Строка: MODEL = "google/gemini-3-flash"

# Пересобираем
docker compose build analyzer && docker compose up -d analyzer
```

---

## Источники данных

### Добавить новый web/rss источник

```sql
-- Через psql:
docker exec -it cis-thermometer-db-1 psql -U thermo -d cis_thermometer

INSERT INTO sources (name, url, country_code, source_type, weight, language, active)
VALUES ('Имя источника', 'https://example.com', 'KZ', 'web', 1.0, 'ru', true);
```

**source_type**: `web` (scraping), `rss` (RSS feed), `telegram` (TG канал)

### Добавить Telegram канал

```sql
INSERT INTO vox_channels (platform, channel_username, country_code, name, active)
VALUES ('telegram', 'channel_username', 'KZ', 'Название канала', true);
```

### Google News RSS (формат URL)

```
https://news.google.com/rss/search?q=<запрос_urlencoded>&hl=ru&gl=RU&ceid=RU:ru
```

Пример: `q=%D0%9A%D0%B0%D0%B7%D0%B0%D1%85%D1%81%D1%82%D0%B0%D0%BD` = "Казахстан"

### Текущая статистика

```bash
docker exec cis-thermometer-db-1 psql -U thermo -d cis_thermometer -c "
SELECT country_code,
  COUNT(*) FILTER (WHERE source_type='web') as web,
  COUNT(*) FILTER (WHERE source_type='telegram') as tg,
  COUNT(*) FILTER (WHERE source_type='rss') as rss,
  COUNT(*) as total
FROM sources WHERE active = true
GROUP BY country_code ORDER BY total DESC;"
```

---

## Firecrawl (JS-сайты)

Firecrawl — fallback для сайтов где trafilatura не может извлечь контент (JS-rendered, SPA).

- **URL из collector**: `http://172.18.0.1:3002`
- **Ключ**: `fc-test`
- **Логика**: trafilatura → 0 статей → автоматически Firecrawl
- **Логи**: `docker logs cis-thermometer-collector-1 | grep Firecrawl`

```bash
# Тест Firecrawl вручную:
curl -s http://localhost:3002/v1/scrape \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer fc-test" \
  -d '{"url":"https://example.com","formats":["markdown"]}' | python3 -m json.tool
```

---

## Backfill защита

Статьи старше **48 часов** по `published_at` маркируются `is_backfill=true`:
- Не влияют на температуру (engine фильтрует)
- Не показываются как "свежие" в API
- Остаются в БД для исторического анализа

```bash
# Настройка порога (ENV):
BACKFILL_HOURS=48  # в docker-compose.yml для collector

# Проверить backfill статистику:
docker exec cis-thermometer-db-1 psql -U thermo -d cis_thermometer -c "
SELECT is_backfill, COUNT(*) FROM articles GROUP BY is_backfill;"
```

---

## Мониторинг

### Логи сервисов
```bash
docker logs cis-thermometer-collector-1 --tail 20    # Сбор статей
docker logs cis-thermometer-analyzer-1 --tail 20     # LLM анализ
docker logs cis-thermometer-threads-1 --tail 20      # Сюжетные нити
docker logs cis-thermometer-vox-collector-1 --tail 20 # VOX комментарии
docker logs cis-thermometer-vox-engine-1 --tail 20   # Народная температура
```

### Полезные SQL-запросы
```sql
-- Статьи за 24 часа по странам
SELECT s.country_code, COUNT(*) as articles_24h
FROM articles a JOIN sources s ON s.id = a.source_id
WHERE a.published_at > NOW() - INTERVAL '24h' AND a.is_backfill = false
GROUP BY s.country_code ORDER BY articles_24h DESC;

-- Сломанные источники (0 статей за неделю)
SELECT s.country_code, s.name, s.url, s.source_type
FROM sources s
WHERE s.active = true
  AND (SELECT COUNT(*) FROM articles a WHERE a.source_id = s.id 
       AND a.published_at > NOW() - INTERVAL '7d') = 0;

-- Модели анализа
SELECT model_used, COUNT(*) as cnt, MAX(analyzed_at) as last
FROM analysis GROUP BY model_used ORDER BY cnt DESC;

-- VOX температура
SELECT * FROM vox_temperature ORDER BY time DESC LIMIT 10;

-- Комментарии по странам
SELECT c.country_code, COUNT(*) as comments
FROM comments c GROUP BY c.country_code ORDER BY comments DESC;
```

### Здоровье API
```bash
curl -s https://massaraksh.tech/api/v1/countries | python3 -m json.tool | head -5
curl -s https://massaraksh.tech/api/v1/threads?limit=1 | python3 -m json.tool | head -5
curl -s https://massaraksh.tech/api/v1/vox?days=999 | python3 -m json.tool | head -10
```

---

## Nginx конфигурация

```bash
# Файл: /etc/nginx/sites-available/massaraksh
# Редактировать:
vi /etc/nginx/sites-available/massaraksh
nginx -t && systemctl reload nginx
```

Ключевые location:
- `/` → `127.0.0.1:3333` (Next.js фронт)
- `/api/` → `127.0.0.1:8100` (FastAPI бэкенд)
- SSL: `/etc/letsencrypt/live/massaraksh.tech/`

---

## Частые проблемы

| Проблема | Причина | Решение |
|----------|---------|---------|
| Фронт показывает пустоту | API контейнер рестартовался | Перезагрузить страницу (Ctrl+Shift+R) |
| `NEXT_PUBLIC_*` не работает | Build-time переменные | Пересобрать Docker image |
| Mixed content (HTTPS→HTTP) | API URL захардкожен как HTTP | Использовать `API_URL=""` + nginx proxy |
| Источник даёт 0 статей | JS-heavy сайт или CAPTCHA | Проверить логи, Firecrawl попробует автоматически |
| VOX показывает нули | Нет комментов за период | Увеличить `days` параметр |
| Temperature 0 для страны | Мало проанализированных статей | Подождать цикл анализа |

---

## БД подключение

```bash
# psql в контейнере:
docker exec -it cis-thermometer-db-1 psql -U thermo -d cis_thermometer

# Извне:
psql "postgresql://thermo:REDACTED_DB_PASSWORD@YOUR_SERVER_IP:5432/cis_thermometer"
```

## Бэкап

```bash
# Дамп БД:
docker exec cis-thermometer-db-1 pg_dump -U thermo cis_thermometer > backup_$(date +%Y%m%d).sql

# Git бэкенд:
cd /opt/cis-thermometer && git add -A && git commit -m "backup" && git push

# Git фронт:
cd /root/geo-pulse-next && git add -A && git commit -m "backup" && git push
```
